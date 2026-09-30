"""测绘控制接口：看板统计、控制点台账与点位图工作台共用同一份聚合快照。

核验/状态动作成功后同步触发聚合重算，三个读端拿到的 data_version 必然一致；
重算失败时返回 ok=false 且保留上一份成功统计，不会把看板清零。
"""
from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.survey_control import (
    ACTION_RULES,
    DEFAULT_SHEET,
    REQUIRED_FIELDS,
    AggregateError,
    ReviewConflict,
    SurveyControlService,
)
from app.store import store

router = APIRouter(prefix="/api/survey_point", tags=["测绘控制"])

# 路由层与看板/台账/点位图共用同一个聚合服务单例（同一把锁、同一份快照）
service = SurveyControlService(source=lambda: store.rows("survey_point"))

LIST_FIELDS = ["点号", "点类型", "坐标X", "坐标Y", "高程", "精度等级", "观测日期", "点位状态"]
STATUSES = ["完好", "损坏", "已恢复", "废弃"]


def _snapshot_or_503() -> dict[str, Any]:
    try:
        return service.ensure_snapshot()
    except AggregateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---- 聚合读端：看板 / 点位图工作台 ---------------------------------------

@router.get("/aggregate")
def get_aggregate() -> dict[str, Any]:
    """测绘控制看板 + 点位图工作台数据：一次返回同源同版本的统计与分段。"""
    return _snapshot_or_503()


@router.get("/segments")
def get_segments() -> dict[str, Any]:
    """点位图工作台：按责任组图幅分组的点位，分段总数与去重点位数已校验。"""
    snapshot = _snapshot_or_503()
    return {
        "data_version": snapshot["data_version"],
        "review_seq": snapshot["review_seq"],
        "segments": snapshot["segments"],
        "checks": snapshot["checks"],
    }


@router.post("/aggregate/rebuild", response_model=ActionResult)
def rebuild_aggregate(payload: EntryPayload | None = None) -> ActionResult:
    """幂等批次聚合重建；同批次重放只返回当前快照，失败保留旧统计不清零。"""
    batch_size = 50
    if payload is not None:
        try:
            batch_size = int(payload.values.get("batch_size") or 50)
        except (TypeError, ValueError):
            batch_size = 50
    try:
        snapshot = service.rebuild(batch_size=batch_size)
    except AggregateError as exc:
        return ActionResult(ok=False, message=str(exc), entry=service.stale_snapshot())
    message = "聚合已重建，看板统计/控制点清单/点位图工作台一致"
    if snapshot.get("replayed"):
        message = "批次已生效，幂等跳过重复重算，当前统计保持不变"
    return ActionResult(ok=True, message=message, entry=snapshot)


# ---- 控制点台账 -----------------------------------------------------------

@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按点号检索"),
    point_type: str | None = Query(default=None, description="按点类型过滤"),
    review: str | None = Query(default=None, description="待复核/已复核"),
    sheet: str | None = Query(default=None, description="按责任组图幅编号过滤"),
    status: str | None = Query(default=None, description="完好、损坏、已恢复、废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """控制点台账走聚合快照（按点号去重），与看板/点位图同版本。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    try:
        items, total = service.list_points(
            keyword=keyword,
            point_type=point_type,
            review=review,
            sheet=sheet,
            page=page,
            size=size,
        )
    except AggregateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if status:
        items = [item for item in items if item.get("status") == status]
        total = len(items)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条控制点明细（含全部历史签发坐标版本）；不存在给出可读说明。"""
    entry = service.find_point(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"控制点 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条控制点；成功后立即重算聚合，三端同步可见。"""
    values = payload.values
    missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    rows = store.rows("survey_point")
    entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
    for field in set(list(LIST_FIELDS) + ["责任组", "图幅编号", "图幅名称"]):
        if values.get(field) is not None:
            entry[field] = values[field]
    entry["status"] = values.get("点位状态") or "完好"
    entry["pending"] = True
    entry["abnormal"] = False
    entry["复核状态"] = "待复核"
    if values.get("坐标X") is not None:
        entry["coordinate_versions"] = [{
            "版本": 1,
            "坐标X": values.get("坐标X"),
            "坐标Y": values.get("坐标Y"),
            "高程": values.get("高程"),
            "图幅编号": values.get("图幅编号") or DEFAULT_SHEET,
            "图幅名称": values.get("图幅名称"),
            "责任组": values.get("责任组"),
            "签发日期": values.get("观测日期"),
        }]
    rows.append(entry)
    try:
        snapshot = service.rebuild()
    except AggregateError as exc:
        rows.remove(entry)
        return ActionResult(ok=False, message=f"登记后聚合重算失败已回滚：{exc}")
    return ActionResult(ok=True, message="控制点已登记，看板/台账/点位图已同步", entry=snapshot)


@router.post("/{entry_id}/review", response_model=ActionResult)
def review_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """点类型复核：带期望版本号做并发控制，成功后三端同步回写。"""
    values = payload.values
    point_type = str(values.get("点类型") or values.get("point_type") or "").strip() or None
    expected_seq = values.get("expected_seq")
    try:
        expected = int(expected_seq) if expected_seq is not None else None
    except (TypeError, ValueError):
        return ActionResult(ok=False, message=f"期望版本号格式不正确：{expected_seq}")
    try:
        entry, message, snapshot = service.review_point(
            entry_id,
            point_type=point_type,
            expected_seq=expected,
            operator=str(values.get("复核人") or ""),
        )
    except ReviewConflict as exc:
        return ActionResult(ok=False, message=str(exc), entry=service.current_snapshot())
    if entry is None:
        return ActionResult(ok=False, message=message, entry=snapshot)
    return ActionResult(ok=True, message=message, entry=snapshot)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条控制点执行登记损坏、安排恢复、标记废弃；成功后同步重算聚合。"""
    action = str(payload.values.get("action") or "").strip()
    if action not in ACTION_RULES:
        return ActionResult(ok=False, message=f"动作「{action}」不属于测绘控制可执行范围")
    row = None
    for candidate in store.rows("survey_point"):
        if int(candidate.get("id", 0)) == entry_id:
            row = candidate
            break
    if row is None:
        return ActionResult(ok=False, message=f"控制点 {entry_id} 不存在或已归档")

    backup = copy.deepcopy(row)
    target = ACTION_RULES[action]
    row["status"] = target
    row["点位状态"] = target
    # 状态流转不改变核验结论；pending 仅表示点位是否仍需业务跟踪（废弃除外）
    row["pending"] = target != "废弃"
    row["abnormal"] = target == "损坏"
    try:
        snapshot = service.rebuild()
    except AggregateError as exc:
        row.clear()
        row.update(backup)
        return ActionResult(ok=False, message=f"控制点已{action}，但聚合重算失败，动作已回滚：{exc}")
    return ActionResult(ok=True, message=f"控制点已{action}，看板/台账/点位图已同步", entry=snapshot)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出测绘控制清单：返回聚合后的去重全量台账。"""
    items, total = service.list_points(page=1, size=10000)
    return {"module": "survey_point", "total": total, "items": items}
