"""拖轮调度接口：拖轮台账、派工单（试算/派工/流转）、调度看板与分水域口径配置。

马力需求全部由后端按 ``tug_policy`` 同一口径算定并落库，前端任何页面都不自行计算。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.tug import ORDER_STATUSES, TUG_STATUSES, tug_service
from app.services.tug_policy import VESSEL_TYPES, WATERS

router = APIRouter(prefix="/api/tug", tags=["拖轮调度"])

TUG_COLUMNS = ["拖轮编号", "船名", "额定马力", "水域", "作业号", "需求马力", "status"]
ORDER_COLUMNS = [
    "派工单号", "作业号", "船名", "船型", "水域", "拖带作业量",
    "作业开始", "作业结束", "需求马力", "需求条数", "派出马力", "拖轮编号列表",
    "status", "version", "窗口提示",
]


class DispatchPayload(BaseModel):
    """派工/试算提交字段。"""

    作业号: str
    船名: str
    船型: str
    水域: str
    拖带作业量: float
    作业开始: str
    作业结束: str
    拖轮编号: list[str] = Field(default_factory=list)


class StandardUpdatePayload(BaseModel):
    """分水域调整派工口径，只影响之后的新派工单。"""

    标准拖轮马力: int | None = None
    允许开始: str | None = None
    允许结束: str | None = None
    船型: dict[str, dict[str, int]] = Field(default_factory=dict)


# ------------------------------------------------------------- 调度看板与口径
@router.get("/board", response_model=dict)
def get_board() -> dict[str, Any]:
    """调度看板：各水域需求/派出马力、在途派工单、窗口越界提示。"""
    return tug_service.board()


@router.get("/standards", response_model=dict)
def list_standards() -> dict[str, Any]:
    """查看分水域、分船型的马力口径与允许作业时段。"""
    rows = tug_service.list_standards()
    return {
        "waters": list(WATERS),
        "vessel_types": list(VESSEL_TYPES),
        "items": rows,
    }


@router.put("/standards/{water}", response_model=ActionResult)
def update_standard(water: str, payload: StandardUpdatePayload) -> ActionResult:
    """按水域更新一套口径；已落库派工单不回算，保证历史数值不回退。"""
    rows, message = tug_service.update_standard(water, payload.model_dump())
    if rows is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry={"water": water, "items": rows})


# ------------------------------------------------------------- 派工试算/派工
@router.post("/dispatch/preview", response_model=dict)
def preview_dispatch(payload: DispatchPayload) -> dict[str, Any]:
    """派工前试算：按统一口径回显需求马力、推荐拖轮、缺口与窗口提示，不落库。"""
    plan, message = tug_service.preview_dispatch(payload.model_dump())
    if plan is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "plan": plan}


@router.post("/dispatch", response_model=ActionResult)
def dispatch(payload: DispatchPayload) -> ActionResult:
    """正式派工：占用冲突先到先得、同一作业升版覆盖、马力不足直接拒单。"""
    entry, message = tug_service.dispatch(payload.model_dump())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


# ------------------------------------------------------------- 拖轮台账
@router.get("", response_model=PageResult[dict])
def list_tugs(
    keyword: str | None = Query(default=None, description="按拖轮编号或船名检索"),
    water: str | None = Query(default=None, description="按水域过滤"),
    status: str | None = Query(default=None, description="待命、作业中、检修中"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """拖轮台账列表；台账上的需求马力直接取自在途派工单。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in TUG_STATUSES:
        raise HTTPException(status_code=400, detail=f"状态仅支持：{'、'.join(TUG_STATUSES)}")
    items, total = tug_service.list_tugs(keyword=keyword, water=water, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_tug(payload: EntryPayload) -> ActionResult:
    """登记一条拖轮，缺字段或马力非法时说明原因。"""
    entry, message = tug_service.create_tug(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="拖轮已登记", entry=entry)


@router.get("/{tug_id}", response_model=dict)
def get_tug(tug_id: int) -> dict[str, Any]:
    """读取单条拖轮台账；不存在时给出可读的错误说明。"""
    entry = tug_service.get_tug(tug_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"拖轮 {tug_id} 不存在或已归档")
    return entry


@router.post("/{tug_id}/actions", response_model=ActionResult)
def run_tug_action(tug_id: int, payload: EntryPayload) -> ActionResult:
    """拖轮台账动作：标记检修 / 解除检修；在执行作业的拖轮不允许检修。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = tug_service.run_tug_action(tug_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


# ------------------------------------------------------------- 派工单
@router.get("/orders/list", response_model=PageResult[dict])
def list_orders(
    keyword: str | None = Query(default=None, description="按作业号、派工单号或船名检索"),
    water: str | None = Query(default=None, description="按水域过滤"),
    status: str | None = Query(default=None, description="已派工、作业中、已完成、已取消"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """派工单列表；需求马力为派工时算定落库的数值，刷新、换班后读取都一致。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in ORDER_STATUSES:
        raise HTTPException(status_code=400, detail=f"状态仅支持：{'、'.join(ORDER_STATUSES)}")
    items, total = tug_service.list_orders(keyword=keyword, water=water, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/orders/{order_id}", response_model=dict)
def get_order(order_id: int) -> dict[str, Any]:
    """读取单张派工单。"""
    entry = tug_service.get_order(order_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"派工单 {order_id} 不存在或已归档")
    return entry


@router.post("/orders/{order_id}/actions", response_model=ActionResult)
def run_order_action(order_id: int, payload: EntryPayload) -> ActionResult:
    """派工单流转：开始作业 / 完成作业 / 取消派工，完成或取消后拖轮回到待命。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = tug_service.run_order_action(order_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
