"""梗圖深度調研：一張圖 → 有考據的專欄文章。

這是全站唯一「允許花比較多錢」的呼叫（見 config.blog_research_model）。原因在 PoC
（2026-08-01，真 key、三張真圖、三個模型）：便宜模型在**查不到出處時會編**，而且編得
很像真的——同一張 TVB 截圖，gemini-2.5-flash 說是《奪命提示》2025 並點名了兩位演員與
劇情設定，perplexity/sonar-pro 說是《跳躍生命線》2018，兩者互相矛盾且都附了連得上的
（但不相關的）網址。claude-sonnet-5 是唯一回 ``unknown``、把 origin 留空的。

一個每天自動發文的專欄，寫錯考據的代價遠高於省下來的模型費，所以這裡不省。

防幻覺靠三層，缺一不可：
1. prompt 明講「編一個看起來合理的答案，比誠實說不知道糟糕一百倍」
2. 結構化輸出把「查到的」與「沒查到的」分欄（verdict / sources / unverified_claims）
3. 呼叫端依 verdict + confidence 決定自動發布或留草稿（見 api/blog.py 的閘門）
"""

from __future__ import annotations

import base64

from pydantic import BaseModel, Field

from memeradar.shared.imaging import downscale_for_vlm
from memeradar.shared.prompt_lang import OUTPUT_ZH_TW

RESEARCH_PROMPT_VERSION = "research-v1"
MAX_OUTPUT_TOKENS = 4000
#: 網路搜尋抓幾筆。PoC 實測 5 筆時 sonnet 單次 input 曾衝到 75k tokens（$0.23 一篇），
#: 搜尋結果整包塞進 context 是成本主因。3 筆足夠佐證，貴的那 2 筆通常是雜訊。
WEB_MAX_RESULTS = 3


class ResearchOrigin(BaseModel):
    work: str = Field(description="作品名稱；不確定給空字串")
    year: str = Field(description="年份；不確定給空字串")
    scene: str = Field(description="這個畫面在作品中的橋段；不確定給空字串")
    characters: str = Field(description="畫面中的角色／人物；不確定給空字串")
    region: str = Field(description="地區或語系（香港/台灣/日本…）；不確定給空字串")


class ResearchSource(BaseModel):
    title: str = Field(description="來源標題")
    url: str = Field(description="可點的真實網址，必須是實際查到的")
    supports: str = Field(description="這個來源支撐了文中哪一句話")


class ResearchResult(BaseModel):
    verdict: str = Field(description="identified / partial / unknown")
    origin: ResearchOrigin
    caption_is_original: bool | None = Field(
        description="圖上的文字是否為原作品台詞；無法判斷給 null"
    )
    caption_note: str = Field(description="圖上文字的來歷（原台詞／二創／改編／不明）")
    sources: list[ResearchSource] = Field(description="佐證來源，查不到就給空陣列")
    confidence: float = Field(description="整體考據信心 0~1")
    unverified_claims: list[str] = Field(description="想講但查不到證據的事，不得寫進正文")
    title: str = Field(description="文章標題，20 字內，不要用聳動標題農場口吻")
    article_html: str = Field(description="繁體中文短文 250~450 字，可用 <p><h3><strong>")


def build_system_prompt() -> str:
    return """你是梗圖文化的調研記者，要為「每日一梗」專欄寫一篇有考據的短文。

## 最重要的規則：查不到就說查不到

這是調研任務，不是創作任務。你寫下的每一個關於出處的事實，都必須有你**實際搜尋到的來源**支撐。

- 認得出作品／人物／事件 → 用網路搜尋確認，並在 sources 附上可點的 URL。
- 有點眉目但無法確認 → verdict 給 "partial"，把不確定的部分寫進 unverified_claims，**不要**寫進文章正文當事實。
- 完全查不到 → verdict 給 "unknown"，origin 各欄位留空，文章就只談「這張圖在對話裡怎麼用」，不要談來源。
- **絕對不要**為了讓文章看起來完整而編造片名、年份、集數、演員、事件或引文。編一個看起來合理的答案，比誠實說不知道糟糕一百倍。
- 不要拿搜尋到的**不相關**新聞來充當脈絡。與這張圖無關的時事，就算查得到也不要寫進正文。

## 圖上的文字未必是原文

梗圖常是「既有影像 + 網友後製的字」。若圖上的字是二創、改編或惡搞，**必須明講那不是當事人真的說過的話**，並分開交代：影像出自哪裡、文字是誰加的（查不到就說不知道）。把網友的玩笑寫成真實引文是最嚴重的錯誤。

反過來也要小心：電視台截圖若帶有台標與內嵌字幕，那多半就是原字幕，不要一律當成二創。

## 文章怎麼寫

繁體中文、250~450 字、口語但有考據感。只陳述你有把握的事；沒把握的一律不要寫進去。
不要寫「根據網路資料顯示」這種空話，要嘛講清楚出處，要嘛承認查不到。

只輸出一個 JSON 物件，不要圍欄。""" + OUTPUT_ZH_TW


