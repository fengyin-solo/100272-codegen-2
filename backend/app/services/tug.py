"""拖轮调度业务规则：派工校验、窗口冲突、重复登记与看板汇总。

派工口径集中在 :mod:`app.services.tug_rules`，本层只负责：
- 检修中的拖轮不参与派工；
- 两人同时派同一条船（时间窗口重叠）时，后一次以先落库的为准，直接拦下；
- 可用马力低于下限时不允许派工，并说明差多少；
- 作业窗口超出水域允许时段时放行但另作提示；
- 同一艘拖轮重复登记同一作业（同作业号）只留最新一版。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.services.tug_rules import (
    calculate_requirement,
    find_standard,
    parse_window,
    window_within_allowed,
    windows_overlap,
)
from app.store import store

TUG_MODULE = "tug"
ORDER_MODULE = "tug_order"
STANDARD_MODULE = "_tug_standard"

TUG_STATUSES = ["待命", "执行中", "检修中", "停用"]
ORDER_STATUSES = ["已派工", "已完成", "已取消"]
ACTIVE_ORDER_STATUS = "已派工"

REQUIRED_FIELDS = ["作业编号", "船名", "船型", "水域", "作业量", "开始时间", "结束时间", "拖轮"]


class TugService:
    def __init__(self) -> None:
        # 派工要做「查重再落库」，串行化后先落库的那份就是冲突裁定依据。
        self._lock = threading.Lock()

    # ---- 拖轮台账 ----------------------------------------------------
    def list_tugs(
        self,
        *,
        keyword: str | None = None,
        area: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(TUG_MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("拖轮编号", ""))]
        if area:
            rows = [row for row in rows if row.get("水域") == area]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_tug(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(TUG_MODULE, entry_id)

    def run_tug_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        row = store.find(TUG_MODULE, entry_id)
        if row is None:
            return None, f"拖轮 {entry_id} 不存在或已归档"
        if action == "登记检修":
            if row.get("status") == "执行中":
                return None, f"拖轮 {row.get('拖轮编号')} 正在执行派工单，不能登记检修"
            row["status"] = "检修中"
        elif action == "修复归队":
            if row.get("status") != "检修中":
                return None, f"拖轮 {row.get('拖轮编号')} 当前不是检修状态"
            row["status"] = "待命"
        elif action == "停用":
            row["status"] = "停用"
        else:
            return None, f"动作「{action}」不属于拖轮台账可执行范围"
        return row, f"拖轮已{action}"

    # ---- 水域标准 ----------------------------------------------------
    def list_standards(self) -> list[dict[str, Any]]:
        return store.rows(STANDARD_MODULE)

    def calculate(self, area: str, vessel_type: str, workload: float) -> dict[str, Any]:
        return calculate_requirement(area, vessel_type, workload)

    # ---- 派工单 ------------------------------------------------------
    def list_orders(
        self,
        *,
        keyword: str | None = None,
        area: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(ORDER_MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("作业编号", "")) or keyword in str(row.get("船名", ""))]
        if area:
            rows = [row for row in rows if row.get("水域") == area]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        rows = sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_order(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(ORDER_MODULE, entry_id)

    def create_order(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str, list[str]]:
        """登记/改派一条派工单。

        返回 (派工单, 错误说明, 预警说明)：错误时不落库；预警（如超时段）照常落库。
        """
        with self._lock:
            return self._create_order_locked(values)

    def _create_order_locked(
        self,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str, list[str]]:
        missing = [
            field for field in REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}", []

        job_no = str(values["作业编号"]).strip()
        vessel_name = str(values["船名"]).strip()
        vessel_type = str(values["船型"]).strip()
        area = str(values["水域"]).strip()
        tug_names_raw = str(values["拖轮"]).strip()
        tug_names = [item.strip() for item in tug_names_raw.replace("，", ",").replace("、", ",").split(",") if item.strip()]

        try:
            workload = float(values["作业量"])
        except (TypeError, ValueError):
            return None, f"作业量「{values.get('作业量')}」不是有效数字", []
        if workload <= 0:
            return None, "拖带作业量必须大于 0", []

        try:
            start = parse_window(values["开始时间"])
            end = parse_window(values["结束时间"])
        except (TypeError, ValueError):
            return None, "作业时间格式应为 YYYY-MM-DD HH:MM", []
        if end <= start:
            return None, "结束时间必须晚于开始时间", []

        if not tug_names:
            return None, "至少要派一条拖轮", []
        if len(set(tug_names)) != len(tug_names):
            return None, "同一条拖轮不能在一份派工单里重复出现", []

        # 口径试算：所需马力、建议条数、允许时段全部按分水域标准计算。
        try:
            requirement = calculate_requirement(area, vessel_type, workload)
        except ValueError as exc:
            return None, str(exc), []

        # 检修中的拖轮不参与派工，停用的同样不能派。
        selected: list[dict[str, Any]] = []
        for name in tug_names:
            tug = self._find_tug_by_code(name)
            if tug is None:
                return None, f"拖轮「{name}」不在拖轮台账中", []
            if tug.get("水域") != area:
                return None, f"拖轮「{name}」配属{tug.get('水域')}，不能跨水域派到{area}", []
            if tug.get("status") == "检修中":
                return None, f"拖轮「{name}」检修中，不参与派工", []
            if tug.get("status") == "停用":
                return None, f"拖轮「{name}」已停用，不能派工", []
            selected.append(tug)

        orders = store.rows(ORDER_MODULE)
        existing = self._find_order_by_job(job_no)

        # 两人同时派同一条船：与先落库的派工单窗口重叠即冲突，后一次以前一份为准。
        for name, tug in zip(tug_names, selected):
            occupied = self._occupied_by(
                tug_id=int(tug["id"]),
                start=start,
                end=end,
                exclude_order_id=int(existing["id"]) if existing else None,
            )
            if occupied is not None:
                return (
                    None,
                    (
                        f"拖轮「{name}」在 {start:%Y-%m-%d %H:%M}~{end:%Y-%m-%d %H:%M} "
                        f"已先派给作业 {occupied.get('作业编号')}（派工单 #{occupied.get('id')}，"
                        f"落库时间 {occupied.get('登记时间')}），本次派工作废，以先落库的那份为准"
                    ),
                    [],
                )

        required_hp = int(requirement["所需马力"])
        provided_hp = sum(int(tug["额定马力"]) for tug in selected)
        min_count = int(requirement["最少条数"])

        # 可用马力低于下限时不允许派工，并说明差多少。
        if provided_hp < required_hp:
            gap = required_hp - provided_hp
            return (
                None,
                (
                    f"可用马力不足：本作业按{area}「{vessel_type}」口径需要 {required_hp} 马力"
                    f"（口径：{requirement['计算口径']}），所选 {len(selected)} 条拖轮合计 "
                    f"{provided_hp} 马力，还差 {gap} 马力，不允许派工"
                ),
                [],
            )
        if len(selected) < min_count:
            return (
                None,
                (
                    f"派工条数不足：{area}「{vessel_type}」最少派 {min_count} 条，"
                    f"本次只选了 {len(selected)} 条，不允许派工"
                ),
                [],
            )

        # 作业窗口超出允许时段：不拦截，另作提示并在派工单上留痕。
        warnings: list[str] = []
        standard = find_standard(area, vessel_type)
        assert standard is not None
        if not window_within_allowed(start, end, standard["allowed_window"]):
            warnings.append(
                f"作业窗口 {start:%Y-%m-%d %H:%M}~{end:%Y-%m-%d %H:%M} 超出{area}允许作业时段"
                f"（{requirement['允许时段']}），已另行提示，请向调度主管确认后再执行"
            )

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 同一艘拖轮重复登记同一作业时只留最新一版：按作业号覆盖，不新增派工单。
        if existing is not None:
            # 改派时把旧版选中、本次不再选中的拖轮释放回待命。
            old_names = self._split_tug_names(existing.get("拖轮", ""))
            released_names = old_names - set(tug_names)
            self._release_tugs("、".join(released_names), exclude_order_id=int(existing["id"]))
            existing.update(
                {
                    "船名": vessel_name,
                    "船型": vessel_type,
                    "水域": area,
                    "作业量": workload,
                    "作业量单位": requirement["作业量单位"],
                    "开始时间": start.strftime("%Y-%m-%d %H:%M"),
                    "结束时间": end.strftime("%Y-%m-%d %H:%M"),
                    "拖轮": "、".join(tug_names),
                    "所需马力": required_hp,
                    "合计马力": provided_hp,
                    "马力下限": requirement["马力下限"],
                    "建议条数": requirement["建议条数"],
                    "实派条数": len(selected),
                    "允许时段": requirement["允许时段"],
                    "计算口径": requirement["计算口径"],
                    "超窗口": bool(warnings),
                    "提示": "；".join(warnings),
                    "status": ACTIVE_ORDER_STATUS,
                    "pending": True,
                    "abnormal": bool(warnings),
                    "登记时间": now,
                }
            )
            entry = existing
            message = f"作业 {job_no} 已有派工单，已按最新登记覆盖"
        else:
            entry = {
                "id": max((int(row.get("id", 0)) for row in orders), default=0) + 1,
                "作业编号": job_no,
                "船名": vessel_name,
                "船型": vessel_type,
                "水域": area,
                "作业量": workload,
                "作业量单位": requirement["作业量单位"],
                "开始时间": start.strftime("%Y-%m-%d %H:%M"),
                "结束时间": end.strftime("%Y-%m-%d %H:%M"),
                "拖轮": "、".join(tug_names),
                "所需马力": required_hp,
                "合计马力": provided_hp,
                "马力下限": requirement["马力下限"],
                "建议条数": requirement["建议条数"],
                "实派条数": len(selected),
                "允许时段": requirement["允许时段"],
                "计算口径": requirement["计算口径"],
                "超窗口": bool(warnings),
                "提示": "；".join(warnings),
                "status": ACTIVE_ORDER_STATUS,
                "pending": True,
                "abnormal": bool(warnings),
                "登记时间": now,
            }
            orders.append(entry)
            message = "派工单已落库"

        for tug in selected:
            tug["status"] = "执行中"
            tug["当前任务"] = job_no

        return entry, message, warnings

    def run_order_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        with self._lock:
            entry = store.find(ORDER_MODULE, entry_id)
            if entry is None:
                return None, f"派工单 {entry_id} 不存在或已归档"
            if entry.get("status") != ACTIVE_ORDER_STATUS:
                return None, f"派工单当前为「{entry.get('status')}」，不能再执行{action}"
            if action == "完成作业":
                entry["status"] = "已完成"
            elif action == "取消派工":
                entry["status"] = "已取消"
            else:
                return None, f"动作「{action}」不属于派工单可执行范围"
            entry["pending"] = False
            entry["abnormal"] = bool(entry.get("超窗口"))
            self._release_tugs(str(entry.get("拖轮", "")), exclude_order_id=int(entry.get("id", 0)))
            return entry, f"派工单已{action}"

    # ---- 调度看板 ----------------------------------------------------
    def board(self) -> dict[str, Any]:
        tugs = store.rows(TUG_MODULE)
        orders = store.rows(ORDER_MODULE)
        active = [row for row in orders if row.get("status") == ACTIVE_ORDER_STATUS]
        warning_orders = [row for row in active if row.get("超窗口")]
        areas = sorted({str(row.get("水域")) for row in tugs if row.get("水域")})
        area_cards = []
        for area in areas:
            area_tugs = [row for row in tugs if row.get("水域") == area]
            idle = [row for row in area_tugs if row.get("status") == "待命"]
            area_cards.append(
                {
                    "水域": area,
                    "拖轮总数": len(area_tugs),
                    "待命条数": len(idle),
                    "检修条数": sum(1 for row in area_tugs if row.get("status") == "检修中"),
                    "可用马力": sum(int(row["额定马力"]) for row in idle),
                    "在执派工单": sum(1 for row in active if row.get("水域") == area),
                }
            )
        return {
            "cards": [
                {"label": "拖轮总数", "value": len(tugs)},
                {"label": "待命拖轮", "value": sum(1 for row in tugs if row.get("status") == "待命")},
                {"label": "在执派工单", "value": len(active)},
                {"label": "超窗口提示", "value": len(warning_orders)},
                {"label": "检修中", "value": sum(1 for row in tugs if row.get("status") == "检修中")},
            ],
            "areas": area_cards,
            "active_orders": sorted(active, key=lambda row: str(row.get("开始时间", ""))),
            "warning_orders": warning_orders,
        }

    # ---- 内部辅助 ----------------------------------------------------
    @staticmethod
    def _split_tug_names(text: Any) -> set[str]:
        # 录入兼容半角逗号、全角逗号与顿号；落库统一用顿号，解析时全部还原。
        normalized = str(text or "").replace("，", ",").replace("、", ",")
        return {item.strip() for item in normalized.split(",") if item.strip()}

    def _find_tug_by_code(self, code: str) -> dict[str, Any] | None:
        for row in store.rows(TUG_MODULE):
            if str(row.get("拖轮编号")) == code or str(row.get("拖轮名称")) == code:
                return row
        return None

    def _find_order_by_job(self, job_no: str) -> dict[str, Any] | None:
        for row in store.rows(ORDER_MODULE):
            if str(row.get("作业编号")) == job_no:
                return row
        return None

    def _occupied_by(
        self,
        *,
        tug_id: int,
        start: datetime,
        end: datetime,
        exclude_order_id: int | None,
    ) -> dict[str, Any] | None:
        """该拖轮在给定窗口内是否已被先落库的有效派工单占用。"""
        for row in store.rows(ORDER_MODULE):
            if row.get("status") != ACTIVE_ORDER_STATUS:
                continue
            if exclude_order_id is not None and int(row.get("id", 0)) == exclude_order_id:
                continue
            names = self._split_tug_names(row.get("拖轮", ""))
            tug = store.find(TUG_MODULE, tug_id)
            if tug is None or tug.get("拖轮编号") not in names:
                continue
            try:
                other_start = parse_window(row["开始时间"])
                other_end = parse_window(row["结束时间"])
            except (KeyError, ValueError):
                continue
            if windows_overlap(start, end, other_start, other_end):
                return row
        return None

    def _release_tugs(self, names_text: str, exclude_order_id: int | None = None) -> None:
        names = self._split_tug_names(names_text)
        for tug in store.rows(TUG_MODULE):
            if tug.get("拖轮编号") not in names or tug.get("status") != "执行中":
                continue
            # 该拖轮还挂在别的有效派工单上时不能归队（一份派工单完成/改派不等于全部完工）。
            if self._active_orders_for(str(tug.get("拖轮编号")), exclude_order_id):
                continue
            tug["status"] = "待命"
            tug["当前任务"] = ""

    def _active_orders_for(self, code: str, exclude_order_id: int | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in store.rows(ORDER_MODULE):
            if row.get("status") != ACTIVE_ORDER_STATUS:
                continue
            if exclude_order_id is not None and int(row.get("id", 0)) == exclude_order_id:
                continue
            if code in self._split_tug_names(row.get("拖轮", "")):
                result.append(row)
        return result
