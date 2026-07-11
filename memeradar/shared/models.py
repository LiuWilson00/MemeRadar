"""資料模型（對應 docs/01-architecture.md §4 概念模型）。

概念模型中的 TEMPLATE 實體在 v1 簡化為 ``MemeAnnotation.template_name`` 欄位
（與 docs/03 標註輸出對齊）；等「模板知識庫」有實際需求時再正規化成獨立表。
JSON 欄位在資料庫層以 TEXT 儲存，序列化細節封裝在 repository 層。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

MEME_STATUSES = ("active", "pending_review", "removed")
EMBEDDING_KINDS = ("text_retrieval", "image_dedup")
RATINGS = ("up", "down")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Meme:
    meme_id: str
    image_uri: str
    sha256: str
    phash: str | None = None
    width: int | None = None
    height: int | None = None
    hotness: float = 0.0
    status: str = "active"
    first_seen_at: str = field(default_factory=_now_iso)


@dataclass
class MemeSource:
    source_id: str
    meme_id: str
    platform: str
    post_url: str | None = None
    post_title: str | None = None
    top_comments: list[str] = field(default_factory=list)
    upvotes: int | None = None
    posted_at: str | None = None
    crawled_at: str = field(default_factory=_now_iso)


@dataclass
class MemeAnnotation:
    meme_id: str
    model_version: str
    is_meme: bool = True
    nsfw: bool = False
    ocr_text: str = ""
    description: str = ""
    characters: list[str] = field(default_factory=list)
    franchise: str | None = None
    template_name: str | None = None
    emotions: list[str] = field(default_factory=list)
    usage_hints: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    confidence: float | None = None
    annotated_at: str = field(default_factory=_now_iso)


@dataclass
class Embedding:
    meme_id: str
    kind: str  # EMBEDDING_KINDS 之一
    model: str
    vector: list[float]
    created_at: str = field(default_factory=_now_iso)


@dataclass
class RecommendationLog:
    query_id: str
    conversation: list[dict]
    params_snapshot: dict
    intent_result: dict | None = None
    candidates: list[dict] | None = None
    final_results: list[dict] | None = None
    latency_ms: int | None = None
    created_at: str = field(default_factory=_now_iso)


@dataclass
class FeedbackEvent:
    feedback_id: str
    query_id: str
    meme_id: str
    rank: int
    rating: str  # RATINGS 之一
    note: str | None = None
    created_at: str = field(default_factory=_now_iso)
