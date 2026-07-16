"""memes.tw 資料源 adapter：打公開 JSON API ``/wtf/api``，輸出統一 :class:`Candidate`。

- API：``GET https://memes.tw/wtf/api?page=N[&contest=C]``，每頁 20 筆，新→舊排序。
- robots.txt 允許全站爬取（``Disallow:`` 空）。
- 水位＝上次抓到的最大 meme id → 增量只抓更新的（新→舊排序，遇到 <= 水位即停）。
- 禮貌節流：每頁之間 sleep ``request_delay`` 秒（預設 1s），寧慢勿打爆對方。
- 只輸出候選；去重 / 標註 / 向量化交給既有 ingestion pipeline。

CLI 見 ``scripts/crawl_memes_tw.py``（解耦匯入：先入庫、標註交背景 worker）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from memeradar.ingestion.base import Candidate, _now_iso

API_URL = "https://memes.tw/wtf/api"
USER_AGENT = "MemeRadar/1.0 (meme ingestion; +https://memeradar.zeabur.app)"


def to_candidate(m: dict) -> Candidate:
    """memes.tw 一筆 API 物件 → Candidate（帶作者/連結/讚數/時間供 attribution）。"""
    ts = (m.get("created_at") or {}).get("timestamp")
    posted = datetime.fromtimestamp(int(ts), tz=UTC).isoformat() if ts else _now_iso()
    hashtag = (m.get("hashtag") or "").strip()
    author = ((m.get("author") or {}).get("name") or "").strip()
    comments = [c for c in (hashtag, f"作者：{author}" if author else "") if c]
    return Candidate(
        platform="memes_tw",
        post_id=str(m["id"]),
        post_url=m.get("url") or "",
        post_title=(m.get("title") or "").strip(),
        top_comments=comments,
        upvotes=m.get("total_like_count"),
        posted_at=posted,
        images=[{"url": m["src"], "order": 0}],
    )


class MemesTwAdapter:
    """memes.tw 資料源。``fetch(watermark) -> (candidates, new_watermark)``。"""

    def __init__(
        self,
        *,
        max_items: int = 2000,
        contest: int | None = None,
        request_delay: float = 1.0,
        http_get: Callable[[str], list] | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        # 不同來源（全站 vs 各 contest）用不同 name → 各自獨立水位、可增量
        self.name = "memes_tw" if contest is None else f"memes_tw_c{contest}"
        self._max = max_items
        self._contest = contest
        self._delay = request_delay
        self._get = http_get
        self._sleep = sleep or time.sleep

    def _page(self, page: int) -> list:
        url = f"{API_URL}?page={page}"
        if self._contest is not None:
            url += f"&contest={self._contest}"
        if self._get is not None:
            data = self._get(url)
        else:
            import requests

            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        return data if isinstance(data, list) else []

    def fetch(self, watermark: str | None) -> tuple[list[Candidate], str | None]:
        since = int(watermark) if watermark and str(watermark).isdigit() else 0
        out: list[Candidate] = []
        max_id = since
        page = 1
        while len(out) < self._max:
            batch = self._page(page)
            if not batch:
                break
            reached_seen = False
            for m in batch:
                try:
                    mid = int(m["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                if mid <= since:  # 新→舊：碰到已抓過的 → 之後都更舊，停止
                    reached_seen = True
                    break
                if not m.get("src"):
                    continue
                max_id = max(max_id, mid)
                out.append(to_candidate(m))
                if len(out) >= self._max:
                    break
            if reached_seen:
                break
            page += 1
            self._sleep(self._delay)
        return out, (str(max_id) if max_id else watermark)
