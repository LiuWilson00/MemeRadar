"""P4-1 測試：回饋報表聚合（規格：docs/05 §2.2、docs/06 §3.6、docs/04 §6）。"""

import pytest

from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import (
    FeedbackEvent,
    Meme,
    MemeAnnotation,
    RecommendationLog,
    new_id,
)
from memeradar.shared.reporting import build_feedback_report


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


def seed_meme(conn, *, ocr: str, franchise: str) -> Meme:
    meme = Meme(meme_id=new_id("m"), image_uri="x.png", sha256=new_id("h").ljust(64, "0")[:64])
    repo.insert_meme(conn, meme)
    repo.upsert_annotation(conn, MemeAnnotation(
        meme_id=meme.meme_id, model_version="v", ocr_text=ocr, franchise=franchise,
        emotions=["無奈"], usage_hints=["用途"], categories=["卡通動畫"],
    ))
    return meme


def seed_query(conn, memes_with_strategy: list[tuple[Meme, str]], *,
               created_at: str, diversity: float = 0.5) -> RecommendationLog:
    log = RecommendationLog(
        query_id=new_id("q"),
        conversation=[{"speaker": "other", "text": "你報告又遲交了！"}],
        intent_result={"summary": "同事指責遲交", "punchline": "行不行"},
        params_snapshot={"params": {"top_n": 5, "min_similarity": 0.35,
                                    "diversity": diversity, "hotness_weight": 0.1}},
        final_results=[
            {"meme_id": m.meme_id, "rank": i + 1, "matched_strategy": strategy}
            for i, (m, strategy) in enumerate(memes_with_strategy)
        ],
        created_at=created_at,
    )
    repo.insert_recommendation_log(conn, log)
    return log


def feedback(conn, log, meme, *, rating: str, rank: int = 1, note: str | None = None,
             created_at: str | None = None) -> None:
    event = FeedbackEvent(
        feedback_id=new_id("f"), query_id=log.query_id, meme_id=meme.meme_id,
        rank=rank, rating=rating, note=note,
    )
    if created_at:
        event.created_at = created_at
    repo.insert_feedback(conn, event)


class TestFeedbackReport:
    def test_totals_and_up_rate(self, conn):
        # 回饋對每組 query+meme 冪等 → 三張不同梗圖各一票（up/up/down）
        m1 = seed_meme(conn, ocr="甲", franchise="海綿寶寶")
        m2 = seed_meme(conn, ocr="乙", franchise="海綿寶寶")
        m3 = seed_meme(conn, ocr="丙", franchise="海綿寶寶")
        log = seed_query(conn, [(m1, "自嘲"), (m2, "自嘲"), (m3, "自嘲")],
                         created_at="2026-07-10T10:00:00+00:00")
        feedback(conn, log, m1, rating="up")
        feedback(conn, log, m2, rating="up")
        feedback(conn, log, m3, rating="down")

        report = build_feedback_report(conn)

        assert report["totals"] == {"ups": 2, "downs": 1, "total": 3,
                                    "up_rate": pytest.approx(2 / 3)}
        assert report["queries_with_feedback"] == 1

    def test_daily_trend_buckets(self, conn):
        m1 = seed_meme(conn, ocr="甲", franchise="海綿寶寶")
        m2 = seed_meme(conn, ocr="乙", franchise="海綿寶寶")
        m3 = seed_meme(conn, ocr="丙", franchise="海綿寶寶")
        log = seed_query(conn, [(m1, "自嘲"), (m2, "自嘲"), (m3, "自嘲")],
                         created_at="2026-07-10T10:00:00+00:00")
        feedback(conn, log, m1, rating="up", created_at="2026-07-10T11:00:00+00:00")
        feedback(conn, log, m2, rating="down", created_at="2026-07-11T09:00:00+00:00")
        feedback(conn, log, m3, rating="up", created_at="2026-07-11T10:00:00+00:00")

        report = build_feedback_report(conn)

        assert report["daily"] == [
            {"date": "2026-07-10", "ups": 1, "downs": 0},
            {"date": "2026-07-11", "ups": 1, "downs": 1},
        ]

    def test_group_by_strategy_franchise_and_rank(self, conn):
        sponge = seed_meme(conn, ocr="我就爛", franchise="海綿寶寶")
        zhen = seed_meme(conn, ocr="臣妾做不到", franchise="甄嬛傳")
        log = seed_query(conn, [(sponge, "自嘲"), (zhen, "滑跪求饒")],
                         created_at="2026-07-10T10:00:00+00:00")
        feedback(conn, log, sponge, rating="up", rank=1)
        feedback(conn, log, zhen, rating="down", rank=2)

        report = build_feedback_report(conn)

        by_strategy = {r["key"]: r for r in report["by_strategy"]}
        assert by_strategy["自嘲"]["ups"] == 1 and by_strategy["自嘲"]["downs"] == 0
        assert by_strategy["滑跪求饒"]["downs"] == 1

        by_franchise = {r["key"]: r for r in report["by_franchise"]}
        assert by_franchise["海綿寶寶"]["up_rate"] == pytest.approx(1.0)
        assert by_franchise["甄嬛傳"]["up_rate"] == pytest.approx(0.0)

        by_rank = {r["key"]: r for r in report["by_rank"]}
        assert by_rank[1]["ups"] == 1
        assert by_rank[2]["downs"] == 1

    def test_group_by_params(self, conn):
        sponge = seed_meme(conn, ocr="我就爛", franchise="海綿寶寶")
        low = seed_query(conn, [(sponge, "自嘲")],
                         created_at="2026-07-10T10:00:00+00:00", diversity=0.0)
        high = seed_query(conn, [(sponge, "自嘲")],
                          created_at="2026-07-10T11:00:00+00:00", diversity=0.8)
        feedback(conn, low, sponge, rating="up")
        feedback(conn, high, sponge, rating="down")

        report = build_feedback_report(conn)

        keys = {r["key"] for r in report["by_params"]}
        assert any("div=0.0" in k for k in keys)
        assert any("div=0.8" in k for k in keys)

    def test_down_notes_for_manual_attribution(self, conn):
        sponge = seed_meme(conn, ocr="我就爛", franchise="海綿寶寶")
        m2 = seed_meme(conn, ocr="乙", franchise="海綿寶寶")
        m3 = seed_meme(conn, ocr="丙", franchise="海綿寶寶")
        log = seed_query(conn, [(sponge, "自嘲"), (m2, "看戲"), (m3, "附和")],
                         created_at="2026-07-10T10:00:00+00:00")
        feedback(conn, log, sponge, rating="down", note="梗太老了")
        feedback(conn, log, m2, rating="down")  # 無備註者不列
        feedback(conn, log, m3, rating="up", note="讚")  # 👍 備註不列

        report = build_feedback_report(conn)

        assert len(report["down_notes"]) == 1
        note = report["down_notes"][0]
        assert note["note"] == "梗太老了"
        assert note["meme_ocr"] == "我就爛"
        assert note["matched_strategy"] == "自嘲"
        assert note["intent_summary"] == "同事指責遲交"

    def test_empty_db_returns_zeroes(self, conn):
        report = build_feedback_report(conn)
        assert report["totals"]["total"] == 0
        assert report["totals"]["up_rate"] is None
        assert report["daily"] == []
        assert report["down_notes"] == []
