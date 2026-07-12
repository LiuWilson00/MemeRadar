"""P0-3 seed 配平統計（docs/06 §3.2、docs/TASKS.md P0-3）。

配額：每策略錨點 ≥ 8 張、海綿寶寶 / 甄嬛傳 ≥ 30 張、總量 150–300。
一張圖「覆蓋」某策略 = 任一 usage_hint 含該策略 label 或別名（子字串）。
只計 active 且 is_meme 的已標註圖。
"""

from __future__ import annotations

import pytest

from memeradar.ingestion.coverage import (
    FRANCHISE_TARGETS,
    STRATEGY_TARGET,
    build_coverage_report,
    format_coverage,
)
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Meme, MemeAnnotation, new_id
from memeradar.shared.repository import insert_meme, set_status, upsert_annotation


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


def seed(conn, *, usage_hints=(), franchise=None, categories=("其他",),
         status="active", is_meme=True):
    meme_id = new_id("m")
    insert_meme(conn, Meme(meme_id=meme_id, image_uri=f"{meme_id}.png", sha256=meme_id))
    upsert_annotation(conn, MemeAnnotation(
        meme_id=meme_id, model_version="v", is_meme=is_meme,
        ocr_text="", usage_hints=list(usage_hints),
        franchise=franchise, categories=list(categories),
    ))
    if status != "active":
        set_status(conn, meme_id, status)
    return meme_id


class TestStrategyCoverage:
    def test_counts_by_label_and_alias_substring(self, conn):
        seed(conn, usage_hints=["被罵時滑跪求饒"])
        seed(conn, usage_hints=["先滑跪再說"])  # 別名「滑跪」也算
        seed(conn, usage_hints=["對好消息表達慶祝"])

        report = build_coverage_report(conn)

        by_strategy = {row["label"]: row["count"] for row in report["strategies"]}
        assert by_strategy["滑跪求饒"] == 2
        assert by_strategy["慶祝"] == 1
        assert by_strategy["安撫"] == 0

    def test_one_meme_can_cover_multiple_strategies(self, conn):
        seed(conn, usage_hints=["自嘲化解尷尬", "被嗆時嗆聲反擊"])

        report = build_coverage_report(conn)

        by_strategy = {row["label"]: row["count"] for row in report["strategies"]}
        assert by_strategy["自嘲"] == 1
        assert by_strategy["嗆聲反擊"] == 1

    def test_unmatched_hints_bucketed(self, conn):
        seed(conn, usage_hints=["純粹放著好看"])

        report = build_coverage_report(conn)

        assert report["unmatched"] == 1

    def test_excludes_removed_pending_and_non_meme(self, conn):
        seed(conn, usage_hints=["安撫朋友"], status="removed")
        seed(conn, usage_hints=["安撫朋友"], status="pending_review")
        seed(conn, usage_hints=["安撫朋友"], is_meme=False)

        report = build_coverage_report(conn)

        by_strategy = {row["label"]: row["count"] for row in report["strategies"]}
        assert by_strategy["安撫"] == 0
        assert report["total"] == 0

    def test_gap_reflects_target(self, conn):
        for _ in range(3):
            seed(conn, usage_hints=["安撫朋友"])

        report = build_coverage_report(conn)

        comfort = next(r for r in report["strategies"] if r["label"] == "安撫")
        assert comfort["target"] == STRATEGY_TARGET
        assert comfort["gap"] == STRATEGY_TARGET - 3


class TestFranchiseCoverage:
    def test_alias_normalized_and_priority_targets(self, conn):
        seed(conn, usage_hints=["附和"], franchise="SpongeBob")
        seed(conn, usage_hints=["附和"], franchise="海綿寶寶")
        seed(conn, usage_hints=["附和"], franchise="甄嬛传")

        report = build_coverage_report(conn)

        franchises = {row["name"]: row for row in report["franchises"]}
        assert franchises["海綿寶寶"]["count"] == 2
        assert franchises["甄嬛傳"]["count"] == 1
        assert franchises["海綿寶寶"]["target"] == FRANCHISE_TARGETS["海綿寶寶"]
        assert franchises["甄嬛傳"]["gap"] == FRANCHISE_TARGETS["甄嬛傳"] - 1


class TestTotalsAndFormat:
    def test_totals_and_category_counts(self, conn):
        seed(conn, usage_hints=["安撫"], categories=["卡通動畫"])
        seed(conn, usage_hints=["拒絕"], categories=["動物"])

        report = build_coverage_report(conn)

        assert report["total"] == 2
        assert report["total_target"] == (150, 300)
        assert {c["label"]: c["count"] for c in report["categories"]}["卡通動畫"] == 1

    def test_format_marks_gaps(self, conn):
        seed(conn, usage_hints=["安撫朋友"])

        text = format_coverage(build_coverage_report(conn))

        assert "安撫" in text
        assert "缺" in text  # 未達標的錨點要點名缺口
        assert "海綿寶寶" in text
