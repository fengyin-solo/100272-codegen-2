"""拖轮派工口径：船型作业量口径、分水域马力配置标准与纯函数计算。

派工口径只有这一份：拖轮台账、派工单、调度看板读到的「所需马力」都来自
``calculate_requirement``；派工接口与马力试算接口也调同一组函数，
任何页面都不允许自己另算一套。
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

# 各船型的拖带作业量口径：作业量取什么数、单位是什么。
VESSEL_TYPES: dict[str, dict[str, str]] = {
    "集装箱船": {"workload_label": "作业箱量", "workload_unit": "TEU"},
    "散货船": {"workload_label": "载货吨位", "workload_unit": "千载重吨"},
    "油轮": {"workload_label": "载货吨位", "workload_unit": "千载重吨"},
    "件杂货船": {"workload_label": "载货吨位", "workload_unit": "千总吨"},
    "客滚船": {"workload_label": "载货吨位", "workload_unit": "千总吨"},
}

# 不同水域的标准分开配置，互不混用：
#   hp_per_unit   每单位拖带作业量需要的马力
#   min_hp        该水域该船型的马力配置下限
#   min_tug_count 最少派几条
#   tug_band_hp   单条拖轮折算马力，用所需马力反推建议条数
#   allowed_window 允许作业时段（跨午夜写成起止倒置，如 18:00-06:00）
TUG_STANDARDS: list[dict[str, Any]] = [
    {
        "area": "南港池",
        "vessel_type": "集装箱船",
        "hp_per_unit": 4.0,
        "min_hp": 5000,
        "min_tug_count": 2,
        "tug_band_hp": 4000,
        "allowed_window": ("06:00", "22:00"),
    },
    {
        "area": "南港池",
        "vessel_type": "散货船",
        "hp_per_unit": 600.0,
        "min_hp": 6000,
        "min_tug_count": 2,
        "tug_band_hp": 4500,
        "allowed_window": ("06:00", "22:00"),
    },
    {
        "area": "南港池",
        "vessel_type": "件杂货船",
        "hp_per_unit": 500.0,
        "min_hp": 4000,
        "min_tug_count": 1,
        "tug_band_hp": 4000,
        "allowed_window": ("07:00", "21:00"),
    },
    {
        "area": "北港池",
        "vessel_type": "集装箱船",
        "hp_per_unit": 3.2,
        "min_hp": 6000,
        "min_tug_count": 2,
        "tug_band_hp": 5000,
        "allowed_window": ("00:00", "23:59"),
    },
    {
        "area": "北港池",
        "vessel_type": "油轮",
        "hp_per_unit": 800.0,
        "min_hp": 8000,
        "min_tug_count": 2,
        "tug_band_hp": 6000,
        "allowed_window": ("05:00", "20:00"),
    },
    {
        "area": "锚地",
        "vessel_type": "集装箱船",
        "hp_per_unit": 5.0,
        "min_hp": 9000,
        "min_tug_count": 2,
        "tug_band_hp": 8000,
        "allowed_window": ("00:00", "23:59"),
    },
    {
        "area": "锚地",
        "vessel_type": "散货船",
        "hp_per_unit": 700.0,
        "min_hp": 10000,
        "min_tug_count": 2,
        "tug_band_hp": 8000,
        "allowed_window": ("00:00", "23:59"),
    },
    {
        "area": "锚地",
        "vessel_type": "客滚船",
        "hp_per_unit": 600.0,
        "min_hp": 8000,
        "min_tug_count": 2,
        "tug_band_hp": 6000,
        "allowed_window": ("06:00", "20:00"),
    },
]


def standard_rows() -> list[dict[str, Any]]:
    """把配置常量摊成台账行，供水域标准接口与初始化数据共用。"""
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(TUG_STANDARDS, start=1):
        lo, hi = item["allowed_window"]
        rows.append(
            {
                "id": index,
                "水域": item["area"],
                "船型": item["vessel_type"],
                "作业量单位": VESSEL_TYPES[item["vessel_type"]]["workload_unit"],
                "单位马力系数": item["hp_per_unit"],
                "马力下限": item["min_hp"],
                "最少条数": item["min_tug_count"],
                "单条折算马力": item["tug_band_hp"],
                "允许时段": f"{lo}-{hi}",
            }
        )
    return rows


def find_standard(area: str, vessel_type: str) -> dict[str, Any] | None:
    for item in TUG_STANDARDS:
        if item["area"] == area and item["vessel_type"] == vessel_type:
            return item
    return None


def parse_window(value: Any) -> datetime:
    """兼容表单的 2026-10-01T08:30 与落库的 2026-10-01 08:30 两种写法。"""
    text = str(value).strip().replace("T", " ")
    return datetime.strptime(text, "%Y-%m-%d %H:%M")


def windows_overlap(
    start_a: datetime,
    end_a: datetime,
    start_b: datetime,
    end_b: datetime,
) -> bool:
    """两个作业窗口是否重叠；端点相接（前一条刚结束）不算冲突。"""
    return start_a < end_b and start_b < end_a


def window_within_allowed(
    start: datetime,
    end: datetime,
    allowed_window: tuple[str, str],
) -> bool:
    """作业窗口是否完整落在水域允许时段内；跨日窗口一律视为超出，需要另作提示。"""
    if start.date() != end.date():
        return False
    lo = datetime.strptime(allowed_window[0], "%H:%M").time()
    hi = datetime.strptime(allowed_window[1], "%H:%M").time()
    start_time = start.time()
    end_time = end.time()
    if lo <= hi:
        return lo <= start_time and end_time <= hi
    # 允许时段跨午夜（如 18:00-06:00）
    return (start_time >= lo or start_time <= hi) and (
        end_time >= lo or end_time <= hi
    )


def calculate_requirement(
    area: str,
    vessel_type: str,
    workload: float,
) -> dict[str, Any]:
    """按口径计算所需马力配置。

    所需马力 = max(roundup(作业量 × 单位马力系数), 马力下限)
    建议条数 = max(ceil(所需马力 / 单条折算马力), 最少条数)
    """
    standard = find_standard(area, vessel_type)
    if standard is None:
        raise ValueError(f"水域「{area}」尚未配置「{vessel_type}」的派工口径")
    vessel = VESSEL_TYPES[vessel_type]
    raw_hp = workload * standard["hp_per_unit"]
    required_hp = max(math.ceil(raw_hp), int(standard["min_hp"]))
    suggested_count = max(
        math.ceil(required_hp / standard["tug_band_hp"]),
        int(standard["min_tug_count"]),
    )
    lo, hi = standard["allowed_window"]
    return {
        "水域": area,
        "船型": vessel_type,
        "作业量": workload,
        "作业量口径": vessel["workload_label"],
        "作业量单位": vessel["workload_unit"],
        "单位马力系数": standard["hp_per_unit"],
        "所需马力": required_hp,
        "马力下限": int(standard["min_hp"]),
        "建议条数": suggested_count,
        "最少条数": int(standard["min_tug_count"]),
        "单条折算马力": int(standard["tug_band_hp"]),
        "允许时段": f"{lo}-{hi}",
        "计算口径": (
            f"max(ceil({workload:g} × {standard['hp_per_unit']:g}), "
            f"{int(standard['min_hp'])}) = {required_hp} 马力；"
            f"建议 max(ceil({required_hp} / {int(standard['tug_band_hp'])}), "
            f"{int(standard['min_tug_count'])}) = {suggested_count} 条"
        ),
    }
