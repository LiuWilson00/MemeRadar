"""任務失敗時給使用者看的文案。

前台（``console/src/lib/api.ts``）把 ``task.error`` 原封不動顯示給使用者，所以那一欄
就是產品文案，不是給工程師看的：**不得出現供應商名稱、模型 id、HTTP 狀態碼**。
真正的技術原因走 ``task.error_detail``（同一份 JSON 回得到，方便 debug）與 runtime log。

依**例外型別**分派，不做字串比對——比對錯誤訊息的話，訊息一改文案就會悄悄失效。
"""

from __future__ import annotations

from memeradar.matching.intent import IntentRefusedError
from memeradar.matching.screenshot import ScreenshotParseError
from memeradar.understanding.embedding import EmbeddingUnavailableError
from memeradar.understanding.nvidia_vlm import VlmExhaustedError
from memeradar.understanding.opponent import OpponentMemeRefusedError

FALLBACK_COPY = "出事了阿 Sir，但我不知道出什麼事。"

# 順序即優先序（子類別要排在父類別之前）。VlmConfigError 是 VlmExhaustedError 的子類，
# 兩者共用同一句——「模型下架」是我們的問題，不該變成使用者的閱讀理解題。
_COPY_BY_TYPE: tuple[tuple[type[BaseException], str], ...] = (
    (IntentRefusedError, "這個話題我不敢接，換一句試試？"),
    (OpponentMemeRefusedError, "這張圖我不敢解讀，換一張試試？"),
    (ScreenshotParseError, "這張截圖我瞳孔地震，換一張清楚點的？"),
    (VlmExhaustedError, "腦袋轉到冒煙了，再給我一次機會好嗎。"),
    (EmbeddingUnavailableError, "這題我真的想不出梗，讓我先自閉一下。"),
)


def user_facing(exc: BaseException) -> str:
    """把例外翻成給使用者看的一句話；不認得的一律回 ``FALLBACK_COPY``。"""
    for exc_type, copy in _COPY_BY_TYPE:
        if isinstance(exc, exc_type):
            return copy
    return FALLBACK_COPY


def all_copy() -> list[str]:
    """全部文案（含 fallback），供測試檢查沒有內部細節外洩。"""
    return [copy for _, copy in _COPY_BY_TYPE] + [FALLBACK_COPY]