def build_user_text(*, ocr_text: str, description: str, franchise: str | None) -> str:
    return (
        "請調研這張梗圖並寫成專欄短文。\n\n"
        "已知的機器標註（僅供參考，可能有誤，不可當作事實引用）：\n"
        f"- 圖上文字（OCR）：{ocr_text or '（無）'}\n"
        f"- 畫面描述：{description}\n"
        f"- 系統猜測的出處：{franchise or '（無）'}\n\n"
        "請先用網路搜尋確認出處，再依系統指引輸出 JSON。"
    )


def build_research_vlm(model: str | None = None):
    """建調研用的 client：貴模型 + 開網路搜尋 + 有耐心的逾時。

    與標註用的 client 分開建，因為三件事都不一樣：模型（sonnet 而非 flash）、
    plugins（要搜尋）、逾時（搜尋+長文動輒 40~50 秒，PoC 實測最慢 49s）。
    """
    from memeradar.shared.config import get_settings
    from memeradar.understanding.nvidia_vlm import NvidiaVlm, build_clients

    settings = get_settings()
    keys = settings.vlm_keys()
    if not keys:
        raise RuntimeError("缺少 VLM_API_KEYS / OPENROUTER_API_KEY，無法進行調研")
    clients, key_ids = build_clients(
        keys, base_url=settings.vlm_base_url, timeout=240.0
    )
    return NvidiaVlm(
        clients, key_ids, model or settings.blog_research_model,
        max_wait_s=600.0,  # 每天只跑一次，慢一點無所謂，寧可等也不要沒文章
        # 調研要推理，不能像標註那樣關掉 thinking；plugins 開網路搜尋
        extra_body={"plugins": [{"id": "web", "max_results": WEB_MAX_RESULTS}]},
    )


def research_meme(
    vlm,
    image_bytes: bytes,
    *,
    ocr_text: str,
    description: str,
    franchise: str | None = None,
    meme_id: str | None = None,
    model: str | None = None,
    log=None,
) -> ResearchResult | None:
    """調研一張梗圖；解析失敗回 None（呼叫端自行決定重試或跳過）。"""
    from memeradar.matching.screenshot import detect_media_type
    from memeradar.understanding.nvidia_vlm import call_structured

    image_bytes = downscale_for_vlm(image_bytes)
    media_type = detect_media_type(image_bytes)  # 縮圖會轉 JPEG，故縮完才定 media type
    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    return call_structured(
        vlm, ResearchResult, build_system_prompt(),
        build_user_text(ocr_text=ocr_text, description=description, franchise=franchise),
        image_b64=image_b64, media_type=media_type, task="research",
        meme_id=meme_id, model=model, log=log, max_tokens=MAX_OUTPUT_TOKENS,
        retries=1,  # 一篇要 $0.08~0.23，不像標註可以重試三次
    )


def should_auto_publish(result: ResearchResult, *, min_confidence: float = 0.6) -> bool:
    """自動發布閘門：只有查得到出處且有信心的才直接上線。

    低信心不是「品質差一點」，是「可能整段是編的」——PoC 裡連 sonnet 都編過一個連不上的
    Facebook 網址。擋下來進草稿，由人看過再發。
    """
    return (
        result.verdict == "identified"
        and result.confidence >= min_confidence
        and bool(result.sources)
    )
