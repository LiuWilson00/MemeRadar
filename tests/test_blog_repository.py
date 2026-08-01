"""blog_posts 存取層 + slug 生成。"""

from __future__ import annotations

from memeradar.blog import repository as blog_repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import new_id


def _conn(tmp_path):
    conn = connect(tmp_path / "db.sqlite3")
    migrate(conn)
    return conn


def _make(conn, *, status="draft", meme_id=None, title="測試標題", featured_on="2026-08-02"):
    return blog_repo.create_post(
        conn, post_id=new_id("post"), meme_id=meme_id or new_id("m"),
        title=title, article_html="<p>內文</p>", status=status,
        verdict="identified", confidence=0.9, origin={"work": "九品芝麻官"},
        caption_is_original=True, caption_note="原台詞",
        sources=[{"title": "來源", "url": "https://example.com", "supports": "出處"}],
        unverified_claims=[], model_version="research-v1@sonnet", cost_usd=0.08,
        featured_on=featured_on)


class TestRoundTrip:
    def test_json_fields_survive_round_trip(self, tmp_path):
        conn = _conn(tmp_path)
        post = _make(conn)
        got = blog_repo.get_post(conn, post["post_id"])
        assert got["origin"] == {"work": "九品芝麻官"}
        assert got["sources"][0]["url"] == "https://example.com"
        assert got["unverified_claims"] == []
        assert got["caption_is_original"] is True  # SQLite 存 0/1，讀回要是 bool

    def test_caption_unknown_stays_none(self, tmp_path):
        conn = _conn(tmp_path)
        p = blog_repo.create_post(
            conn, post_id=new_id("post"), meme_id=new_id("m"), title="t",
            article_html="<p>x</p>", status="draft", caption_is_original=None)
        assert blog_repo.get_post(conn, p["post_id"])["caption_is_original"] is None


class TestSlug:
    def test_slug_keeps_chinese_and_is_unique_per_meme(self):
        a = blog_repo.make_slug("九品芝麻官：打我呀笨蛋", "m_aaaaaa111111")
        b = blog_repo.make_slug("九品芝麻官：打我呀笨蛋", "m_bbbbbb222222")
        assert "九品芝麻官" in a and a != b

    def test_empty_title_still_produces_usable_slug(self):
        assert blog_repo.make_slug("", "m_abc123").endswith("abc123")


class TestPublishing:
    def test_published_post_gets_timestamp(self, tmp_path):
        conn = _conn(tmp_path)
        post = _make(conn, status="draft")
        assert post["published_at"] is None
        after = blog_repo.set_status(conn, post["post_id"], "published")
        assert after["status"] == "published" and after["published_at"]

    def test_list_defaults_to_published_only(self, tmp_path):
        conn = _conn(tmp_path)
        _make(conn, status="draft")
        pub = _make(conn, status="published")
        listed = blog_repo.list_posts(conn)
        assert [p["post_id"] for p in listed] == [pub["post_id"]]
        assert len(blog_repo.list_posts(conn, status=None)) == 2


class TestSchedulerSupport:
    def test_get_post_for_day_finds_todays_post(self, tmp_path):
        conn = _conn(tmp_path)
        _make(conn, featured_on="2026-08-01")
        today = _make(conn, featured_on="2026-08-02")
        assert blog_repo.get_post_for_day(conn, "2026-08-02")["post_id"] == today["post_id"]
        assert blog_repo.get_post_for_day(conn, "2026-08-03") is None

    def test_featured_ids_include_drafts_and_rejected(self, tmp_path):
        """草稿與退稿也要算寫過——否則同一張圖會被重選、重付一次調研費。"""
        conn = _conn(tmp_path)
        d = _make(conn, status="draft")
        r = _make(conn, status="rejected")
        assert blog_repo.featured_meme_ids(conn) == {d["meme_id"], r["meme_id"]}
