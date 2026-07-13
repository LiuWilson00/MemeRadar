"""API 請求 schema（契約：docs/01 §5.2；參數定義：docs/04 §3）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from memeradar.shared.labels import EmotionLabel
from memeradar.shared.taxonomy import get_taxonomy


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
    input_type: Literal["text", "screenshot", "meme_battle"]
    conversation: list[TurnIn] = Field(default_factory=list)
    image: str | None = None  # screenshot（P2-5）/ meme_battle（對方梗圖）的 base64
    filters: FiltersIn = Field(default_factory=FiltersIn)
    params: ParamsIn = Field(default_factory=ParamsIn)
    client_id: str | None = None  # localStorage 匿名碼（無個資），供回饋分群分析


class ParseScreenshotRequest(BaseModel):
    image: str  # base64（PNG / JPEG / WebP）


class UploadMemeRequest(BaseModel):
    """Console 手動上傳（seed 匯入口）：匯入 → 標註 → 向量化一條龍。"""

    image: str  # base64（PNG / JPEG / WebP）
    title_hint: str | None = None  # 標註時的上下文提示（如主題名）
    model: str | None = None  # 覆寫標註用 vision 模型（Console 切換按鈕）


class AnnotationPatch(BaseModel):
    """人工複核的標籤修補（僅覆蓋有提供的欄位）。

    情緒為封閉集（enum 鎖 taxonomy）；分類為開放集，經 normalize_category 收斂同義詞。
    """

    ocr_text: str | None = None
    description: str | None = None
    franchise: str | None = None
    template_name: str | None = None
    emotions: list[EmotionLabel] | None = None
    usage_hints: list[str] | None = None
    categories: list[str] | None = None
    nsfw: bool | None = None
    is_meme: bool | None = None

    @field_validator("categories")
    @classmethod
    def _normalize_categories(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        tax = get_taxonomy()
        seen: dict[str, None] = {}
        for value in values:
            normalized = tax.normalize_category(value)
            if normalized is not None:
                seen.setdefault(normalized, None)
        return list(seen)


class ReviewAnnotationRequest(BaseModel):
    action: Literal["approve", "remove"]
    patch: AnnotationPatch | None = None


class DedupResolutionRequest(BaseModel):
    resolution: Literal["merged", "distinct"]


class FeedbackRequest(BaseModel):
    query_id: str
    meme_id: str
    rank: int = Field(ge=1)
    rating: Literal["up", "down"]
    note: str | None = None


class ModelSettingsRequest(BaseModel):
    """後台各任務模型設定；值為 None / 空字串 = 回 VLM 預設。"""

    models: dict[str, str | None] = Field(default_factory=dict)
