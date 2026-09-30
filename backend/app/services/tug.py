"""拖轮调度业务规则：拖轮台账、派工单、冲突仲裁与调度看板。

派工口径（需求马力/条数、作业时段）统一收在 ``tug_policy``，本服务只负责编排，
保证拖轮台账、派工单、调度看板读到的马力需求是同一份落库数值。
"""
from __future__ import annotations

import threading
from typing import Any

from app.services import tug_policy
from app.services.tug_policy import VESSEL_TYPES, WATERS, requirements
from app.store import store

TUG_TABLE = "tug"
ORDER_TABLE = "tug_order"
POLICY_VERSION = "2026-09"

TUG_REQUIRED_FIELDS = ["拖轮编号", "船名", "额定马力", "水域"]
TUG_STATUSES = ["待命", "作业中", "检修中"]
ORDER_STATUSES = ["已派工", "作业中", "已完成", "已取消"]
ACTIVE_ORDER_STATUSES = ("已派工", "作业中")
TERMINAL_ORDER_STATUSES = ("已完成", "已取消")

DISPATCH_REQUIRED = ["作业号", "船名", "船型", "水域", "拖带作业量", "作业开始", "作业结束"]


class TugService:
    def __init__(self) -> None:
        # 派工涉及多表校验与写入，用一把锁把“检查占用—落库”串行化，
        # 两个人同时抢同一条拖轮时，后一次一定能看到先落库的那份派工单。
        self._lock = threading.RLock()
        self._seq = 0

    # ------------------------------------------------------------------ 台账
    def list_tugs(
        self,
        *,
        keyword: str | None = None,
        water: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            rows = [dict(row) for row in store.rows(TUG_TABLE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("拖轮编号", "")) or keyword in str(row.get("船名", ""))]
        if water:
            rows = [row for row in rows if row.get("水域") == water]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        for row in rows:
            self._decorate_tug(row)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_tug(self, tug_id: int) -> dict[str, Any] | None:
        row = store.find(TUG_TABLE, tug_id)
        if row is None:
            return None
        result = dict(row)
        self._decorate_tug(result)
        return result

    def create_tug(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        missing = [field for field in TUG_REQUIRED_FIELDS if str(values.get(field) or "").strip() == ""]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        water = str(values["水域"]).strip()
        if water not in WATERS:
            return None, f"水域「{water}」不在配置范围内，可选：{'、'.join(WATERS)}"
        try:
            hp = int(float(values["额定马力"]))
        except (TypeError, ValueError):
            return None, "额定马力必须是整数（马力）"
        if hp <= 0:
            return None, "额定马力必须大于 0"
        code = str(values["拖轮编号"]).strip()
        with self._lock:
            rows = store.rows(TUG_TABLE)
            if any(row.get("拖轮编号") == code for row in rows):
                return None, f"拖轮编号「{code}」已登记，请勿重复建档"
            entry = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "拖轮编号": code,
                "船名": str(values["船名"]).strip(),
                "额定马力": hp,
                "水域": water,
                "status": "待命",
                "pending": False,
                "abnormal": False,
            }
            rows.append(entry)
            return dict(entry), ""

    def run_tug_action(self, tug_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with self._lock:
            tug = store.find(TUG_TABLE, tug_id)
            if tug is None:
                return None, f"拖轮 {tug_id} 不存在或已归档"
            if action == "标记检修":
                active = self._active_order_for(tug["拖轮编号"])
                if active is not None:
                    return None, f"拖轮「{tug['拖轮编号']}」正在执行作业「{active['作业号']}」，完成或取消后才能检修"
                tug["status"] = "检修中"
            elif action == "解除检修":
                if tug["status"] != "检修中":
                    return None, f"拖轮当前为「{tug['status']}」，无需解除检修"
                tug["status"] = "待命"
            else:
                return None, f"动作「{action}」不属于拖轮台账可执行范围"
            result = dict(tug)
            self._decorate_tug(result)
            return result, f"拖轮已{action}"

    def _decorate_tug(self, row: dict[str, Any]) -> None:
        # 台账上看到的马力需求只取在途派工单（已完成/已取消的不再占用拖轮），三处口径一致。
        active = self._active_order_for(row.get("拖轮编号", ""))
        row["作业号"] = active["作业号"] if active else ""
        row["派工单号"] = active["派工单号"] if active else ""
        row["需求马力"] = int(active["需求马力"]) if active else 0

    # ------------------------------------------------------------------ 派工单
    def list_orders(
        self,
        *,
        keyword: str | None = None,
        water: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            rows = [dict(row) for row in store.rows(ORDER_TABLE)]
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("作业号", ""))
                or keyword in str(row.get("派工单号", ""))
                or keyword in str(row.get("船名", ""))
            ]
        if water:
            rows = [row for row in rows if row.get("水域") == water]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        rows.sort(key=lambda row: int(row.get("updated_seq", 0)), reverse=True)
        for row in rows:
            self._decorate_order(row)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        row = store.find(ORDER_TABLE, order_id)
        if row is None:
            return None
        result = dict(row)
        self._decorate_order(result)
        return result

    def run_order_action(self, order_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with self._lock:
            order = store.find(ORDER_TABLE, order_id)
            if order is None:
                return None, f"派工单 {order_id} 不存在或已归档"
            if action == "开始作业":
                if order["status"] != "已派工":
                    return None, f"派工单当前为「{order['status']}」，不能开始作业"
                order["status"] = "作业中"
            elif action == "完成作业":
                if order["status"] not in ACTIVE_ORDER_STATUSES:
                    return None, f"派工单当前为「{order['status']}」，不能完成"
                order["status"] = "已完成"
                order["pending"] = False
                self._release_tugs(order)
            elif action == "取消派工":
                if order["status"] not in ACTIVE_ORDER_STATUSES:
                    return None, f"派工单当前为「{order['status']}」，不能取消"
                order["status"] = "已取消"
                order["pending"] = False
                self._release_tugs(order)
            else:
                return None, f"动作「{action}」不属于派工单可执行范围"
            order["updated_seq"] = self._next_seq()
            result = dict(order)
            self._decorate_order(result)
            return result, f"派工单已{action}"

    def _decorate_order(self, row: dict[str, Any]) -> None:
        details = []
        assigned_hp = 0
        for code in row.get("拖轮编号列表", []):
            tug = self._find_tug_by_code(code)
            hp = int(tug["额定马力"]) if tug else 0
            assigned_hp += hp
            details.append({"拖轮编号": code, "船名": tug["船名"] if tug else "（已删除）", "额定马力": hp})
        row["拖轮明细"] = details
        row["派出马力"] = assigned_hp
        row["马力缺口"] = max(0, int(row["需求马力"]) - assigned_hp)

    # ------------------------------------------------------------------ 派工
    def preview_dispatch(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """试算：不算库，只按同一套口径回显需求、推荐拖轮与各种拦截/提示。"""
        parsed, message = self._parse_dispatch(values)
        if parsed is None:
            return None, message
        with self._lock:
            plan = self._build_plan(parsed)
        return plan, ""

    def dispatch(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """正式派工：校验口径、仲裁占用，随后原子落库。"""
        parsed, message = self._parse_dispatch(values)
        if parsed is None:
            return None, message
        with self._lock:
            plan = self._build_plan(parsed)
            if not plan["can_dispatch"]:
                return None, "；".join(plan["blockers"])
            selected = plan["selected_tugs"]
            if not selected:
                return None, "没有可派出的拖轮"

            existing = self._find_active_order_by_job(parsed["作业号"])
            seq = self._next_seq()
            if existing is None:
                order = {
                    "id": max((int(row.get("id", 0)) for row in store.rows(ORDER_TABLE)), default=0) + 1,
                    "派工单号": self._next_order_no(),
                    "作业号": parsed["作业号"],
                    "version": 1,
                    "created_seq": seq,
                }
                store.rows(ORDER_TABLE).append(order)
                verb = "已派工"
            else:
                # 同一作业重复登记：原单升版，只留最新一版，不新开单。
                order = existing
                order["version"] = int(order["version"]) + 1
                self._release_tugs(order)
                verb = "派工单已更新为最新版本"

            order.update({
                "船名": parsed["船名"],
                "船型": parsed["船型"],
                "水域": parsed["水域"],
                "拖带作业量": parsed["workload"],
                "作业开始": parsed["start_at"].strftime("%Y-%m-%d %H:%M"),
                "作业结束": parsed["end_at"].strftime("%Y-%m-%d %H:%M"),
                "需求马力": plan["required_hp"],
                "需求条数": plan["required_count"],
                "拖轮编号列表": [tug["拖轮编号"] for tug in selected],
                "窗口越界": not plan["within_window"],
                "窗口提示": plan["window_warning"],
                "标准版本": POLICY_VERSION,
                "status": "已派工",
                "pending": True,
                "abnormal": not plan["within_window"],
                "updated_seq": seq,
            })
            for tug in selected:
                tug["status"] = "作业中"
            result = dict(order)
            self._decorate_order(result)
            result["warnings"] = [plan["window_warning"]] if plan["window_warning"] else []
            suffix = f"；{plan['window_warning']}" if plan["window_warning"] else ""
            return result, f"{verb}：需求 {plan['required_hp']} 马力，派出 {result['派出马力']} 马力、{len(selected)} 条{suffix}"

    def _parse_dispatch(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        missing = [field for field in DISPATCH_REQUIRED if str(values.get(field) or "").strip() == ""]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        water = str(values["水域"]).strip()
        vessel_type = str(values["船型"]).strip()
        if water not in WATERS:
            return None, f"未知水域「{water}」，可选：{'、'.join(WATERS)}"
        if vessel_type not in VESSEL_TYPES:
            return None, f"未知船型「{vessel_type}」，可选：{'、'.join(VESSEL_TYPES)}"
        try:
            workload = float(values["拖带作业量"])
        except (TypeError, ValueError):
            return None, "拖带作业量必须是数字（吨）"
        if workload <= 0:
            return None, "拖带作业量必须大于 0"
        try:
            start_at = tug_policy.parse_dt(str(values["作业开始"]))
            end_at = tug_policy.parse_dt(str(values["作业结束"]))
        except ValueError as exc:
            return None, str(exc)
        if end_at <= start_at:
            return None, "作业结束时间必须晚于开始时间"
        requested_codes = values.get("拖轮编号") or []
        if isinstance(requested_codes, str):
            requested_codes = [code.strip() for code in requested_codes.split(",") if code.strip()]
        if not isinstance(requested_codes, list):
            return None, "拖轮编号必须是列表"
        return {
            "作业号": str(values["作业号"]).strip(),
            "船名": str(values["船名"]).strip(),
            "船型": vessel_type,
            "水域": water,
            "workload": workload,
            "start_at": start_at,
            "end_at": end_at,
            "requested_codes": [str(code).strip() for code in requested_codes],
        }, ""

    def _build_plan(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """按口径算需求、挑拖轮、列出拦截项与窗口提示；不写任何数据。"""
        calc = requirements(parsed["水域"], parsed["船型"], parsed["workload"])
        required_hp = calc["required_hp"]
        required_count = calc["required_count"]
        window = tug_policy.check_window(parsed["水域"], parsed["start_at"], parsed["end_at"])

        existing = self._find_active_order_by_job(parsed["作业号"])
        keep_codes = set(existing["拖轮编号列表"]) if existing else set()

        blockers: list[str] = []
        selected: list[dict[str, Any]] = []
        requested = parsed["requested_codes"]

        if requested:
            for code in requested:
                tug = self._find_tug_by_code(code)
                if tug is None:
                    blockers.append(f"拖轮「{code}」不在台账中")
                    continue
                if tug["水域"] != parsed["水域"]:
                    blockers.append(f"拖轮「{code}」归属{tug['水域']}，不能跨水域派到{parsed['水域']}")
                    continue
                if tug["status"] == "检修中":
                    blockers.append(f"拖轮「{code}」检修中，不参与派工")
                    continue
                holder = self._active_order_for(code, exclude_order=existing)
                if holder is not None:
                    # 后一次看到先落库的那份，先到先得。
                    blockers.append(
                        f"拖轮「{code}」已被先落库的派工单「{holder['派工单号']}」占用（作业「{holder['作业号']}」），本次派工冲突"
                    )
                    continue
                selected.append(dict(tug))
            if not blockers:
                selected_hp = sum(int(tug["额定马力"]) for tug in selected)
                if selected_hp < required_hp:
                    blockers.append(f"指定拖轮可用马力 {selected_hp}，低于下限 {required_hp}，差 {required_hp - selected_hp} 马力")
                if len(selected) < required_count:
                    blockers.append(f"指定拖轮 {len(selected)} 条，少于该口径要求的 {required_count} 条，差 {required_count - len(selected)} 条")
        else:
            candidates = [
                dict(tug) for tug in store.rows(TUG_TABLE)
                if tug["水域"] == parsed["水域"]
                and tug["status"] != "检修中"
                and self._active_order_for(tug["拖轮编号"], exclude_order=existing) is None
            ]
            candidates.sort(key=lambda tug: int(tug["额定马力"]), reverse=True)
            available_hp = sum(int(tug["额定马力"]) for tug in candidates)
            total_hp = 0
            for tug in candidates:
                if total_hp >= required_hp and len(selected) >= required_count:
                    break
                selected.append(tug)
                total_hp += int(tug["额定马力"])
            if available_hp < required_hp:
                blockers.append(f"{parsed['水域']}可用马力 {available_hp}，低于下限 {required_hp}，差 {required_hp - available_hp} 马力")
            if len(candidates) < required_count:
                blockers.append(f"{parsed['水域']}可用拖轮 {len(candidates)} 条，少于要求的 {required_count} 条，差 {required_count - len(candidates)} 条")

        selected_hp = sum(int(tug["额定马力"]) for tug in selected)
        return {
            "作业号": parsed["作业号"],
            "水域": parsed["水域"],
            "船型": parsed["船型"],
            "拖带作业量": parsed["workload"],
            "required_hp": required_hp,
            "required_count": required_count,
            "base_hp": calc["base_hp"],
            "hp_per_kt": calc["hp_per_kt"],
            "available_hp": selected_hp,
            "hp_gap": max(0, required_hp - selected_hp),
            "within_window": window["within_window"],
            "allowed_window": window["allowed_window"],
            "window_warning": window["warning"],
            "blockers": blockers,
            "can_dispatch": not blockers,
            "selected_tugs": selected,
            "recommended_codes": [tug["拖轮编号"] for tug in selected],
            "existing_order": (
                {"id": existing["id"], "派工单号": existing["派工单号"], "version": existing["version"]}
                if existing else None
            ),
            "reused_codes": sorted(keep_codes & {tug["拖轮编号"] for tug in selected}),
        }

    # ------------------------------------------------------------------ 看板
    def board(self) -> dict[str, Any]:
        with self._lock:
            tugs = [dict(row) for row in store.rows(TUG_TABLE)]
            orders = [dict(row) for row in store.rows(ORDER_TABLE)]
        for order in orders:
            self._decorate_order(order)
        active_orders = [row for row in orders if row["status"] in ACTIVE_ORDER_STATUSES]

        waters: list[dict[str, Any]] = []
        for water in WATERS:
            water_tugs = [row for row in tugs if row["水域"] == water]
            water_orders = [row for row in active_orders if row["水域"] == water]
            waters.append({
                "水域": water,
                "允许时段": tug_policy.allowed_window_text(water),
                "需求马力合计": sum(int(row["需求马力"]) for row in water_orders),
                "派出马力合计": sum(int(row["派出马力"]) for row in water_orders),
                "在途派工单": len(water_orders),
                "待命拖轮": sum(1 for row in water_tugs if row["status"] == "待命"),
                "作业拖轮": sum(1 for row in water_tugs if row["status"] == "作业中"),
                "检修拖轮": sum(1 for row in water_tugs if row["status"] == "检修中"),
            })
        window_alerts = [
            {
                "派工单号": row["派工单号"],
                "作业号": row["作业号"],
                "船名": row["船名"],
                "水域": row["水域"],
                "窗口提示": row["窗口提示"],
            }
            for row in active_orders
            if row.get("窗口越界")
        ]
        cards = [
            {"label": "在途派工单", "value": len(active_orders)},
            {"label": "在途需求马力", "value": sum(int(row["需求马力"]) for row in active_orders)},
            {"label": "窗口越界提示", "value": len(window_alerts)},
            {"label": "检修中拖轮", "value": sum(1 for row in tugs if row["status"] == "检修中")},
        ]
        return {
            "cards": cards,
            "waters": waters,
            "active_orders": [
                {
                    "派工单号": row["派工单号"],
                    "作业号": row["作业号"],
                    "船名": row["船名"],
                    "船型": row["船型"],
                    "水域": row["水域"],
                    "需求马力": int(row["需求马力"]),
                    "需求条数": int(row["需求条数"]),
                    "派出马力": int(row["派出马力"]),
                    "拖轮编号列表": list(row["拖轮编号列表"]),
                    "状态": row["status"],
                    "版本": int(row["version"]),
                    "窗口提示": row.get("窗口提示", ""),
                }
                for row in sorted(active_orders, key=lambda item: int(item.get("updated_seq", 0)), reverse=True)
            ],
            "window_alerts": window_alerts,
        }

    # ------------------------------------------------------------------ 标准
    def list_standards(self) -> list[dict[str, Any]]:
        return [dict(row) for row in tug_policy.standard_rows()]

    def update_standard(self, water: str, values: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, str]:
        """按水域整体改一套口径；已落库派工单保留当时算定的需求，不回算、不回退。"""
        if water not in WATERS:
            return None, f"未知水域「{water}」，可选：{'、'.join(WATERS)}"
        rows = [row for row in tug_policy.standard_rows() if row["水域"] == water]
        try:
            if values.get("标准拖轮马力") is not None:
                ref_hp = int(float(values["标准拖轮马力"]))
                if ref_hp <= 0:
                    return None, "标准拖轮马力必须大于 0"
            else:
                ref_hp = int(rows[0]["标准拖轮马力"])
            start = str(values.get("允许开始") or rows[0]["允许开始"]).strip()
            end = str(values.get("允许结束") or rows[0]["允许结束"]).strip()
            tug_policy._to_minutes(start)
            tug_policy._to_minutes(end)
            vessel_overrides = values.get("船型") or {}
            if not isinstance(vessel_overrides, dict):
                return None, "船型口径必须按船型逐项提供"
            for row in rows:
                override = vessel_overrides.get(row["船型"], {})
                base_hp = int(float(override.get("基准马力", row["基准马力"])))
                hp_per_kt = int(float(override.get("每千吨马力", row["每千吨马力"])))
                min_tugs = int(float(override.get("最少拖轮数", row["最少拖轮数"])))
                if min(base_hp, hp_per_kt, min_tugs, ref_hp) <= 0:
                    return None, f"{row['船型']}口径数值都必须大于 0"
                row["基准马力"] = base_hp
                row["每千吨马力"] = hp_per_kt
                row["最少拖轮数"] = min_tugs
                row["标准拖轮马力"] = ref_hp
                row["允许开始"] = start
                row["允许结束"] = end
        except (TypeError, ValueError):
            return None, "口径数值格式不正确"
        return [dict(row) for row in rows], f"{water}派工口径已更新，仅对之后的派工生效"

    # ------------------------------------------------------------------ 内部工具
    def _next_seq(self) -> int:
        if self._seq == 0:
            self._seq = max((int(row.get("updated_seq", 0)) for row in store.rows(ORDER_TABLE)), default=0)
        self._seq += 1
        return self._seq

    def _next_order_no(self) -> str:
        serial = max((int(row.get("id", 0)) for row in store.rows(ORDER_TABLE)), default=0) + 1
        return f"TASK-{serial:04d}"

    def _find_tug_by_code(self, code: str) -> dict[str, Any] | None:
        for row in store.rows(TUG_TABLE):
            if row.get("拖轮编号") == code:
                return row
        return None

    def _find_active_order_by_job(self, job_no: str, *, exclude: dict[str, Any] | None = None) -> dict[str, Any] | None:
        for row in store.rows(ORDER_TABLE):
            if row.get("作业号") != job_no or row.get("status") not in ACTIVE_ORDER_STATUSES:
                continue
            if exclude is not None and row is exclude:
                continue
            return row
        return None

    def _active_order_for(self, tug_code: str, *, exclude_order: dict[str, Any] | None = None) -> dict[str, Any] | None:
        for row in store.rows(ORDER_TABLE):
            if row.get("status") not in ACTIVE_ORDER_STATUSES:
                continue
            if exclude_order is not None and row is exclude_order:
                continue
            if tug_code in row.get("拖轮编号列表", []):
                return row
        return None

    def _release_tugs(self, order: dict[str, Any]) -> None:
        for code in order.get("拖轮编号列表", []):
            tug = self._find_tug_by_code(code)
            if tug is None:
                continue
            if self._active_order_for(code, exclude_order=order) is None:
                tug["status"] = "待命"


# 模块级单例：换班后重新进入页面、刷新页面读到的都是同一进程里这份落库数据。
tug_service = TugService()

# 服务启动即物化分水域马力口径，保证试算、派工、看板随时可读。
tug_policy.standard_rows()
