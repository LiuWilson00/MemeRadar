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
import json
import sqlite3
import sys
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from memeradar.shared import repository as repo
from memeradar.shared.config import get_settings
from memeradar.shared.models import Meme, MemeAnnotation, MemeSource
from memeradar.shared.taxonomy import get_taxonomy

ANNOTATION_PROMPT_VERSION = "labeler-v1"
# 2026-07 搬到 NVIDIA NIM 免費 VLM（成本考量）；預設模型見 config.nvidia_vlm_model，
# Console 可切換。標的可用 model= / --model 覆寫。
DEFAULT_ANNOTATION_MODEL = "qwen/qwen3.5-122b-a10b"
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
    emotions: list[str] = Field(description="情緒標籤，限用字典，可多選")
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

    @field_validator("emotions")
    @classmethod
    def _filter_emotions(cls, values: list[str]) -> list[str]:
        # 情緒為封閉集：NVIDIA VLM 不像 Claude 能鎖 enum，故事後濾掉字典外的
        valid = set(get_taxonomy().emotions)
        seen: dict[str, None] = {}
        for value in values:
            cleaned = value.strip() if isinstance(value, str) else value
            if cleaned in valid:
                seen.setdefault(cleaned, None)
        return list(seen)

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
- usage_hints：最重要的欄位。寫 1–3 條**完整的情境句子**描述「這張圖通常什麼時候丟出來」，每條以動作語彙開頭、帶出具體情境（例如「被指責能力不足時，理直氣壯地自嘲認了」），並對齊以下回應策略：{strategies}。**不可只寫策略詞**（例如只寫「嗆聲反擊」「擺爛」是不合格的），每條都要是完整的使用時機描述。
- emotions：限用固定字典：{emotions}。
- categories：媒材類型（通常單選）。優先沿用既有分類：{categories}；只有都不合適時才自創一個簡短新分類詞（例如「運動賽事」「音樂」）。宗教、佛法、勸世語錄類請用「宗教心靈」；政治、時事、爭議公眾人物一律用「名人政治」。
- franchise：作品來源；不確定時給 null，不要猜。
- template_name：僅在是廣為流傳的知名模板時填寫，否則 null。

is_meme 判準：
- 是：帶網路文化語境、可拿來回覆對話的圖，包括「對話截圖形式」的截圖梗。
- 否：一般生活照、風景照、商品廣告、長輩圖、純資訊截圖（新聞、行程表）、資訊圖表。

nsfw 判準：含成人、血腥、露骨性暗示內容為 true；一般嘲諷或粗俗用語不算。

若提供了貼文標題或留言，僅作為理解使用情境的旁證，不可照抄為事實；圖文矛盾時以圖片為準。

confidence 為你對整體標註正確性的自評（0–1），拿不準的冷僻典故請誠實給低分。

