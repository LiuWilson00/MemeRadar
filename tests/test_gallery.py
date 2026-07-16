"""探索圖庫：按讚 / 彈幕留言 / 暱稱 / 讚併入風雲榜。"""

from __future__ import annotations

import pytest

from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Meme, MemeAnnotation, new_id


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


def _meme(conn, ocr="圖", *, nsfw=0, is_meme=1, status="active", w=100, h=200) -> Meme:
    m = Meme(meme_id=new_id("m"), image_uri=f"images/{new_id('x')}.png",
             sha256=new_id("h").ljust(64, "0")[:64], width=w, height=h, status=status)
    repo.insert_meme(conn, m)
    repo.upsert_annotation(conn, MemeAnnotation(
        meme_id=m.meme_id, model_version="v", ocr_text=ocr, franchise="海綿寶寶",
        emotions=["無奈"], usage_hints=["用途"], categories=["卡通動畫"],
        is_meme=bool(is_meme), nsfw=bool(nsfw)))
    return m


class TestLikes:
    def test_toggle_like_adds_then_removes(self, conn):
        m = _meme(conn)
        assert repo.toggle_like(conn, m.meme_id, "c1") == {"likes": 1, "liked": True}
        assert repo.toggle_like(conn, m.meme_id, "c1") == {"likes": 0, "liked": False}

    def test_distinct_clients_count(self, conn):
        m = _meme(conn)
        repo.toggle_like(conn, m.meme_id, "c1")
        repo.toggle_like(conn, m.meme_id, "c2")
        repo.toggle_like(conn, m.meme_id, "c1")  # c1 取消
        assert repo.toggle_like(conn, m.meme_id, "c3")["likes"] == 2  # c2, c3

    def test_concurrent_double_like_does_not_error(self, conn):
        """並發雙擊競態：兩個請求都判定「沒讚過」→ 都插入同一 (meme, client)。
        toggle_like 的 INSERT 帶 ON CONFLICT，第二次不炸（原本 UniqueViolation→500）。
        連下兩次插入模擬競態，驗證約束被吸收、最終只有一筆。"""
        m = _meme(conn)
        sql = (
            "INSERT INTO meme_likes (meme_id, client_id, created_at) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING"
        )
        conn.execute(sql, (m.meme_id, "c1", "2026-01-01T00:00:00Z"))
        conn.execute(sql, (m.meme_id, "c1", "2026-01-01T00:00:00Z"))  # 不應拋
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM meme_likes WHERE meme_id = %s", (m.meme_id,)
        ).fetchone()["n"]
        assert n == 1


class TestGalleryList:
    def test_only_active_non_nsfw_real_memes(self, conn):
        a = _meme(conn, "A")
        _meme(conn, "N", nsfw=1)
        _meme(conn, "P", status="pending_review")
        _meme(conn, "X", is_meme=0)
        items = repo.list_gallery(conn, seed="s", offset=0, limit=20, client_id="c1")
        assert [i["meme_id"] for i in items] == [a.meme_id]
        assert items[0]["width"] == 100 and items[0]["height"] == 200

    def test_counts_and_liked_flag(self, conn):
        m = _meme(conn)
        repo.toggle_like(conn, m.meme_id, "c1")
        repo.toggle_like(conn, m.meme_id, "c2")
        repo.add_comment(conn, m.meme_id, "c9", "路人", "笑死")
        mine = repo.list_gallery(conn, seed="s", offset=0, limit=20, client_id="c1")[0]
        assert mine["likes"] == 2 and mine["comments"] == 1 and mine["liked"] is True
        other = repo.list_gallery(conn, seed="s", offset=0, limit=20, client_id="cX")[0]
        assert other["liked"] is False

    def test_seed_pagination_stable_no_overlap(self, conn):
        for i in range(5):
            _meme(conn, f"m{i}")
        p1 = repo.list_gallery(conn, seed="fixed", offset=0, limit=2, client_id="c")
        p2 = repo.list_gallery(conn, seed="fixed", offset=2, limit=2, client_id="c")
        ids = [i["meme_id"] for i in p1 + p2]
        assert len(ids) == len(set(ids)) == 4
        p1b = repo.list_gallery(conn, seed="fixed", offset=0, limit=2, client_id="c")
        assert [i["meme_id"] for i in p1] == [i["meme_id"] for i in p1b]


class TestComments:
    def test_add_list_edit_delete_owner(self, conn):
        m = _meme(conn)
        c = repo.add_comment(conn, m.meme_id, "c1", "臭臭束褲", "笑死我了")
        got = repo.list_comments(conn, m.meme_id, client_id="c1")
        assert len(got) == 1 and got[0]["text"] == "笑死我了"
        assert got[0]["mine"] is True and got[0]["edited"] is False
        assert repo.list_comments(conn, m.meme_id, client_id="cX")[0]["mine"] is False
        assert repo.update_comment(conn, c["comment_id"], "c1", "改一下") is True
        after = repo.list_comments(conn, m.meme_id, "c1")[0]
        assert after["text"] == "改一下" and after["edited"] is True
        assert repo.update_comment(conn, c["comment_id"], "cX", "亂改") is False
        assert repo.delete_comment(conn, c["comment_id"], "cX") is False
        assert repo.delete_comment(conn, c["comment_id"], "c1") is True
        assert repo.list_comments(conn, m.meme_id, "c1") == []


class TestLeaderboardWithGalleryLikes:
    def test_gallery_likes_count_toward_score(self, conn):
        m = _meme(conn, "熱門")
        repo.toggle_like(conn, m.meme_id, "c1")
        repo.toggle_like(conn, m.meme_id, "c2")
        board = repo.leaderboard(conn, limit=10)
        assert board[0]["meme_id"] == m.meme_id
        assert board[0]["likes"] == 2 and board[0]["score"] == 6  # 2×3


class TestNickname:
    def test_set_and_roundtrip(self, conn):
        u = repo.upsert_user(conn, google_sub="g1", email="a@x", name="A", picture="")
        repo.set_user_nickname(conn, u["user_id"], "邪惡飛魚")
        assert repo.get_user(conn, u["user_id"])["nickname"] == "邪惡飛魚"
