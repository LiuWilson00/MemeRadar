"""P1-1 標註器：單一 VLM pass 完成 OCR / 描述 / 標籤 / 使用情境（docs/03 §2）。

設計要點：
- Claude vision + structured outputs（``client.messages.parse`` + pydantic），
  情緒以 taxonomy 動態 enum 進 JSON schema（封閉集，API 端保證合法）；
  分類為開放集（franchise 式）：自由文字 + ``normalize_category`` 正規化收斂同義詞。
- 貼文上下文（標題 / 熱門留言）注入 user turn，prompt 明示其為旁證。
- system prompt 為穩定字串（由 taxonomy 決定性生成），掛 cache_control 吃 prompt caching。
- 版本化：``model_version = {ANNOTATION_PROMPT_VERSION}@{model}`` 寫入標註列。
- pending_review 規則（docs/03 §4、§6）：模型拒答、is_meme=false、
  confidence < 0.7 皆轉人工複核；拒答不落標註列。
- CLI：``python -m memeradar.understanding.annotator [--limit N]`` 批次標註未標註者。
"""

from __future__ import annotations

import base64
import sqlite3
import sys
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from memeradar.shared import repository as repo
from memeradar.shared.config import get_settings
from memeradar.shared.labels import EmotionLabel
from memeradar.shared.models import Meme, MemeAnnotation, MemeSource
from memeradar.shared.taxonomy import get_taxonomy

ANNOTATION_PROMPT_VERSION = "labeler-v1"
# 2026-07-11 團隊決策：成本考量採 sonnet 級為預設；品質不足時以 --model 升級 opus
DEFAULT_ANNOTATION_MODEL = "claude-sonnet-5"
# 2026-07 依積壓佇列實證下調（原 0.7）：模型對正常梗圖多給 0.6（92 張積壓中
# 79 張剛好 0.6、全 is_meme=true），0.7 門檻把好圖灌爆複核佇列。0.5 只攔真正很低者。
CONFIDENCE_REVIEW_THRESHOLD = 0.5
MAX_OUTPUT_TOKENS = 2048


class AnnotationResult(BaseModel):
    """VLM 單次 pass 的標註輸出（docs/03 §2.2 schema）。"""

    is_meme: bool = Field(description="是否為可用於對話回應的梗圖")
    nsfw: bool = Field(description="是否含成人 / 血腥等不宜內容")
    ocr_text: str = Field(description="圖中所有文字，保留原文（含錯字、注音文）；無文字給空字串")
    description: str = Field(description="客觀視覺描述：人物、表情、動作、構圖")
    characters: list[str] = Field(description="圖中主體角色名稱")
    franchise: str | None = Field(description="作品來源（如：海綿寶寶、甄嬛傳）；不明確給 null")
    template_name: str | None = Field(description="可辨識的知名梗圖模板名；否則 null")
    emotions: list[EmotionLabel] = Field(description="情緒標籤，限用字典，可多選")
    usage_hints: list[str] = Field(
        description="1–3 條使用情境：這張圖通常什麼時候丟出來，以動作語彙開頭"
    )
    categories: list[str] = Field(
        description="媒材類型分類，通常單選；優先沿用已知分類，沒有合適的才自創簡短新詞"
    )
    confidence: float = Field(description="整體標註信心 0–1")

    @field_validator("franchise")
    @classmethod
    def _normalize_franchise(cls, value: str | None) -> str | None:
        return get_taxonomy().normalize_franchise(value)

    @field_validator("categories")
    @classmethod
    def _normalize_categories(cls, values: list[str]) -> list[str]:
        tax = get_taxonomy()
        # 正規化 + 去重（保序）：同義詞收斂後可能重複
        seen: dict[str, None] = {}
        for value in values:
            normalized = tax.normalize_category(value)
            if normalized is not None:
                seen.setdefault(normalized, None)
        return list(seen)


def build_system_prompt() -> str:
    """由 taxonomy 決定性生成標註指引（穩定字串，供 prompt caching）。"""
    tax = get_taxonomy()
    emotions = "、".join(tax.emotions)
    categories = "、".join(tax.known_categories)
    strategies = "、".join(s.label for s in tax.strategies)
    return f"""你是梗圖標註專家，為「對話梗圖推薦系統」建立檢索資料。對每張圖片，在同一次判讀中綜合三件事：圖中文字、人物表情動作、文化典故。

各欄位要求：
- ocr_text：抄錄圖中所有文字，保留原文原樣（含錯字、諧音、注音文），不要改寫。若文字是諧音或網路用語，在 description 中解釋原意。
- description：客觀描述畫面（人物、表情、動作、構圖），不加入使用建議。
- usage_hints：最重要的欄位。寫 1–3 條「這張圖通常什麼時候丟出來」，以動作語彙開頭，盡量對齊以下回應策略詞彙：{strategies}。
- emotions：限用固定字典：{emotions}。
- categories：媒材類型（通常單選）。優先沿用既有分類：{categories}；只有都不合適時才自創一個簡短新分類詞（例如「運動賽事」「音樂」）。宗教、佛法、勸世語錄類請用「宗教心靈」；政治、時事、爭議公眾人物一律用「名人政治」。
- franchise：作品來源；不確定時給 null，不要猜。
- template_name：僅在是廣為流傳的知名模板時填寫，否則 null。

is_meme 判準：
- 是：帶網路文化語境、可拿來回覆對話的圖，包括「對話截圖形式」的截圖梗。
- 否：一般生活照、風景照、商品廣告、長輩圖、純資訊截圖（新聞、行程表）、資訊圖表。

nsfw 判準：含成人、血腥、露骨性暗示內容為 true；一般嘲諷或粗俗用語不算。

若提供了貼文標題或留言，僅作為理解使用情境的旁證，不可照抄為事實；圖文矛盾時以圖片為準。

confidence 為你對整體標註正確性的自評（0–1），拿不準的冷僻典故請誠實給低分。"""