只輸出一個 JSON 物件，不要多餘文字、不要 markdown 圍欄。欄位（型別）：is_meme(布林)、nsfw(布林)、ocr_text(字串)、description(字串)、characters(字串陣列)、franchise(字串或 null)、template_name(字串或 null)、emotions(字串陣列)、usage_hints(字串陣列)、categories(字串陣列)、confidence(0~1 浮點)。"""


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


def build_user_text(context_text: str | None) -> str:
    """VLM user turn 的文字（圖片由 NvidiaVlm 另外附上）。"""
    text = "請依系統指引標註這張圖片，只回 JSON。"
    if context_text:
        text += f"\n\n以下為該圖出處的旁證資訊（僅供參考，非事實）：\n{context_text}"
    return text


def _extract_json(raw: str) -> str | None:
    """從模型回應抽出 JSON 物件（容忍 markdown 圍欄與前後贅字）。"""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return raw[start : end + 1]


def parse_annotation(raw: str) -> AnnotationResult | None:
    """解析 VLM 回傳文字為 AnnotationResult；格式/驗證失敗回 None（供重試）。"""
    fragment = _extract_json(raw)
    if fragment is None:
        return None
    try:
        return AnnotationResult(**json.loads(fragment))
    except (json.JSONDecodeError, ValidationError, TypeError):
        return None


def load_meme_image_bytes(conn: sqlite3.Connection, meme: Meme, *, data_dir: Path) -> bytes:
    """讀梗圖原圖位元組，來源優先序與 serving 一致：DB image_data → R2 → 檔案系統。

    修正背景標註卡死：雲端上傳的圖存在 R2/DB、容器本機沒有那個檔，原本只讀檔案系統
    會永遠 FileNotFoundError → 標註佇列卡死。
    """
    data = repo.get_image_data(conn, meme.meme_id)
    if data is not None:
        return data
    settings = get_settings()
    if settings.r2_upload_enabled():
        from memeradar.shared import storage

        return storage.get_image(settings, meme.image_uri)
    return (data_dir / meme.image_uri).read_bytes()


def annotate_meme(
    conn: sqlite3.Connection,
    vlm,
    meme: Meme,
    *,
    model: str | None = None,
    data_dir: Path | None = None,
    retries: int = 2,
) -> MemeAnnotation | None:
    """用 NVIDIA VLM 標註單張梗圖並寫入資料庫。

    ``vlm`` 為 :class:`NvidiaVlm`。解析/驗證失敗會重試 ``retries`` 次；
    仍失敗（拒答 / 回傳非 JSON）→ 轉 pending_review、不落標註列。
    ``model`` 覆寫本次使用的 vision 模型（Console 切換按鈕用）。
    每次呼叫的用量寫入 vlm_calls 表。
    """
    data_dir = data_dir if data_dir is not None else get_settings().memeradar_data_dir
    image_bytes = load_meme_image_bytes(conn, meme, data_dir=data_dir)
    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    media_type = media_type_for(meme.image_uri)
    system = build_system_prompt()
    user_text = build_user_text(build_context_text(repo.list_sources(conn, meme.meme_id)))
    used_model = model or vlm.model

    result: AnnotationResult | None = None
    for _ in range(retries + 1):
        raw = vlm.annotate(
            image_b64, media_type, system, user_text,
            meme_id=meme.meme_id, model=model,
            log=lambda rec: repo.insert_vlm_call(conn, rec),
        )
        result = parse_annotation(raw)
        if result is not None:
            break

    if result is None:
        # 重試耗盡（拒答 / 非 JSON）→ 轉人工複核
        repo.insert_vlm_call(
            conn, {"model": used_model, "task": "annotate", "meme_id": meme.meme_id,
                   "status": "parse_fail"}
        )
        repo.set_status(conn, meme.meme_id, "pending_review")
        return None

    annotation = MemeAnnotation(
        meme_id=meme.meme_id,
        model_version=f"{ANNOTATION_PROMPT_VERSION}@{used_model}",
        is_meme=result.is_meme,
        nsfw=result.nsfw,
        ocr_text=result.ocr_text,
        description=result.description,
        characters=result.characters,
        franchise=result.franchise,
        template_name=result.template_name,
        emotions=result.emotions,  # 已由 validator 濾到 taxonomy
        usage_hints=result.usage_hints,
        categories=result.categories,  # 已由 validator 正規化為開放集正規名
        confidence=result.confidence,
    )
    repo.upsert_annotation(conn, annotation)

    if not result.is_meme or result.confidence < CONFIDENCE_REVIEW_THRESHOLD:
        repo.set_status(conn, meme.meme_id, "pending_review")

    return annotation


def build_default_vlm():
    """由 settings 的 NVIDIA key 清單建 NvidiaVlm（正式執行用）。"""
    from memeradar.understanding.nvidia_vlm import NvidiaVlm, build_clients

    settings = get_settings()
    keys = settings.nvidia_keys()
    if not keys:
        raise RuntimeError(
            "缺少 NVIDIA_API_KEYS：請於 .env 填入至少一把 NVIDIA key（逗號分隔多把）"
        )
    clients, key_ids = build_clients(keys)
    return NvidiaVlm(clients, key_ids, settings.nvidia_vlm_model)


def main(argv: list[str] | None = None) -> None:
    import argparse

    from memeradar.shared.db import connect, migrate

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="批次標註尚未標註的梗圖（NVIDIA VLM）")
    parser.add_argument("--limit", type=int, default=None, help="最多標註張數（預設全部）")
    parser.add_argument("--model", default=None, help="覆寫 vision 模型（預設用 config 設定）")
    args = parser.parse_args(argv)

    vlm = build_default_vlm()

    conn = connect()
    try:
        migrate(conn)
        pending = repo.list_memes_missing_annotation(conn, limit=args.limit)
        if not pending:
            print("沒有待標註的梗圖。")
            return
        print(f"待標註 {len(pending)} 張（model={args.model or vlm.model}）")
        ok = review = failed = 0
        for i, meme in enumerate(pending, 1):
            try:
                annotation = annotate_meme(conn, vlm, meme, model=args.model)
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
