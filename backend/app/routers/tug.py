"""拖轮调度接口：拖轮台账、派工单、调度看板、分水域标准与马力试算。

路由层不做业务判断；所需马力一律由服务层按 tug_rules 口径算出，
保证台账、派工单、看板与试算接口读到的是同一套数。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.tug import TugService

router = APIRouter(prefix="/api/tug", tags=["拖轮调度"])

service = TugService()

TUG_COLUMNS = ["拖轮编号", "拖轮名称", "水域", "额定马力", "所属单位", "当前任务", "拖轮状态"]
ORDER_COLUMNS = [
    "作业编号", "船名", "船型", "水域", "作业量", "作业量单位",
    "开始时间", "结束时间", "拖轮", "所需马力", "合计马力", "建议条数", "实派条数",
    "允许时段", "超窗口", "提示", "登记时间",
]


@router.get("/board")
def board() -> dict[str, Any]:
    """调度看板：分水域的待命条数/可用马力与在执派工单、超窗口提示。"""
    return service.board()


@router.get("/standards")
def standards() -> dict[str, Any]:
    """分水域派工口径：单位马力系数、马力下限、最少条数、允许时段。"""
    items = service.list_standards()
    return {"items": items, "total": len(items)}


@router.get("/calculate")
def calculate(
    area: str = Query(description="水域，如 南港池/北港池/锚地"),
    vessel_type: str = Query(description="船型，如 集装箱船/散货船"),
    workload: float = Query(description="拖带作业量（口径随船型而定）"),
) -> dict[str, Any]:
    """马力试算：只按口径返回所需马力与建议条数，不落任何数据。"""
    try:
        return service.calculate(area, vessel_type, workload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---- 拖轮台账 ----
@router.get("/tugs", response_model=PageResult[dict])
def list_tugs(
    keyword: str | None = Query(default=None, description="按拖轮编号检索"),
    area: str | None = Query(default=None, description="按水域过滤"),
    status: str | None = Query(default=None, description="待命、执行中、检修中、停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_tugs(keyword=keyword, area=area, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/tugs/{entry_id}/actions", response_model=ActionResult)
def run_tug_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """登记检修 / 修复归队 / 停用；检修中的拖轮不会出现在可派工集合里。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_tug_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


# ---- 派工单 ----
@router.get("/orders", response_model=PageResult[dict])
def list_orders(
    keyword: str | None = Query(default=None, description="按作业编号或船名检索"),
    area: str | None = Query(default=None, description="按水域过滤"),
    status: str | None = Query(default=None, description="已派工、已完成、已取消"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_orders(keyword=keyword, area=area, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/orders", response_model=ActionResult)
def create_order(payload: EntryPayload) -> ActionResult:
    """登记派工单：马力不够或缺条数直接拒绝；超时段会放行并在 warnings 里提示；
    同作业编号重复登记只保留最新一版；窗口撞车时以先落库的那份为准。"""
    entry, message, warnings = service.create_order(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    result = ActionResult(ok=True, message=message, entry=entry)
    result.warnings = warnings
    return result


@router.post("/orders/{entry_id}/actions", response_model=ActionResult)
def run_order_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """完成作业 / 取消派工；动作后释放占用的拖轮回待命。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_order_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
