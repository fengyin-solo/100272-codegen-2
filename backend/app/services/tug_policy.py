"""拖轮派工马力口径：按水域与船型分开配置，所有接口（试算、派工、看板）共用这一份算法。

口径说明：
- 需求马力 = 船型基准马力 + ⌈拖带作业量(吨) × 每千吨马力系数 / 1000⌉
- 需求拖轮数 = max(船型最少拖轮数, ⌈需求马力 / 该水域标准拖轮马力⌉)
- 作业时段按水域配置（支持跨零点，如 20:00-次日06:00），只做提示不强制拦截
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from app.store import store

STANDARD_TABLE = "tug_standard"

WATERS = ("内港池", "主航道", "锚地")
VESSEL_TYPES = ("小型船", "中型船", "大型船", "超大型船")

# 水域 -> 允许时段（起、止）、单船标准马力、各船型口径
DEFAULT_STANDARDS: dict[str, dict[str, Any]] = {
    "内港池": {
        "allowed_start": "06:00",
        "allowed_end": "22:00",
        "reference_hp": 4000,
        "vessels": {
            # 船型: (基准马力, 每千吨作业量马力系数, 最少拖轮数)
            "小型船": (800, 120, 1),
            "中型船": (1600, 150, 2),
            "大型船": (2600, 180, 2),
            "超大型船": (3600, 220, 3),
        },
    },
    "主航道": {
        "allowed_start": "00:00",
        "allowed_end": "23:59",
        "reference_hp": 5000,
        "vessels": {
            "小型船": (600, 100, 1),
            "中型船": (1200, 120, 2),
            "大型船": (2200, 150, 2),
            "超大型船": (3200, 180, 3),
        },
    },
    "锚地": {
        "allowed_start": "05:00",
        "allowed_end": "20:00",
        "reference_hp": 5000,
        "vessels": {
            "小型船": (1000, 150, 1),
            "中型船": (2000, 180, 2),
            "大型船": (3000, 200, 2),
            "超大型船": (4200, 240, 3),
        },
    },
}


def standard_rows() -> list[dict[str, Any]]:
    """读取当前生效的水域标准（标准可按水域分别调整，落库后新派工单按新标准算）。"""
    rows = store.rows(STANDARD_TABLE)
    if not rows:
        seed_standard_rows(commit=True)
        rows = store.rows(STANDARD_TABLE)
    return rows


def seed_standard_rows(*, commit: bool = False) -> list[dict[str, Any]]:
    """把默认口径展开成 水域×船型 的行；commit=True 时写入仓库。"""
    rows: list[dict[str, Any]] = []
    next_id = 1
    for water in WATERS:
        conf = DEFAULT_STANDARDS[water]
        for vessel in VESSEL_TYPES:
            base_hp, hp_per_kt, min_tugs = conf["vessels"][vessel]
            rows.append({
                "id": next_id,
                "水域": water,
                "船型": vessel,
                "基准马力": base_hp,
                "每千吨马力": hp_per_kt,
                "最少拖轮数": min_tugs,
                "标准拖轮马力": conf["reference_hp"],
                "允许开始": conf["allowed_start"],
                "允许结束": conf["allowed_end"],
                "pending": False,
                "abnormal": False,
            })
            next_id += 1
    if commit:
        table = store.rows(STANDARD_TABLE)
        table.clear()
        table.extend(rows)
    return rows


def _standard_row(water: str, vessel_type: str) -> dict[str, Any]:
    for row in standard_rows():
        if row["水域"] == water and row["船型"] == vessel_type:
            return row
    raise KeyError(f"水域「{water}」船型「{vessel_type}」没有配置派工标准")


def water_config(water: str) -> dict[str, Any]:
    """汇总单个水域的时段与标准马力（取该水域任意船型行上的公共配置）。"""
    for row in standard_rows():
        if row["水域"] == water:
            return {
                "water": water,
                "allowed_start": row["允许开始"],
                "allowed_end": row["允许结束"],
                "reference_hp": int(row["标准拖轮马力"]),
            }
    raise KeyError(f"水域「{water}」没有配置派工标准")


def requirements(water: str, vessel_type: str, workload_tons: float) -> dict[str, int]:
    """按口径算需求马力与需求拖轮数——这是全平台唯一的马力需求算法。"""
    if water not in WATERS:
        raise ValueError(f"未知水域「{water}」，可选：{'、'.join(WATERS)}")
    if vessel_type not in VESSEL_TYPES:
        raise ValueError(f"未知船型「{vessel_type}」，可选：{'、'.join(VESSEL_TYPES)}")
    try:
        workload = float(workload_tons)
    except (TypeError, ValueError):
        raise ValueError("拖带作业量必须是数字（吨）")
    if workload <= 0:
        raise ValueError("拖带作业量必须大于 0")

    row = _standard_row(water, vessel_type)
    base_hp = int(row["基准马力"])
    hp_per_kt = int(row["每千吨马力"])
    min_tugs = int(row["最少拖轮数"])
    reference_hp = int(row["标准拖轮马力"])

    variable_hp = math.ceil(workload * hp_per_kt / 1000)
    required_hp = base_hp + variable_hp
    required_count = max(min_tugs, math.ceil(required_hp / reference_hp))
    return {
        "required_hp": required_hp,
        "required_count": required_count,
        "base_hp": base_hp,
        "hp_per_kt": hp_per_kt,
        "reference_hp": reference_hp,
        "min_tugs": min_tugs,
    }


def parse_dt(value: str) -> datetime:
    """兼容 'YYYY-MM-DD HH:MM' 与 datetime-local 的 'YYYY-MM-DDTHH:MM'。"""
    text = str(value or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"时间「{value}」格式不正确，应为 YYYY-MM-DD HH:MM")


def _to_minutes(hm: str) -> int:
    hour, minute = str(hm).split(":")[:2]
    return int(hour) * 60 + int(minute)


def allowed_window_text(water: str) -> str:
    """人类可读的允许时段文本，跨零点显式标注“次日”。"""
    conf = water_config(water)
    start, end = conf["allowed_start"], conf["allowed_end"]
    overnight = _to_minutes(start) >= _to_minutes(end)
    return f"{start}-次日{end}" if overnight else f"{start}-{end}"


def check_window(water: str, start_at: datetime, end_at: datetime) -> dict[str, Any]:
    """作业窗口是否落在该水域允许时段内；跨零点时段按环绕处理。"""
    conf = water_config(water)
    start_limit = _to_minutes(conf["allowed_start"])
    end_limit = _to_minutes(conf["allowed_end"])
    overnight = start_limit >= end_limit

    def within(moment: datetime) -> bool:
        clock = moment.hour * 60 + moment.minute
        if overnight:
            return clock >= start_limit or clock <= end_limit
        return start_limit <= clock <= end_limit

    bad_points = [name for name, moment in (("开始", start_at), ("结束", end_at)) if not within(moment)]
    if bad_points:
        return {
            "within_window": False,
            "allowed_window": allowed_window_text(water),
            "warning": f"作业{ '、'.join(bad_points) }时刻超出{water}允许作业时段（{allowed_window_text(water)}），请另行确认后再派",
        }
    return {"within_window": True, "allowed_window": allowed_window_text(water), "warning": ""}
