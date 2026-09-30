"""测绘控制点聚合域：核验结论、幂等聚合重算与三端一致快照都收在这里。

三个读端共用同一份聚合快照：

- 看板统计：``stats``（点类型/复核状态/点位状态）
- 控制点清单（台账）：``points`` 去重视图
- 点位图工作台：``segments`` 按责任组图幅分组

口径约束：

- 同一控制点跨图幅时，以最新签发坐标与责任组所在图幅为准，历史坐标按
  签发版本（``版本`` 越大越新）整体保留在 ``coordinate_versions`` 里；
- 存量缺责任组的点首次进入聚合时迁移补数（默认责任组/默认图幅），迁移只补
  一次、重复执行幂等；
- 聚合重建按幂等批次执行，同一个批次重复提交只生效一次；
- 分段总数必须与点位去重数相符，校验不过整批作废，已生效的成功统计不会被
  清零；
- 并发核验带期望版本号（CAS），只有版本匹配的那一个结论能生效。
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from typing import Any, Callable

MODULE = "survey_point"

# 复核状态
REVIEW_PENDING = "待复核"
REVIEW_DONE = "已复核"

# 存量缺责任组点迁移补数时的默认归属
DEFAULT_GROUP = "未分幅补录组"
DEFAULT_SHEET = "H49G000000（待分幅）"
DEFAULT_SHEET_NAME = "待分幅"

# 通用动作映射（兼容既有损坏/恢复/废弃流转）
REQUIRED_FIELDS = ["点号", "点类型", "坐标X"]
STATUS_ORDER = ["完好", "损坏", "已恢复", "废弃"]
ACTION_RULES = {"登记损坏": "损坏", "安排恢复": "已恢复", "标记废弃": "废弃"}

POINT_TYPES = ["三角点", "GPS点", "导线点", "水准点", "图根点"]


class AggregateError(RuntimeError):
    """聚合重算过程中口径校验失败：整批作废，保留上一份成功快照。"""


class ReviewConflict(RuntimeError):
    """并发核验：期望版本与当前版本不一致，本结论不生效。"""


def _coord_value(version: dict[str, Any], key: str) -> float | None:
    raw = version.get(key)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _sorted_versions(row: dict[str, Any]) -> list[dict[str, Any]]:
    """取一条原始点的签发坐标版本，按签发版本号升序（最新在最后）。"""
    versions = row.get("coordinate_versions")
    if isinstance(versions, list) and versions:
        result = [dict(v) for v in versions]
    else:
        result = [{
            "版本": 1,
            "坐标X": row.get("坐标X"),
            "坐标Y": row.get("坐标Y"),
            "高程": row.get("高程"),
            "图幅编号": row.get("图幅编号"),
            "图幅名称": row.get("图幅名称"),
            "责任组": row.get("责任组"),
            "签发日期": row.get("观测日期"),
        }]
    for idx, version in enumerate(result, start=1):
        try:
            version["版本"] = int(version.get("版本") or idx)
        except (TypeError, ValueError):
            version["版本"] = idx
    result.sort(key=lambda item: int(item["版本"]))
    return result


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """把存量行规范化为带签发版本/责任组/复核状态的内部结构（幂等）。"""
    point = dict(row)
    point["coordinate_versions"] = _sorted_versions(point)
    latest = point["coordinate_versions"][-1]

    # 存量缺责任组点迁移补数：责任组以最新签发版本为准，缺失才补默认值
    group = str(latest.get("责任组") or point.get("责任组") or "").strip()
    if not group:
        group = DEFAULT_GROUP
        latest["责任组"] = latest.get("责任组") or DEFAULT_GROUP
        point["migrated"] = True
    point["责任组"] = group

    sheet = str(latest.get("图幅编号") or point.get("图幅编号") or "").strip()
    if not sheet:
        sheet = DEFAULT_SHEET
        latest["图幅编号"] = latest.get("图幅编号") or DEFAULT_SHEET
        latest["图幅名称"] = latest.get("图幅名称") or DEFAULT_SHEET_NAME
        point["migrated"] = True
    point["图幅编号"] = sheet
    point["图幅名称"] = str(latest.get("图幅名称") or point.get("图幅名称") or "")

    # 最新签发坐标提到点视图顶层；顶层旧坐标字段仅为兼容旧读端
    for key in ("坐标X", "坐标Y", "高程"):
        value = latest.get(key)
        if value not in (None, ""):
            point[key] = value
    point["签发版本"] = int(latest["版本"])

    if point.get("复核状态") not in (REVIEW_PENDING, REVIEW_DONE):
        point["复核状态"] = REVIEW_PENDING if point.get("pending", True) else REVIEW_DONE
    return point


def _batch_id(rows: list[dict[str, Any]]) -> str:
    """按批次业务内容生成稳定批次号：内容不变重放得到同一批次（幂等键）。"""
    digest = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"batch-{digest[:16]}"


class SurveyControlService:
    """测绘控制点聚合服务：迁移补数、幂等重建、核验 CAS、一致性校验。"""

    def __init__(self, source: Callable[[], list[dict[str, Any]]] | None = None) -> None:
        self._source = source
        self._lock = threading.RLock()
        self._snapshot: dict[str, Any] | None = None
        self._applied_batches: set[str] = set()
        self._review_seq = 0
        self._data_version = 0
        self._migrated_rows: set[int] = set()
        # 重算故障演练缝：置位后下一次发布会带错失败，用于验证旧统计不被清零
        self._fault: str | None = None

    def arm_fault(self, reason: str = "演练：聚合重算失败") -> None:
        self._fault = reason

    def clear_fault(self) -> None:
        self._fault = None

    # ------------------------------------------------------------------ 数据源
    def _rows(self) -> list[dict[str, Any]]:
        if self._source is not None:
            rows = self._source()
        else:
            from app.store import store

            rows = store.rows(MODULE)
        return rows

    # ------------------------------------------------------------------ 迁移
    def migrate_legacy(self) -> int:
        """存量缺责任组点迁移补数；只补缺失字段，重复执行不产生新变更。"""
        with self._lock:
            changed = 0
            for row in self._rows():
                row_id = int(row.get("id", 0))
                versions = row.get("coordinate_versions")
                if isinstance(versions, list) and versions:
                    targets = versions
                else:
                    targets = [row]
                missing_group = not any(str(v.get("责任组") or "").strip() for v in targets)
                missing_sheet = not any(str(v.get("图幅编号") or "").strip() for v in targets)
                if not (missing_group or missing_sheet):
                    continue
                if missing_group:
                    row["责任组"] = DEFAULT_GROUP
                if missing_sheet:
                    row["图幅编号"] = DEFAULT_SHEET
                    row["图幅名称"] = row.get("图幅名称") or DEFAULT_SHEET_NAME
                row["migrated"] = True
                row["迁移补数"] = "责任组/图幅缺失补录"
                self._migrated_rows.add(row_id)
                changed += 1
            return changed

    # ------------------------------------------------------------- 聚合构建
    def _finalize_point(self, merged: list[dict[str, Any]]) -> dict[str, Any]:
        point = _normalize_row(merged[0])
        if len(merged) > 1:
            # 跨图幅同一控制点：合并各条签发版本，再以最新签发版本为准
            merged_versions = point["coordinate_versions"]
            seen = {(int(v["版本"]), str(v.get("坐标X")), str(v.get("坐标Y"))) for v in merged_versions}
            for other in merged[1:]:
                for version in _sorted_versions(other):
                    key = (int(version["版本"]), str(version.get("坐标X")), str(version.get("坐标Y")))
                    if key not in seen:
                        merged_versions.append(version)
                        seen.add(key)
            # 最新签发优先：版本号 -> 签发日期；仍并列时后收录的来源行视为更新
            merged_versions.sort(key=lambda item: (int(item["版本"]), str(item.get("签发日期") or "")))
            point["coordinate_versions"] = merged_versions
            latest = merged_versions[-1]
            point["签发版本"] = int(latest["版本"])
            point["责任组"] = str(latest.get("责任组") or point.get("责任组") or DEFAULT_GROUP)
            point["图幅编号"] = str(latest.get("图幅编号") or point.get("图幅编号") or DEFAULT_SHEET)
            point["图幅名称"] = str(latest.get("图幅名称") or point.get("图幅名称") or DEFAULT_SHEET_NAME)
            for coord_key in ("坐标X", "坐标Y", "高程"):
                if latest.get(coord_key) not in (None, ""):
                    point[coord_key] = latest.get(coord_key)
            point["跨图幅"] = len({
                str(v.get("图幅编号")) for v in merged_versions if v.get("图幅编号")
            }) > 1
            point["_source_ids"] = sorted({int(r.get("id", 0)) for r in merged})
        else:
            point["跨图幅"] = len({
                str(v.get("图幅编号")) for v in point["coordinate_versions"] if v.get("图幅编号")
            }) > 1
            point["_source_ids"] = [int(merged[0].get("id", 0))]
        latest = point["coordinate_versions"][-1]
        point["最新坐标"] = {
            "版本": int(latest["版本"]),
            "坐标X": _coord_value(latest, "坐标X"),
            "坐标Y": _coord_value(latest, "坐标Y"),
            "高程": _coord_value(latest, "高程"),
            "签发日期": latest.get("签发日期"),
        }
        point.setdefault("跨图幅", False)
        point.setdefault("migrated", False)
        return point

    def _build_candidate(self) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
        """从原始行重算候选聚合；不抛异常的情况下一定返回自洽结果。"""
        grouped: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for row in self._rows():
            code = str(row.get("点号") or f"id-{row.get('id')}").strip()
            if code not in grouped:
                grouped[code] = []
                order.append(code)
            grouped[code].append(row)

        points: list[dict[str, Any]] = []
        for code in order:
            points.append(self._finalize_point(grouped[code]))
        points.sort(key=lambda item: item.get("点号", ""))

        # 点位图工作台：一个点只归责任组所在图幅（跨图幅不重复计数）
        segment_map: dict[str, dict[str, Any]] = {}
        for point in points:
            sheet = point["图幅编号"]
            segment = segment_map.setdefault(sheet, {
                "图幅编号": sheet,
                "图幅名称": point["图幅名称"],
                "责任组": point["责任组"],
                "point_count": 0,
                "点号列表": [],
                "点位": [],
            })
            segment["point_count"] += 1
            segment["点号列表"].append(point["点号"])
            segment["点位"].append({
                "点号": point["点号"],
                "点类型": point.get("点类型"),
                "坐标X": point["最新坐标"]["坐标X"],
                "坐标Y": point["最新坐标"]["坐标Y"],
                "跨图幅": point["跨图幅"],
                "历史版本数": len(point["coordinate_versions"]),
            })
        segments = [segment_map[key] for key in sorted(segment_map)]

        def count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
            result: dict[str, int] = {}
            for row in rows:
                value = str(row.get(key) or "未填写")
                result[value] = result.get(value, 0) + 1
            return result

        stats = {
            "控制点总数": len(points),
            "原始记录数": len(self._rows()),
            "待复核": sum(1 for p in points if p["复核状态"] == REVIEW_PENDING),
            "已复核": sum(1 for p in points if p["复核状态"] == REVIEW_DONE),
            "跨图幅点数": sum(1 for p in points if p["跨图幅"]),
            "迁移补数点数": sum(1 for p in points if p.get("migrated")),
            "图幅数": len(segments),
            "按点类型": count_by(points, "点类型"),
            "按复核状态": count_by(points, "复核状态"),
            "按点位状态": count_by(points, "status"),
        }

        checks: list[dict[str, str]] = []
        segment_total = sum(int(seg["point_count"]) for seg in segments)
        self._check(
            checks,
            "分段总数与点位去重数相符",
            segment_total == len(points),
            f"分段总数 {segment_total} != 去重点位 {len(points)}",
        )
        dedup_codes = {p["点号"] for p in points}
        self._check(
            checks,
            "点位按点号去重",
            len(dedup_codes) == len(points),
            f"去重后点号 {len(dedup_codes)} != 点位数 {len(points)}",
        )
        located = sum(1 for p in points if p["最新坐标"]["坐标X"] is not None and p["最新坐标"]["坐标Y"] is not None)
        self._check(
            checks,
            "点位图坐标覆盖率",
            True,
            f"{len(points) - located} 个点位缺少最新签发坐标，工作台将缺图钉（不阻断发布）",
        )
        if located != len(points):
            for check in checks:
                if check["name"] == "点位图坐标覆盖率":
                    check["status"] = "警告"
        return points, stats, segments, checks

    @staticmethod
    def _check(checks: list[dict[str, str]], name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "status": "通过" if ok else "失败", "detail": detail if not ok else ""})

    def _publish(
        self,
        *,
        batch_id: str | None,
        source_snapshot: Callable[[], tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]],
    ) -> dict[str, Any]:
        """重算并原子发布；任一校验失败则保留旧快照、绝不清零成功统计。"""
        with self._lock:
            if batch_id is not None:
                if batch_id in self._applied_batches:
                    # 幂等：同批次重放直接返回当前快照，不重复处理
                    return self.current_snapshot(replayed=True, batch_id=batch_id)
            fault = self._fault
            if fault:
                # 故障演练缝：失败时直接保留旧快照，绝不把成功统计清零
                self._fault = None
                raise AggregateError(fault)
            try:
                points, stats, segments, checks = source_snapshot()
                if any(item["status"] == "失败" for item in checks):
                    failed = [item["name"] for item in checks if item["status"] == "失败"]
                    raise AggregateError("聚合口径校验未通过：" + "、".join(failed))
            except AggregateError:
                raise
            except Exception as exc:  # 重算本身异常同样不能污染已生效统计
                raise AggregateError(f"聚合重算失败：{exc}") from exc

            self._data_version += 1
            self._snapshot = {
                "data_version": self._data_version,
                "review_seq": self._review_seq,
                "stats": copy.deepcopy(stats),
                "points": copy.deepcopy(points),
                "segments": copy.deepcopy(segments),
                "checks": checks,
                "ok": True,
            }
            if batch_id is not None:
                self._applied_batches.add(batch_id)
                self._snapshot["batch_id"] = batch_id
            return copy.deepcopy(self._snapshot)

    def rebuild(self, batch_size: int = 50) -> dict[str, Any]:
        """幂等批次聚合重建：分段处理，批次号由内容决定，重放不重复生效。

        批次号是“输入内容 + 分段边界”的内容寻址哈希：数据或分批变化都会得到
        新批次并重新重算；完全相同的重建请求则整批幂等跳过。
        """
        with self._lock:
            self.migrate_legacy()
            rows = self._rows()
            batch_size = max(int(batch_size or 50), 1)
            batches = [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)] or [[]]
            batch_ids = [_batch_id(batch) for batch in batches]
            if all(bid in self._applied_batches for bid in batch_ids) and self._snapshot is not None:
                snap = copy.deepcopy(self._snapshot)
                snap["replayed"] = True
                snap["batch_ids"] = batch_ids
                return snap

            # 第一个批次驱动“重算 + 口径校验 + 原子发布”；发布失败整批作废，
            # 一个批次号都不登记，已生效统计保持不变
            snapshot = self._publish(batch_id=None, source_snapshot=self._build_candidate)
            # 发布成功后才把本次幂等批次号一起登记（全有或全无）
            self._applied_batches.update(batch_ids)
            snapshot["batch_ids"] = batch_ids
            return snapshot

    # ------------------------------------------------------------------ 核验
    def review_point(
        self,
        entry_id: int,
        *,
        point_type: str | None = None,
        expected_seq: int | None = None,
        operator: str = "",
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """点类型复核：CAS 保证并发核验只有一个结论生效，并同步回写三端快照。"""
        with self._lock:
            self.migrate_legacy()
            row = None
            for candidate in self._rows():
                if int(candidate.get("id", 0)) == entry_id:
                    row = candidate
                    break
            if row is None:
                return None, f"控制点 {entry_id} 不存在或已归档", None
            if expected_seq is not None and int(expected_seq) != self._review_seq:
                raise ReviewConflict(
                    f"控制点已被其他核验结论更新（期望版本 {expected_seq}，当前 {self._review_seq}）"
                )

            backup = copy.deepcopy(row)
            try:
                if point_type is not None and str(point_type).strip():
                    row["点类型"] = str(point_type).strip()
                row["复核状态"] = REVIEW_DONE
                row["pending"] = False
                row["复核人"] = operator or row.get("复核人") or "值班管理员"
                snapshot = self._publish(batch_id=None, source_snapshot=self._build_candidate)
            except AggregateError as exc:
                # 重算未成功：回滚核验结论，旧统计保持不变
                row.clear()
                row.update(backup)
                return None, str(exc), copy.deepcopy(self._snapshot) if self._snapshot else None
            else:
                self._review_seq += 1
                snapshot["review_seq"] = self._review_seq
                if self._snapshot is not None:
                    self._snapshot["review_seq"] = self._review_seq
                entry = self.find_point(entry_id)
                return entry, f"控制点 {row.get('点号')} 点类型复核已生效，看板/台账/点位图已同步", snapshot

    # ------------------------------------------------------------------ 读端
    def current_snapshot(self, *, replayed: bool = False, batch_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if self._snapshot is None:
                return self.rebuild()
            snapshot = copy.deepcopy(self._snapshot)
            if replayed:
                snapshot["replayed"] = True
            if batch_id is not None:
                snapshot["batch_id"] = batch_id
            return snapshot

    def ensure_snapshot(self) -> dict[str, Any]:
        with self._lock:
            if self._snapshot is None:
                return self.rebuild()
            return self.current_snapshot()

    def stale_snapshot(self) -> dict[str, Any] | None:
        """最近一次成功的聚合快照；重算失败时读端应继续展示它而不是清零。"""
        with self._lock:
            return copy.deepcopy(self._snapshot) if self._snapshot else None

    def find_point(self, entry_id: int) -> dict[str, Any] | None:
        snapshot = self.ensure_snapshot()
        for point in snapshot["points"]:
            if entry_id in point.get("_source_ids", []):
                return copy.deepcopy(point)
        return None

    def list_points(
        self,
        *,
        keyword: str | None = None,
        point_type: str | None = None,
        review: str | None = None,
        sheet: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """控制点台账：读统一聚合快照，保证与看板/点位图同源同版本。"""
        snapshot = self.ensure_snapshot()
        rows = list(snapshot["points"])
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("点号", ""))]
        if point_type:
            rows = [row for row in rows if row.get("点类型") == point_type]
        if review:
            rows = [row for row in rows if row.get("复核状态") == review]
        if sheet:
            rows = [row for row in rows if row.get("图幅编号") == sheet]
        total = len(rows)
        start = max(page - 1, 0) * size
        return copy.deepcopy(rows[start:start + size]), total


# 模块级单例：路由层共享同一份聚合状态与锁
service = SurveyControlService()