def media_type_for(image_uri: str) -> str:
    ext = Path(image_uri).suffix.lower()
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    if ext not in mapping:
        raise ValueError(f"不支援的圖片格式：{ext}（{image_uri}）")
    return mapping[ext]


def build_context_text(sources: list[MemeSource]) -> str | None:
    """把貼文 metadata 組成上下文文字；無可用資訊時回傳 None。"""
    lines: list[str] = []
    for src in sources:
        if src.post_title:
            lines.append(f"貼文標題／收藏分類：{src.post_title}（來源：{src.platform}）")
        for comment in src.top_comments[:5]:
            lines.append(f"熱門留言：{comment}")
        if src.upvotes:
            lines.append(f"互動數：{src.upvotes}")
    return "\n".join(lines) if lines else None


def build_user_content(
    image_bytes: bytes, media_type: str, context_text: str | None
) -> list[dict]:
    text = "請依系統指引標註這張圖片。"
    if context_text:
        text += f"\n\n以下為該圖出處的旁證資訊（僅供參考，非事實）：\n{context_text}"
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(image_bytes).decode("ascii"),
            },
        },
        {"type": "text", "text": text},
    ]


def annotate_meme(
    conn: sqlite3.Connection,
    client,
    meme: Meme,
    *,
    model: str = DEFAULT_ANNOTATION_MODEL,
    data_dir: Path | None = None,
) -> MemeAnnotation | None:
    """標註單張梗圖並寫入資料庫；拒答時回傳 None 並轉 pending_review。"""
    data_dir = data_dir if data_dir is not None else get_settings().memeradar_data_dir
    image_bytes = (data_dir / meme.image_uri).read_bytes()

    response = client.messages.parse(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=[
            {
                "type": "text",
                "text": build_system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": build_user_content(
                    image_bytes,
                    media_type_for(meme.image_uri),
                    build_context_text(repo.list_sources(conn, meme.meme_id)),
                ),
            }
        ],
        output_format=AnnotationResult,
    )

    if getattr(response, "stop_reason", None) == "refusal" or response.parsed_output is None:
        # docs/03 §6：模型拒答 → 記錄後跳過，不落標註列
        repo.set_status(conn, meme.meme_id, "pending_review")
        return None

    result: AnnotationResult = response.parsed_output
    annotation = MemeAnnotation(
        meme_id=meme.meme_id,
        model_version=f"{ANNOTATION_PROMPT_VERSION}@{model}",
        is_meme=result.is_meme,
        nsfw=result.nsfw,
        ocr_text=result.ocr_text,
        description=result.description,
        characters=result.characters,
        franchise=result.franchise,
        template_name=result.template_name,
        emotions=[e.value for e in result.emotions],
        usage_hints=result.usage_hints,
        categories=result.categories,  # 已由 validator 正規化為開放集正規名
        confidence=result.confidence,
    )
    repo.upsert_annotation(conn, annotation)

    if not result.is_meme or result.confidence < CONFIDENCE_REVIEW_THRESHOLD:
        repo.set_status(conn, meme.meme_id, "pending_review")

    return annotation


def main(argv: list[str] | None = None) -> None:
    import argparse

    import anthropic

    from memeradar.shared.db import connect, migrate

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="批次標註尚未標註的梗圖")
    parser.add_argument("--limit", type=int, default=None, help="最多標註張數（預設全部）")
    parser.add_argument("--model", default=DEFAULT_ANNOTATION_MODEL)
    args = parser.parse_args(argv)

    api_key = get_settings().anthropic_api_key
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    conn = connect()
    try:
        migrate(conn)
        pending = repo.list_memes_missing_annotation(conn, limit=args.limit)
        if not pending:
            print("沒有待標註的梗圖。")
            return
        print(f"待標註 {len(pending)} 張（model={args.model}）")
        ok = review = failed = 0
        for i, meme in enumerate(pending, 1):
            try:
                annotation = annotate_meme(conn, client, meme, model=args.model)
            except Exception as exc:  # noqa: BLE001 — 批次不因單張失敗中斷
                failed += 1
                print(f"[{i}/{len(pending)}] {meme.meme_id} 失敗：{exc}")
                continue
            if annotation is None:
                review += 1
                print(f"[{i}/{len(pending)}] {meme.meme_id} 模型拒答 → pending_review")
            else:
                ok += 1
                status = repo.get_meme(conn, meme.meme_id).status
                mark = "（轉人工複核）" if status == "pending_review" else ""
                print(
                    f"[{i}/{len(pending)}] {meme.meme_id} "
                    f"{'✓' if annotation.is_meme else '非梗圖'} "
                    f"conf={annotation.confidence:.2f}{mark} {annotation.ocr_text[:20]}"
                )
        print(f"完成：成功 {ok}、待複核 {review}、失敗 {failed}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
