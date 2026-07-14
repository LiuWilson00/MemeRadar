"""行為事件 log + 排行榜（讚×3 + 下載）。"""

from __future__ import annotations

import pytest

from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import FeedbackEvent, Meme, MemeAnnotation, RecommendationLog, new_id


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


def _meme(conn, ocr: str, franchise: str = "海綿寶寶") -> Meme:
    m = Meme(meme_id=new_id("m"), image_uri=f"images/{new_id('x')}.png",
             sha256=new_id("h").ljust(64, "0")[:64])
    repo.insert_meme(conn, m)
    repo.upsert_annotation(conn, MemeAnnotation(
        meme_id=m.meme_id, model_version="v", ocr_text=ocr, franchise=franchise,
        emotions=["無奈"], usage_hints=["用途"], categories=["卡通動畫"]))
    return m


def _like(conn, meme: Meme) -> None:
    log = RecommendationLog(query_id=new_id("q"), conversation=[], params_snapshot={},
                            final_results=[{"meme_id": meme.meme_id, "rank": 1}])
    repo.insert_recommendation_log(conn, log)
    repo.insert_feedback(conn, FeedbackEvent(
        feedback_id=new_id("f"), query_id=log.query_id, meme_id=meme.meme_id, rank=1, rating="up"))


class TestEventsAndLeaderboard:
    def test_insert_event_roundtrip(self, conn):
        m = _meme(conn, "我就爛")
        repo.insert_event(conn, "download", client_id="c1", meme_id=m.meme_id,
                          meta={"src": "mobile"})
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE event_type='download'").fetchone()["n"]
        assert n == 1

    def test_leaderboard_scores_and_orders(self, conn):
        a = _meme(conn, "A圖")
        b = _meme(conn, "B圖")
        _meme(conn, "C圖")  # 無互動 → 不上榜
        _like(conn, a)  # A：1 讚
        repo.insert_event(conn, "download", meme_id=a.meme_id)  # A：2 下載
        repo.insert_event(conn, "download", meme_id=a.meme_id)
        for _ in range(3):
            repo.insert_event(conn, "download", meme_id=b.meme_id)  # B：3 下載

        board = repo.leaderboard(conn, limit=10)

        # A = 讚1×3 + 下載2 = 5；B = 下載3 = 3；C 無互動不列
        assert [r["meme_id"] for r in board] == [a.meme_id, b.meme_id]
        assert board[0]["likes"] == 1 and board[0]["downloads"] == 2 and board[0]["score"] == 5
        assert board[1]["score"] == 3
        assert board[0]["ocr_text"] == "A圖" and board[0]["franchise"] == "海綿寶寶"

    def test_leaderboard_empty_when_no_engagement(self, conn):
        _meme(conn, "沒人理")
        assert repo.leaderboard(conn) == []
