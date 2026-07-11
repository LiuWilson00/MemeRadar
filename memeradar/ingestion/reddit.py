"""P3-1 Reddit adapter（docs/02 §2 資料源 P1）。

- 水位：貼文建立時間（epoch → ISO），只收嚴格新於水位者。
- 節流禮儀（docs/02 §6）：低分貼文先過門檻再抓留言，省 API 請求。
- 雙客戶端：
  * :class:`PrawRedditClient` —— 官方 OAuth API（PRAW），憑證自 .env
    （``REDDIT_CLIENT_ID`` / ``REDDIT_CLIENT_SECRET``），需安裝 extras
    ``pip install -e ".[crawler]"``。
  * :class:`PublicJsonRedditClient` —— 公開 .json 端點（免憑證、明示 UA、
    固定延遲），開發驗證與低頻抓取用。
- CLI：``python -m memeradar.ingestion.reddit --subreddit memes --limit 25``
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from memeradar.ingestion.base import Candidate

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
DEFAULT_SUBREDDITS = ["memes", "dankmemes"]
DEFAULT_MIN_SCORE = 100  # docs/02 §5 來源別互動門檻
DEFAULT_COMMENTS_TOP_N = 5
PUBLIC_REQUEST_DELAY_SECONDS = 2.0  # docs/02 §6 寧慢勿封


@dataclass(frozen=True)
class RawPost:
    """客戶端層的原始貼文（與 PRAW / JSON 端點解耦）。"""

    post_id: str
    title: str
    permalink: str
    url: str
    score: int
    created_utc: float
    gallery_urls: list[str] | None = None


class RedditClient(Protocol):
    def list_new(self, subreddit: str, limit: int) -> list[RawPost]: ...

    def top_comments(self, post: RawPost, n: int) -> list[str]: ...


class RedditAdapter:
    name = "reddit"

    def __init__(
        self,
        client: RedditClient,
        *,
        subreddits: list[str] | None = None,
        min_score: int = DEFAULT_MIN_SCORE,
        comments_top_n: int = DEFAULT_COMMENTS_TOP_N,
        listing_limit: int = 100,
    ):
        self._client = client
        self._subreddits = subreddits or DEFAULT_SUBREDDITS
        self._min_score = min_score
        self._comments_top_n = comments_top_n
        self._listing_limit = listing_limit

    @staticmethod
    def watermark_from_epoch(epoch: float) -> str:
        return datetime.fromtimestamp(epoch, tz=UTC).isoformat(timespec="seconds")

    @staticmethod
    def _epoch_from_watermark(watermark: str) -> float:
        return datetime.fromisoformat(watermark).timestamp()

    @staticmethod
    def _images_of(post: RawPost) -> list[dict]:
        if post.gallery_urls:
            return [{"url": url, "order": i} for i, url in enumerate(post.gallery_urls)]
        if post.url.lower().split("?")[0].endswith(IMAGE_EXTENSIONS):
            return [{"url": post.url, "order": 0}]
        return []

    def fetch(self, watermark: str | None) -> tuple[list[Candidate], str | None]:
        since_epoch = self._epoch_from_watermark(watermark) if watermark else float("-inf")
        newest_epoch = since_epoch

        candidates: list[Candidate] = []
        for subreddit in self._subreddits:
            for post in self._client.list_new(subreddit, self._listing_limit):
                if post.created_utc <= since_epoch:
                    continue
                newest_epoch = max(newest_epoch, post.created_utc)
                if post.score < self._min_score:
                    continue
                images = self._images_of(post)
                if not images:
                    continue
                candidates.append(
                    Candidate(
                        platform="reddit",
                        post_id=post.post_id,
                        post_url=f"https://www.reddit.com{post.permalink}",
                        post_title=post.title,
                        top_comments=self._client.top_comments(post, self._comments_top_n),
                        upvotes=post.score,
                        posted_at=self.watermark_from_epoch(post.created_utc),
                        images=images,
                    )
                )

        if newest_epoch == float("-inf"):
            return candidates, watermark
        new_watermark = self.watermark_from_epoch(newest_epoch) if newest_epoch > since_epoch \
            else watermark
        return candidates, new_watermark


# ── 客戶端實作 ────────────────────────────────────────────────────────


def _gallery_urls_from_media(data: dict) -> list[str] | None:
    """由貼文 JSON 的 gallery 結構取出圖片網址（PRAW 與 .json 端點結構相同）。"""
    if not data.get("is_gallery"):
        return None
    order = [item.get("media_id") for item in data.get("gallery_data", {}).get("items", [])]
    media = data.get("media_metadata") or {}
    urls = []
    for media_id in order:
        entry = media.get(media_id) or {}
        source = entry.get("s") or {}
        url = source.get("u") or source.get("gif")
        if url:
            urls.append(url.replace("&amp;", "&"))
    return urls or None


class PublicJsonRedditClient:
    """公開 .json 端點（免憑證）。明示 UA、每請求固定延遲（docs/02 §6）。"""

    def __init__(self, user_agent: str, *, delay_seconds: float = PUBLIC_REQUEST_DELAY_SECONDS):
        import httpx

        self._http = httpx.Client(
            headers={"User-Agent": user_agent}, timeout=20, follow_redirects=True
        )
        self._delay = delay_seconds

    def _get(self, url: str, params: dict) -> dict:
        time.sleep(self._delay)
        response = self._http.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def list_new(self, subreddit: str, limit: int) -> list[RawPost]:
        body = self._get(f"https://www.reddit.com/r/{subreddit}/new.json", {"limit": limit})
        posts = []
        for child in body.get("data", {}).get("children", []):
            data = child.get("data", {})
            posts.append(
                RawPost(
                    post_id=data["id"],
                    title=data.get("title", ""),
                    permalink=data.get("permalink", ""),
                    url=data.get("url", ""),
                    score=int(data.get("score", 0)),
                    created_utc=float(data.get("created_utc", 0.0)),
                    gallery_urls=_gallery_urls_from_media(data),
                )
            )
        return posts

    def top_comments(self, post: RawPost, n: int) -> list[str]:
        body = self._get(
            f"https://www.reddit.com{post.permalink}.json",
            {"limit": n, "sort": "top", "depth": 1},
        )
        try:
            children = body[1]["data"]["children"]
        except (IndexError, KeyError, TypeError):
            return []
        comments = [
            c.get("data", {}).get("body", "").strip()
            for c in children
            if c.get("kind") == "t1"
        ]
        return [c for c in comments if c][:n]


class PrawRedditClient:
    """官方 OAuth API（PRAW）。需 extras ``[crawler]`` 與 .env 憑證。"""

    def __init__(self, client_id: str, client_secret: str, user_agent: str):
        try:
            import praw
        except ImportError as exc:
            raise RuntimeError(
                'Reddit 官方 API 需要 praw：請執行 pip install -e ".[crawler]"'
            ) from exc
        self._reddit = praw.Reddit(
            client_id=client_id, client_secret=client_secret, user_agent=user_agent
        )
        self._reddit.read_only = True

    def list_new(self, subreddit: str, limit: int) -> list[RawPost]:
        posts = []
        for submission in self._reddit.subreddit(subreddit).new(limit=limit):
            gallery = None
            if getattr(submission, "is_gallery", False):
                gallery = _gallery_urls_from_media(
                    {
                        "is_gallery": True,
                        "gallery_data": getattr(submission, "gallery_data", {}) or {},
                        "media_metadata": getattr(submission, "media_metadata", {}) or {},
                    }
                )
            posts.append(
                RawPost(
                    post_id=submission.id,
                    title=submission.title,
                    permalink=submission.permalink,
                    url=submission.url,
                    score=submission.score,
                    created_utc=submission.created_utc,
                    gallery_urls=gallery,
                )
            )
        return posts

    def top_comments(self, post: RawPost, n: int) -> list[str]:
        submission = self._reddit.submission(id=post.post_id)
        submission.comment_sort = "top"
        submission.comments.replace_more(limit=0)
        return [c.body.strip() for c in submission.comments[:n] if c.body.strip()]


def build_client(kind: str) -> RedditClient:
    from memeradar.shared.config import get_settings

    settings = get_settings()
    user_agent = "MemeRadar/0.1 (meme research tool)"
    if kind == "praw":
        return PrawRedditClient(
            settings.require("reddit_client_id"),
            settings.require("reddit_client_secret"),
            user_agent,
        )
    return PublicJsonRedditClient(user_agent)


def main(argv: list[str] | None = None) -> None:
    import argparse
    import json

    from memeradar.shared import repository as repo
    from memeradar.shared.db import connect, migrate

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="抓取 Reddit 梗圖候選項（預覽；入庫管線見 P3-5）")
    parser.add_argument(
        "--subreddit", action="append", default=[], help="可重複；預設 memes+dankmemes"
    )
    parser.add_argument("--limit", type=int, default=25, help="每版抓取貼文數")
    parser.add_argument("--min-score", type=int, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--client", choices=["public", "praw"], default="public")
    parser.add_argument("--update-watermark", action="store_true", help="執行後寫回水位")
    parser.add_argument("--json", action="store_true", help="逐筆輸出候選項 JSON")
    args = parser.parse_args(argv)

    adapter = RedditAdapter(
        build_client(args.client),
        subreddits=args.subreddit or None,
        min_score=args.min_score,
        listing_limit=args.limit,
    )

    import httpx

    conn = connect()
    try:
        migrate(conn)
        watermark = repo.get_watermark(conn, adapter.name)
        try:
            candidates, new_watermark = adapter.fetch(watermark)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                print(
                    "Reddit 已封鎖免憑證存取（403）。請改用官方 OAuth API：\n"
                    "  1. 到 https://www.reddit.com/prefs/apps 建立 script 類型 app\n"
                    "  2. 把 client id / secret 填入 .env 的"
                    " REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET\n"
                    "  3. 重跑並加上 --client praw"
                )
                raise SystemExit(1) from None
            raise
    finally:
        conn.close()

    for candidate in candidates:
        if args.json:
            print(json.dumps(candidate.__dict__, ensure_ascii=False))
        else:
            print(
                f"[{candidate.upvotes:>5}↑] {candidate.post_title[:50]}"
                f"  圖x{len(candidate.images)}  留言x{len(candidate.top_comments)}"
            )
    wrote = "（已寫回）" if args.update_watermark else "（未寫回，加 --update-watermark）"
    print(
        f"\n候選 {len(candidates)} 筆；水位 {watermark or '（無）'} → "
        f"{new_watermark or '（無）'}{wrote}"
    )


if __name__ == "__main__":
    main()
