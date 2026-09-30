"""测绘控制聚合服务：控制点台账、签发坐标版本、图幅责任组在这里汇成同一份快照。

口径约定（看板统计、控制点清单、点位图工作台共用这一份快照，不再各算各的）：

- 一个控制点（按点号）只产生一条权威落位：跨图幅时，最新签发坐标决定坐标，
  台账落在哪张图幅则以「责任组图幅」为准；责任组图幅缺失时退回最新坐标图幅。
- survey_point_coord 是只追加的签发历史，按 (点号, version) 保留，重建不改写。
- 缺责任组 / 缺图幅编号的存量点在重建时幂等补数（迁移），不覆盖已存在的值。
- 重建按批次幂等：同一 batch_id 重复请求直接返回已提交快照，不重复计数。
- 分段（按权威图幅落位）总数必须与去重点位数相符，否则整批失败，不换表。
- 核验（点类型复核）与重算在同一把写锁内完成：后到的复核结论按 revision
  做乐观校验，只能有一个结论生效；重算未成功时台账与统计保持旧值不清零。
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "survey_point"
COORD_MODULE = "survey_point_coord"
SHEET_MODULE = "survey_point_sheet"

REQUIRED_FIELDS = ["点号", "点类型", "坐标X"]
STATUS_ORDER = ["完好", "损坏", "已恢复", "废弃"]
ACTION_RULES = {"登记损坏": "损坏", "安排恢复": "已恢复", "标记废弃": "废弃"}
NEGATIVE_ACTIONS = []
POINT_TYPES = ["GPS控制点", "三角点", "水准点", "图根点"]

REBUILD_BATCH_SIZE = 200
UNASSIGNED_SHEET = "未归属图幅"
MIGRATED_GROUP = "补录责任组"
# 已迁移补数的点号：迁移只补一次，重复重建不再重复计数、不覆盖已补值。
_migrated_codes: set[str] = set()


class AggregateError(RuntimeError):
    """聚合一致性不满足或批次参数非法。"""


class ConcurrentVerifyError(AggregateError):
    """复核结论已被先到的请求提交，本次乐观校验失败。"""


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


class SurveyPointService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot: dict[str, Any] | None = None
        self._batch_snapshots: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ 快照

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            if self._snapshot is None:
                self._snapshot = self._rebuild(batch_id=None, register_batch=False)
            return self._clone(self._snapshot)

    def latest_version(self) -> int:
        return int(self.get_snapshot()["version"])

    # ----------------------------------------------------------- 台账只读

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        point_type: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in _text(row.get("点号"))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if point_type:
            rows = [row for row in rows if row.get("点类型") == point_type]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [dict(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return dict(row) if row is not None else None

    # ----------------------------------------------------------- 登记/动作

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _text(values.get(field))]
        if missing:
            return None, missing
        with self._lock:
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            entry["pending_check"] = False
            entry["revision"] = 1
            entry["last_batch"] = None
            for optional in ("坐标Y", "高程", "精度等级", "观测日期", "点位状态", "图幅编号", "责任组"):
                if optional in values:
                    entry[optional] = values.get(optional)
            rows.append(entry)
            self._rebuild(batch_id=None, register_batch=False)
            return dict(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        with self._lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"控制点 {entry_id} 不存在或已归档", None
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于测绘控制可执行范围", None
            target = ACTION_RULES[action]
            if target not in STATUS_ORDER:
                return None, f"目标状态「{target}」不在允许的状态序列里", None
            before = dict(entry)
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            entry["revision"] = int(entry.get("revision", 1)) + 1
            try:
                snapshot = self._rebuild(batch_id=None, register_batch=False)
            except AggregateError as exc:
                # 重算没成功：动作回滚，旧统计保留，绝不把成功统计清零。
                entry.clear()
                entry.update(before)
                return None, f"聚合重算未通过，动作已回滚：{exc}", None
            # replace_table 后原引用已脱离新表，回新表取最新行返回。
            refreshed = store.find(MODULE, entry_id)
            return (dict(refreshed) if refreshed else dict(entry)), f"控制点已{action}", snapshot

    # ------------------------------------------------------- 核验（复核点类型）

    def verify_point(
        self,
        entry_id: int,
        point_type: str,
        *,
        expected_revision: int | None = None,
        batch_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
        """复核点类型：结论 + 重算在同一写锁内提交；同批次重放直接返回已提交结果。"""
        point_type = _text(point_type)
        if point_type not in POINT_TYPES:
            raise AggregateError(f"点类型「{point_type}」不在可复核范围：{'、'.join(POINT_TYPES)}")
        batch_id = _text(batch_id) or f"verify-{uuid.uuid4().hex}"
        with self._lock:
            cached = self._batch_snapshots.get(batch_id)
            if cached is not None:
                cached_entry = self.find_verified(batch_id)
                return cached_entry, self._clone(cached), batch_id

            entry = store.find(MODULE, entry_id)
            if entry is None:
                raise AggregateError(f"控制点 {entry_id} 不存在或已归档")
            current_revision = int(entry.get("revision", 1))
            if expected_revision is not None and int(expected_revision) != current_revision:
                # 并发复核：已经有先到的结论改了 revision，本次结论不允许覆盖。
                raise ConcurrentVerifyError(
                    f"控制点 {entry.get('点号')} 的复核结论已更新（revision {current_revision}），"
                    "请刷新后再提交"
                )

            before = dict(entry)
            entry["点类型"] = point_type
            entry["pending_check"] = False
            entry["revision"] = current_revision + 1
            entry["last_batch"] = batch_id
            try:
                snapshot = self._rebuild(batch_id=batch_id, register_batch=True)
            except AggregateError:
                # 重算失败：回滚复核结论，旧快照保留，统计不清零。
                entry.clear()
                entry.update(before)
                raise
            refreshed = store.find(MODULE, entry_id)
            return (dict(refreshed) if refreshed else dict(entry)), snapshot, batch_id

    def find_verified(self, batch_id: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if row.get("last_batch") == batch_id:
                return dict(row)
        return None

    # ------------------------------------------------------------- 幂等重建

    def rebuild(self, batch_id: str | None = None) -> tuple[dict[str, Any], str]:
        batch_id = _text(batch_id) or f"rebuild-{uuid.uuid4().hex}"
        with self._lock:
            cached = self._batch_snapshots.get(batch_id)
            if cached is not None:
                return self._clone(cached), batch_id
            snapshot = self._rebuild(batch_id=batch_id, register_batch=True)
            return snapshot, batch_id

    def workbench(self) -> dict[str, Any]:
        snapshot = self.get_snapshot()
        return {
            "stats": snapshot["stats"],
            "consistency": snapshot["consistency"],
            "segments": snapshot["segments"],
            "cross_sheet_points": snapshot["cross_sheet_points"],
            "coord_versions": snapshot["coord_versions"],
            "migrated": snapshot["migrated"],
            "version": snapshot["version"],
            "rebuilt_at": snapshot["rebuilt_at"],
        }

    # ================================================================== 内部

    def _rebuild(self, *, batch_id: str | None, register_batch: bool) -> dict[str, Any]:
        """在已持锁状态下执行：迁移补数 → 幂等批次落位 → 一致性校验 → 整表提交。"""
        ledger = [dict(row) for row in store.rows(MODULE)]
        coords = [dict(row) for row in store.rows(COORD_MODULE)]
        sheets = [dict(row) for row in store.rows(SHEET_MODULE)]

        sheet_by_code = {_text(row.get("图幅编号")): row for row in sheets if _text(row.get("图幅编号"))}
        group_by_sheet = {
            _text(row.get("图幅编号")): _text(row.get("责任组"))
            for row in sheets
            if _text(row.get("图幅编号")) and _text(row.get("责任组"))
        }
        sheet_by_group = {
            _text(row.get("责任组")): _text(row.get("图幅编号"))
            for row in sheets
            if _text(row.get("责任组")) and _text(row.get("图幅编号"))
        }

        # 签发历史按点号分组、按版本排序，只追加不改写。
        coord_history: dict[str, list[dict[str, Any]]] = {}
        for coord in coords:
            code = _text(coord.get("点号"))
            if not code:
                continue
            try:
                coord["version"] = int(coord.get("version", 1))
            except (TypeError, ValueError):
                coord["version"] = 1
            coord_history.setdefault(code, []).append(coord)
        for versions in coord_history.values():
            versions.sort(key=lambda item: (item["version"], _text(item.get("issued_at"))))

        migrated_points: list[dict[str, Any]] = []
        new_migrated_codes: set[str] = set()
        placements: dict[tuple[str, str], dict[str, Any]] = {}
        raw_segments: dict[str, int] = {}
        ledger_updates: list[dict[str, Any]] = []
        seen_codes: set[str] = set()
        duplicates: list[str] = []

        # 幂等批次：按固定顺序分批处理，同一 (点号, 权威图幅) 只 upsert 一次。
        for start in range(0, len(ledger), REBUILD_BATCH_SIZE):
            batch = ledger[start:start + REBUILD_BATCH_SIZE]
            for point in batch:
                code = _text(point.get("点号"))
                if not code:
                    continue
                if code in seen_codes:
                    duplicates.append(code)
                    continue
                seen_codes.add(code)

                history = coord_history.get(code, [])
                latest = history[-1] if history else None
                latest_sheet = _text(latest.get("图幅编号")) if latest else ""
                for sheet_code in {_text(c.get("图幅编号")) for c in history if _text(c.get("图幅编号"))}:
                    raw_segments[sheet_code] = raw_segments.get(sheet_code, 0) + 1

                # 存量迁移：缺责任组 / 缺图幅编号才补，已有值不动；同一点只补一次。
                migrated_fields: dict[str, str] = {}
                already_migrated = code in _migrated_codes
                sheet_code = _text(point.get("图幅编号"))
                group = _text(point.get("责任组"))
                if not group and sheet_code and group_by_sheet.get(sheet_code):
                    group = group_by_sheet[sheet_code]
                    point["责任组"] = group
                    migrated_fields["责任组"] = group
                if not sheet_code and group and sheet_by_group.get(group):
                    sheet_code = sheet_by_group[group]
                    point["图幅编号"] = sheet_code
                    migrated_fields["图幅编号"] = sheet_code
                if not group and not sheet_code and latest_sheet:
                    sheet_code = latest_sheet
                    group = group_by_sheet.get(latest_sheet, MIGRATED_GROUP)
                    point["图幅编号"] = sheet_code
                    point["责任组"] = group
                    migrated_fields["图幅编号"] = sheet_code
                    migrated_fields["责任组"] = group
                if migrated_fields and not already_migrated:
                    new_migrated_codes.add(code)
                    migrated_points.append({"点号": code, **migrated_fields})

                # 权威落位：跨图幅时以责任组图幅为准；责任组无法定位时才退回
                # 台账图幅、最新签发坐标图幅；再没有则进未归属桶。
                group_sheet = sheet_by_group.get(group, "")
                authoritative_sheet = group_sheet or sheet_code or latest_sheet or UNASSIGNED_SHEET
                authoritative_group = group
                if not authoritative_group and authoritative_sheet != UNASSIGNED_SHEET:
                    authoritative_group = group_by_sheet.get(authoritative_sheet, MIGRATED_GROUP)
                authoritative_group = authoritative_group or MIGRATED_GROUP

                # 台账坐标与最新签发坐标对齐（历史版本仍保留在 coord 表）。
                if latest is not None:
                    for field in ("坐标X", "坐标Y", "高程"):
                        if latest.get(field) is not None:
                            point[field] = latest.get(field)

                key = (code, authoritative_sheet)
                if key not in placements:
                    issued_sheets = {_text(c.get("图幅编号")) for c in history if _text(c.get("图幅编号"))}
                    placements[key] = {
                        "点号": code,
                        "点类型": _text(point.get("点类型")),
                        "权威图幅": authoritative_sheet,
                        "台账图幅": sheet_code,
                        "图幅名称": _text(sheet_by_code.get(authoritative_sheet, {}).get("图幅名称")),
                        "责任组": authoritative_group,
                        "最新签发图幅": latest_sheet,
                        "跨图幅": len(issued_sheets) > 1,
                        "最新版本": latest["version"] if latest else 0,
                        "签发时间": _text(latest.get("issued_at")) if latest else "",
                        "坐标X": latest.get("坐标X") if latest else point.get("坐标X"),
                        "坐标Y": latest.get("坐标Y") if latest else point.get("坐标Y"),
                        "高程": latest.get("高程") if latest else point.get("高程"),
                    }
                ledger_updates.append(point)

        if duplicates:
            raise AggregateError(f"台账点号重复，已中止重建：{'、'.join(sorted(set(duplicates)))}")

        # 分段总数必须与点位去重数相符。
        segment_totals: dict[str, int] = {}
        for (_, sheet_code) in placements:
            segment_totals[sheet_code] = segment_totals.get(sheet_code, 0) + 1
        segment_total = sum(segment_totals.values())
        if segment_total != len(seen_codes):
            raise AggregateError(
                f"分段总数 {segment_total} 与点位去重数 {len(seen_codes)} 不符，已中止重建"
            )
        if len(placements) != len(seen_codes):
            raise AggregateError(
                f"点位去重数 {len(seen_codes)} 与权威落位数 {len(placements)} 不符，已中止重建"
            )

        type_counts = {name: 0 for name in POINT_TYPES}
        status_counts = {name: 0 for name in STATUS_ORDER}
        pending_check = 0
        cross_sheet: list[dict[str, Any]] = []
        for point in ledger_updates:
            ptype = _text(point.get("点类型"))
            type_counts[ptype] = type_counts.get(ptype, 0) + 1
            status = _text(point.get("status")) or STATUS_ORDER[0]
            status_counts[status] = status_counts.get(status, 0) + 1
            if point.get("pending_check"):
                pending_check += 1
        for placement in placements.values():
            if placement["跨图幅"]:
                cross_sheet.append(placement)
        cross_sheet.sort(key=lambda item: item["点号"])

        segments = [
            {
                "图幅编号": code,
                "图幅名称": _text(sheet_by_code.get(code, {}).get("图幅名称")),
                "责任组": group_by_sheet.get(code, MIGRATED_GROUP if code == UNASSIGNED_SHEET else ""),
                "权威点数": count,
                "原始签发点数": raw_segments.get(code, 0),
            }
            for code, count in sorted(segment_totals.items())
        ]
        coord_versions = [
            {
                "点号": code,
                "version": versions[-1]["version"],
                "签发时间": _text(versions[-1].get("issued_at")),
                "图幅编号": _text(versions[-1].get("图幅编号")),
                "历史版本数": len(versions),
            }
            for code, versions in sorted(coord_history.items())
        ]

        previous_version = int(self._snapshot["version"]) if self._snapshot else 0
        snapshot = {
            "version": previous_version + 1,
            "rebuilt_at": datetime.now().isoformat(timespec="seconds"),
            "stats": {
                "控制点总数": len(seen_codes),
                "完好控制点": status_counts.get("完好", 0),
                "损坏控制点": status_counts.get("损坏", 0),
                "已恢复控制点": status_counts.get("已恢复", 0),
                "废弃控制点": status_counts.get("废弃", 0),
                "待复核点数": pending_check,
                "跨图幅点数": len(cross_sheet),
                "迁移补点数": len(migrated_points),
                "type_counts": type_counts,
                "status_counts": status_counts,
            },
            "segments": segments,
            "cross_sheet_points": cross_sheet,
            "coord_versions": coord_versions,
            "migrated": migrated_points,
            "consistency": {
                "segment_total": segment_total,
                "unique_points": len(seen_codes),
                "raw_placement_total": sum(raw_segments.values()),
                "matched": segment_total == len(seen_codes),
            },
            "batch_id": batch_id,
        }

        # 校验全部通过后才整表换出并提交迁移标记：前面任何异常都走不到这里，
        # 旧快照与旧迁移状态因此都保留。
        _migrated_codes.update(new_migrated_codes)
        store.replace_table(MODULE, ledger_updates)
        self._snapshot = snapshot
        if register_batch and batch_id:
            self._batch_snapshots[batch_id] = self._clone(snapshot)
        return self._clone(snapshot)

    @staticmethod
    def _clone(snapshot: dict[str, Any]) -> dict[str, Any]:
        def copy(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: copy(item) for key, item in value.items()}
            if isinstance(value, list):
                return [copy(item) for item in value]
            return value

        return copy(snapshot)


service = SurveyPointService()
