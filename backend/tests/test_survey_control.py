"""测绘控制聚合线回归测试（仅依赖标准库，离线可跑）。

运行：
    cd backend && python3 tests/test_survey_control.py

覆盖：
- 跨图幅同一点号按最新签发坐标/责任组图幅归并，历史坐标按版本保留；
- 存量缺责任组点迁移补数且幂等；
- 幂等批次重建（同批次重放、分批变化重算）；
- 分段总数与点位去重数必须相符，不符则拒绝发布且不清零旧统计；
- 并发核验 CAS 只允许一个结论生效；
- 重算失败时核验结论回滚、成功统计保留。
"""
from __future__ import annotations

import copy
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.seed import SEED_ROWS  # noqa: E402
from app.services.survey_control import (  # noqa: E402
    AggregateError,
    ReviewConflict,
    SurveyControlService,
)


def fresh_rows():
    return copy.deepcopy(SEED_ROWS["survey_point"])


class SurveyControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = fresh_rows()
        self.svc = SurveyControlService(source=lambda: self.rows)
        self.snap = self.svc.rebuild(batch_size=3)

    def test_dedup_and_snapshot(self) -> None:
        stats = self.snap["stats"]
        self.assertEqual(stats["原始记录数"], 7)
        self.assertEqual(stats["控制点总数"], 6)
        self.assertEqual(stats["跨图幅点数"], 1)
        self.assertEqual(stats["迁移补数点数"], 1)
        self.assertEqual(sum(s["point_count"] for s in self.snap["segments"]), 6)

    def test_cross_sheet_latest_wins_history_kept(self) -> None:
        point = self.svc.find_point(2)
        assert point is not None
        self.assertEqual(point["图幅编号"], "H49G001001")  # 最新签发在青山幅
        self.assertEqual(point["责任组"], "一区控制组")
        self.assertEqual(point["最新坐标"]["坐标X"], 523300.50)
        self.assertEqual(point["签发版本"], 2)
        self.assertTrue(point["跨图幅"])
        self.assertEqual([v["版本"] for v in point["coordinate_versions"]], [1, 2])

    def test_legacy_group_migration_idempotent(self) -> None:
        point = self.svc.find_point(4)
        assert point is not None
        self.assertTrue(point["migrated"])
        self.assertTrue(point["责任组"])
        self.assertTrue(point["图幅编号"])
        self.assertEqual(self.svc.migrate_legacy(), 0)

    def test_idempotent_batches(self) -> None:
        replay = self.svc.rebuild(batch_size=3)
        self.assertTrue(replay.get("replayed"))
        changed = self.svc.rebuild(batch_size=100)
        self.assertFalse(changed.get("replayed"))
        self.assertTrue(self.svc.rebuild(batch_size=100).get("replayed"))

    def test_segment_mismatch_blocks_publish_and_keeps_stats(self) -> None:
        original = self.svc._build_candidate

        def poisoned():
            points, stats, segments, checks = original()
            segments[0]["point_count"] += 1
            checks = [c for c in checks if c["name"] != "分段总数与点位去重数相符"]
            checks.insert(0, {
                "name": "分段总数与点位去重数相符",
                "status": "失败",
                "detail": "poisoned",
            })
            return points, stats, segments, checks

        self.svc._build_candidate = poisoned
        before = copy.deepcopy(self.svc.stale_snapshot()["stats"])
        with self.assertRaises(AggregateError):
            self.svc.rebuild()
        self.assertEqual(self.svc.stale_snapshot()["stats"], before)

    def test_review_cas_and_sync_snapshot(self) -> None:
        entry, _, snapshot = self.svc.review_point(3, point_type="导线点", expected_seq=0)
        assert entry is not None
        self.assertEqual(entry["点类型"], "导线点")
        self.assertEqual(snapshot["stats"]["按点类型"]["导线点"], 2)
        with self.assertRaises(ReviewConflict):
            self.svc.review_point(1, point_type="图根点", expected_seq=0)

    def test_concurrent_review_single_winner(self) -> None:
        barrier = threading.Barrier(5)
        outcomes: list[bool] = []

        def worker(idx: int) -> None:
            barrier.wait()
            try:
                entry, _, _ = self.svc.review_point(
                    3,
                    point_type="导线点" if idx % 2 else "三角点",
                    expected_seq=0,
                )
                outcomes.append(entry is not None)
            except ReviewConflict:
                outcomes.append(False)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(outcomes), 1)

    def test_failed_recompute_rolls_review_back(self) -> None:
        original = self.svc._build_candidate

        def poisoned():
            points, stats, segments, checks = original()
            segments[0]["point_count"] += 1
            checks = [c for c in checks if c["name"] != "分段总数与点位去重数相符"]
            checks.insert(0, {
                "name": "分段总数与点位去重数相符",
                "status": "失败",
                "detail": "poisoned",
            })
            return points, stats, segments, checks

        self.svc._build_candidate = poisoned
        before = copy.deepcopy(self.svc.stale_snapshot()["stats"])
        entry, _, stale = self.svc.review_point(7, point_type="水准点")
        self.assertIsNone(entry)
        self.assertEqual(self.rows[6]["点类型"], "导线点")
        self.assertEqual(self.rows[6]["复核状态"], "待复核")
        self.assertEqual(stale["stats"], before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
