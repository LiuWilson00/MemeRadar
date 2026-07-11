"""P3-4 規則引擎價值過濾（docs/02 §5 第一層：零成本先擋明顯不是的）。

三個檢查點對應管線的三個階段：
- :func:`check_candidate` —— 下載前、貼文層級（互動門檻）
- :func:`check_image_url` —— 下載前、圖片網址層級（格式 / 黑名單）
- :func:`check_image` —— 下載後、像素層級（尺寸 / 長寬比）

注意（docs/02 §7）：對話截圖形式的梗圖屬合法，規則層不得以「像截圖」剔除；
是否梗圖的語意判定屬第二層（VLM 標註的 is_meme）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from memeradar.ingestion.base import Candidate

ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


@dataclass(frozen=True)
class RuleConfig:
    allowed_extensions: tuple[str, ...] = ALLOWED_EXTENSIONS
    min_short_side: int = 200
    max_aspect_ratio: float = 4.0
    # 來源別互動門檻（docs/02 §5：Reddit ≥100、Dcard ≥50）
    min_upvotes_by_platform: dict = field(
        default_factory=lambda: {"reddit": 100, "dcard": 50}
    )
    default_min_upvotes: int = 50
    url_blocklist: tuple[str, ...] = ()


def check_candidate(candidate: Candidate, rules: RuleConfig) -> str | None:
    """貼文層級檢查；回傳拒絕原因或 None。無互動數的來源不套門檻。"""
    if candidate.upvotes is not None:
        threshold = rules.min_upvotes_by_platform.get(
            candidate.platform, rules.default_min_upvotes
        )
        if candidate.upvotes < threshold:
            return "互動門檻"
    return None


def check_image_url(url: str, rules: RuleConfig) -> str | None:
    for blocked in rules.url_blocklist:
        if blocked in url:
            return "網址黑名單"
    path = url.split("?")[0].lower()
    if not path.endswith(rules.allowed_extensions):
        return "不支援格式"
    return None


def check_image(width: int, height: int, rules: RuleConfig) -> str | None:
    if min(width, height) < rules.min_short_side:
        return "尺寸過小"
    if max(width, height) / max(min(width, height), 1) > rules.max_aspect_ratio:
        return "長寬比異常"
    return None
