"""爬蟲 Adapter 框架：統一候選項 schema 與來源介面（docs/02 §2–§3）。

每個資料源實作為獨立 adapter，輸出統一的 :class:`Candidate`，
方便日後增減來源；水位（watermark）語意由各 adapter 自訂
（Reddit 用貼文時間），持久化存於 ``crawl_state`` 表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Candidate:
    """一則含圖貼文的候選項（docs/02 §3 schema）。"""

    platform: str
    post_id: str
    post_url: str
    post_title: str
    top_comments: list[str]
    upvotes: int | None
    posted_at: str  # ISO 8601
    images: list[dict]  # [{"url": str, "order": int}]
    crawled_at: str = field(default_factory=_now_iso)


class SourceAdapter(Protocol):
    """資料源介面：抓取水位之後的新候選項。"""

    name: str

    def fetch(self, watermark: str | None) -> tuple[list[Candidate], str | None]:
        """回傳 (候選項清單, 新水位)。無新內容時水位原樣返回。"""
        ...
