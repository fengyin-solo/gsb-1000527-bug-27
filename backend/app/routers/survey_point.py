"""测绘控制接口：维护控制点，覆盖点类型复核、聚合重算与点位图工作台。

核验动作在同一请求里完成「结论落库 + 聚合重算」，返回的 aggregate 就是看板、
台账清单与点位图工作台共用的同一份快照，前端按它同步刷新三处视图。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.survey_point import (
    POINT_TYPES,
    AggregateError,
    ConcurrentVerifyError,
    service,
)

router = APIRouter(prefix="/api/survey_point", tags=["测绘控制"])

LIST_FIELDS = ["点号", "点类型", "坐标X", "坐标Y", "高程", "精度等级", "观测日期", "点位状态"]
STATUSES = ["完好", "损坏", "已恢复", "废弃"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按点号检索"),
    status: str | None = Query(default=None, description="完好、损坏、已恢复、废弃"),
    point_type: str | None = Query(default=None, description="按点类型筛选"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按点号、状态与点类型过滤控制点清单；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, point_type=point_type, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/aggregate")
def get_aggregate() -> dict[str, Any]:
    """统一汇总视图：看板统计、控制点清单、点位图工作台都以此为准。"""
    return service.get_snapshot()


@router.get("/workbench")
def get_workbench() -> dict[str, Any]:
    """点位图工作台：分段落位、跨图幅点、签发版本与一致性校验结果。"""
    return service.workbench()


@router.post("/rebuild")
def rebuild_aggregate(payload: EntryPayload | None = None) -> dict[str, Any]:
    """幂等批次重建：同一 batch_id 重放只返回已提交快照；校验失败时旧统计保留。"""
    batch_id = None
    if payload is not None:
        batch_id = str(payload.values.get("batch_id") or "").strip() or None
    try:
        snapshot, batch_id = service.rebuild(batch_id)
    except AggregateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "batch_id": batch_id, "aggregate": snapshot}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条控制点明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"控制点 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条控制点，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    aggregate = service.get_snapshot()
    return ActionResult(ok=True, message="控制点已登记", entry=entry, aggregate=aggregate)


@router.post("/{entry_id}/verify", response_model=ActionResult)
def verify_point(entry_id: int, payload: EntryPayload) -> ActionResult:
    """点类型复核：结论与聚合重算原子提交，返回最新快照供三处视图同步。"""
    point_type = str(payload.values.get("点类型") or "").strip()
    batch_id = str(payload.values.get("batch_id") or "").strip() or None
    raw_revision = payload.values.get("expected_revision")
    try:
        expected_revision = int(raw_revision) if raw_revision is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="expected_revision 必须是整数版本号")
    try:
        entry, aggregate, batch_id = service.verify_point(
            entry_id,
            point_type,
            expected_revision=expected_revision,
            batch_id=batch_id,
        )
    except ConcurrentVerifyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AggregateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ActionResult(
        ok=True,
        message=f"复核完成：{entry.get('点号')} 的点类型确认为 {point_type}，统计已同步",
        entry=entry,
        aggregate=aggregate,
        batch_id=batch_id,
    )


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条控制点执行登记损坏、安排恢复、标记废弃；动作与重算一起提交。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message, aggregate = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry, aggregate=aggregate)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出测绘控制清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "survey_point", "total": total, "items": items}
