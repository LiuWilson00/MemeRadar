"""P3-1 測試：Adapter 框架 + Reddit adapter（規格：docs/02 §2–§3、§6）。"""

import pytest

from memeradar.ingestion.base import Candidate
from memeradar.ingestion.reddit import RawPost, RedditAdapter
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate


class FakeRedditClient:
    def __init__(self, posts: dict[str, list[RawPost]], comments: dict[str, list[str]]):
        self.posts = posts
        self.comments = comments
        self.comment_calls: list[str] = []

    def list_new(self, subreddit: str, limit: int) -> list[RawPost]:
        return self.posts.get(subreddit, [])[:limit]

    def top_comments(self, post: RawPost, n: int) -> list[str]:
        self.comment_calls.append(post.post_id)
        return self.comments.get(post.post_id, [])[:n]


def post(
    post_id: str,
    *,
    created_utc: float,
    url: str = "https://i.redd.it/x.png",
    score: int = 500,
    gallery_urls: list[str] | None = None,
) -> RawPost:
    return RawPost(
        post_id=post_id,
        title=f"標題 {post_id}",
        permalink=f"/r/memes/comments/{post_id}/",
        url=url,
        score=score,
        created_utc=created_utc,
        gallery_urls=gallery_urls,
    )


class TestRedditAdapter:
    def test_outputs_unified_candidate_schema(self):
        client = FakeRedditClient(
            {"memes": [post("p1", created_utc=1_800_000_100)]},
            {"p1": ["笑死", "已存", "太真實"]},
        )
        adapter = RedditAdapter(client, subreddits=["memes"], comments_top_n=2)

        candidates, watermark = adapter.fetch(None)

        assert len(candidates) == 1
        c = candidates[0]
        assert isinstance(c, Candidate)
        assert c.platform == "reddit"
        assert c.post_id == "p1"
        assert c.post_url == "https://www.reddit.com/r/memes/comments/p1/"
        assert c.post_title == "標題 p1"
        assert c.top_comments == ["笑死", "已存"]  # comments_top_n=2
        assert c.upvotes == 500
        assert c.images == [{"url": "https://i.redd.it/x.png", "order": 0}]
        assert c.posted_at.startswith("2027-")  # epoch 1.8e9 → ISO
        assert watermark is not None

    def test_watermark_filters_old_posts_and_advances(self):
        client = FakeRedditClient(
            {"memes": [
                post("new", created_utc=2000.0),
                post("old", created_utc=1000.0),
                post("same", created_utc=1500.0),
            ]},
            {},
        )
        adapter = RedditAdapter(client, subreddits=["memes"])
        old_watermark = RedditAdapter.watermark_from_epoch(1500.0)

        candidates, new_watermark = adapter.fetch(old_watermark)

        assert [c.post_id for c in candidates] == ["new"]  # 舊於或等於水位者排除
        assert new_watermark == RedditAdapter.watermark_from_epoch(2000.0)

    def test_no_new_posts_keeps_watermark(self):
        client = FakeRedditClient({"memes": [post("old", created_utc=1000.0)]}, {})
        adapter = RedditAdapter(client, subreddits=["memes"])
        watermark = RedditAdapter.watermark_from_epoch(1500.0)

        candidates, new_watermark = adapter.fetch(watermark)

        assert candidates == []
        assert new_watermark == watermark

    def test_min_score_skips_and_saves_comment_calls(self):
        client = FakeRedditClient(
            {"memes": [
                post("hot", created_utc=2000.0, score=500),
                post("cold", created_utc=2000.0, score=3),
            ]},
            {},
        )
        adapter = RedditAdapter(client, subreddits=["memes"], min_score=100)

        candidates, _ = adapter.fetch(None)

        assert [c.post_id for c in candidates] == ["hot"]
        assert client.comment_calls == ["hot"]  # 低分貼文不浪費留言請求

    def test_gallery_post_yields_ordered_images(self):
        gallery = ["https://i.redd.it/a.jpg", "https://i.redd.it/b.jpg"]
        client = FakeRedditClient(
            {"memes": [post("g1", created_utc=2000.0, url="https://reddit.com/gallery/g1",
                            gallery_urls=gallery)]},
            {},
        )
        adapter = RedditAdapter(client, subreddits=["memes"])

        candidates, _ = adapter.fetch(None)

        assert candidates[0].images == [
            {"url": "https://i.redd.it/a.jpg", "order": 0},
            {"url": "https://i.redd.it/b.jpg", "order": 1},
        ]

    def test_non_image_posts_skipped(self):
        client = FakeRedditClient(
            {"memes": [
                post("video", created_utc=2000.0, url="https://v.redd.it/xyz"),
                post("link", created_utc=2000.0, url="https://example.com/article"),
                post("img", created_utc=2000.0, url="https://i.redd.it/ok.webp"),
            ]},
            {},
        )
        adapter = RedditAdapter(client, subreddits=["memes"])

        candidates, _ = adapter.fetch(None)

        assert [c.post_id for c in candidates] == ["img"]

    def test_multiple_subreddits_merged(self):
        client = FakeRedditClient(
            {
                "memes": [post("m1", created_utc=2000.0)],
                "dankmemes": [post("d1", created_utc=3000.0)],
            },
            {},
        )
        adapter = RedditAdapter(client, subreddits=["memes", "dankmemes"])

        candidates, watermark = adapter.fetch(None)

        assert {c.post_id for c in candidates} == {"m1", "d1"}
        assert watermark == RedditAdapter.watermark_from_epoch(3000.0)


class TestWatermarkPersistence:
    @pytest.fixture
    def conn(self, tmp_path):
        c = connect(tmp_path / "db.sqlite3")
        migrate(c)
        yield c
        c.close()

    def test_get_set_roundtrip_and_upsert(self, conn):
        assert repo.get_watermark(conn, "reddit") is None
        repo.set_watermark(conn, "reddit", "2026-07-11T00:00:00+00:00")
        assert repo.get_watermark(conn, "reddit") == "2026-07-11T00:00:00+00:00"
        repo.set_watermark(conn, "reddit", "2026-07-12T00:00:00+00:00")  # 覆蓋
        assert repo.get_watermark(conn, "reddit") == "2026-07-12T00:00:00+00:00"
