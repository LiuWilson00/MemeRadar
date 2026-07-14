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


class EventRequest(BaseModel):
    """前台行為事件（下載 / 選分類 等）；best-effort 分析用。"""

    event_type: str
    client_id: str | None = None
    meme_id: str | None = None
    meta: dict | None = None


class GoogleAuthRequest(BaseModel):
    """前端 Google 登入回傳的 ID token（credential）。"""

    credential: str


class LibraryUploadRequest(BaseModel):
    """登入使用者上傳梗圖到共用圖庫。"""

    image: str  # base64（PNG / JPEG / WebP）
    title_hint: str | None = None  # 標註上下文提示（如主題名）


class ReportRequest(BaseModel):
    """前台檢舉一張梗圖（不宜 / 冒犯等）。"""

    reason: str | None = None
    client_id: str | None = None  # 匿名代碼，供 distinct 計數（不重複灌報）


class ReportResolutionRequest(BaseModel):
    """後台處理被檢舉的梗圖：下架或忽略。"""

    action: Literal["remove", "dismiss"]


class LikeRequest(BaseModel):
    """探索圖庫按讚 / 取消讚（以匿名 client_id 記）。"""

    client_id: str


class CommentRequest(BaseModel):
    """在梗圖留一則彈幕。"""

    client_id: str
    author_name: str = Field(min_length=1, max_length=24)
    text: str = Field(min_length=1, max_length=80)


class CommentUpdateRequest(BaseModel):
    """編修自己的彈幕。"""

    client_id: str
    text: str = Field(min_length=1, max_length=80)


class NicknameRequest(BaseModel):
    """登入使用者設定顯示暱稱。"""

    nickname: str = Field(min_length=1, max_length=24)
