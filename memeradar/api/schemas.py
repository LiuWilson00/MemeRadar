"""API 請求 schema（契約：docs/01 §5.2；參數定義：docs/04 §3）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TurnIn(BaseModel):
    speaker: str = Field(pattern=r"^(me|other(_\w+)?)$")
    text: str


class FiltersIn(BaseModel):
    franchises: list[str] = Field(default_factory=list)  # 空 = 不限
    categories: list[str] = Field(default_factory=list)
    exclude_nsfw: bool = True


class ParamsIn(BaseModel):
    top_n: int = Field(default=5, ge=1, le=10)
    candidate_k: int = Field(default=50, ge=1, le=200)
    min_similarity: float = Field(default=0.35, ge=0.0, le=1.0)
    diversity: float = Field(default=0.5, ge=0.0, le=1.0)
    hotness_weight: float = Field(default=0.1, ge=0.0, le=0.5)


class RecommendRequest(BaseModel):
    input_type: Literal["text", "screenshot"]
    conversation: list[TurnIn] = Field(default_factory=list)
    image: str | None = None  # input_type=screenshot 時的 base64（P2-5）
    filters: FiltersIn = Field(default_factory=FiltersIn)
    params: ParamsIn = Field(default_factory=ParamsIn)


class FeedbackRequest(BaseModel):
    query_id: str
    meme_id: str
    rank: int = Field(ge=1)
    rating: Literal["up", "down"]
    note: str | None = None
