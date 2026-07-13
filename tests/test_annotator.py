"""P1-1 標註器測試（規格：docs/03 §2；VLM 搬 NVIDIA 後）。

不打真實 API：以 stub VLM（回傳原始 JSON 文字）驗證 orchestration（prompt 組裝、
上下文注入、JSON 解析 + 重試、版本化、pending_review 規則）。
"""

import json

import pytest
from PIL import Image

from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Meme, MemeSource, new_id
from memeradar.understanding.annotator import (
    ANNOTATION_PROMPT_VERSION,
    AnnotationResult,
    annotate_meme,
    build_system_prompt,
    build_user_text,
    media_type_for,
    parse_annotation,
)


def valid_payload(**overrides) -> dict:
    payload = {
        "is_meme": True,
        "nsfw": False,
        "ocr_text": "我就爛",
        "description": "海綿寶寶攤手站立，表情理直氣壯",
        "characters": ["海綿寶寶"],
        "franchise": "SpongeBob",
        "template_name": "我就爛",
        "emotions": ["擺爛", "理直氣壯"],
        "usage_hints": ["被指責能力不足時，理直氣壯地自嘲認了"],
        "categories": ["卡通動畫"],
        "confidence": 0.93,
    }
    payload.update(overrides)
    return payload


class TestAnnotationResultSchema:
    def test_valid_payload_parses(self):
        result = AnnotationResult(**valid_payload())
        assert result.is_meme is True
        assert result.emotions == ["擺爛", "理直氣壯"]

    def test_unknown_emotion_filtered_not_rejected(self):
        # NVIDIA 不像 Claude 能鎖 enum → 事後濾：字典外的丟掉、字典內的保留（不整筆失敗）
        result = AnnotationResult(**valid_payload(emotions=["開心到飛起", "擺爛", "無奈"]))
        assert result.emotions == ["擺爛", "無奈"]

    def test_category_alias_normalized(self):
        # 開放集 + 正規化：別名收斂到正規名
        assert AnnotationResult(**valid_payload(categories=["佛法"])).categories == ["宗教心靈"]

    def test_unknown_category_passthrough(self):
        # 開放集：模型自創的新分類原樣保留（不再驗證失敗）
        assert AnnotationResult(**valid_payload(categories=["運動賽事"])).categories == ["運動賽事"]

    def test_franchise_normalized_via_taxonomy(self):
        result = AnnotationResult(**valid_payload(franchise="SpongeBob"))
        assert result.franchise == "海綿寶寶"

    def test_unknown_franchise_passthrough_and_null_ok(self):
        assert AnnotationResult(**valid_payload(franchise="獵人")).franchise == "獵人"
        assert AnnotationResult(**valid_payload(franchise=None)).franchise is None


class TestPromptBuilding:
    def test_system_prompt_contains_full_taxonomy(self):
        prompt = build_system_prompt()
        from memeradar.shared.taxonomy import get_taxonomy

        tax = get_taxonomy()
        for emotion in tax.emotions:
            assert emotion in prompt
        for category in tax.categories:
            assert category.label in prompt
        for strategy in tax.strategies:
            assert strategy.label in prompt

    def test_system_prompt_is_deterministic(self):
        # 穩定前綴才能吃到 prompt caching（docs/03 §2.1）
        assert build_system_prompt() == build_system_prompt()

    def test_system_prompt_asks_for_json(self):
        assert "JSON" in build_system_prompt()

    def test_system_prompt_demands_full_usage_sentences(self):
        # A/B 實證：Qwen 常把 usage_hints 只寫策略詞 → prompt 明確要求完整情境句
        prompt = build_system_prompt()
        assert "完整" in prompt
        assert "只寫策略詞" in prompt

    def test_user_text_includes_context_as_hearsay(self):
        text = build_user_text("標題：上班的我\n熱門留言：笑死這就是我")
        assert "上班的我" in text
        assert "旁證" in text  # 上下文標明為旁證而非事實

    def test_user_text_without_context(self):
        assert "旁證" not in build_user_text(None)


class TestParseAnnotation:
    def test_parses_plain_json(self):
        result = parse_annotation(json.dumps(valid_payload()))
        assert result is not None and result.ocr_text == "我就爛"

    def test_tolerates_markdown_fences_and_preamble(self):
        raw = "這是標註：\n```json\n" + json.dumps(valid_payload()) + "\n```"
        result = parse_annotation(raw)
        assert result is not None and result.is_meme is True

    def test_non_json_returns_none(self):
        assert parse_annotation("抱歉，我無法標註這張圖片。") is None
        assert parse_annotation("") is None


class TestMediaType:
    def test_known_extensions(self):
        assert media_type_for("images/a.png") == "image/png"
        assert media_type_for("images/a.jpg") == "image/jpeg"
        assert media_type_for("images/a.webp") == "image/webp"

    def test_unknown_extension_raises(self):
        with pytest.raises(ValueError):
            media_type_for("images/a.gif")


# ── annotate_meme orchestration（stub VLM）────────────────────────────


class StubVlm:
    """回傳固定原始文字（模擬 NVIDIA VLM）；記錄呼叫參數供斷言。"""

    model = "qwen/test-model"

    def __init__(self, raw: str):
        self.raw = raw
        self.calls: list[dict] = []

    def annotate(self, image_b64, media_type, system, user_text, *, log=None, **kwargs):
        self.calls.append({"system": system, "user_text": user_text, **kwargs})
        if log is not None:  # 比照 NvidiaVlm：每次呼叫回報用量
            log({"key_id": "…test", "model": kwargs.get("model") or self.model,
                 "task": "annotate", "meme_id": kwargs.get("meme_id"), "status": "ok",
                 "latency_ms": 100, "prompt_tokens": 200, "completion_tokens": 80,
                 "error": None})
        return self.raw


def vlm_returning(**payload_overrides) -> StubVlm:
    return StubVlm(json.dumps(valid_payload(**payload_overrides)))


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    (d / "images").mkdir(parents=True)
    return d


@pytest.fixture
def seeded_meme(conn, data_dir) -> Meme:
    meme_id = new_id("m")
    Image.new("RGB", (400, 400), (200, 30, 30)).save(data_dir / "images" / f"{meme_id}.png")
    meme = Meme(meme_id=meme_id, image_uri=f"images/{meme_id}.png", sha256="d" * 64)
    repo.insert_meme(conn, meme)
    repo.add_source(
        conn,
        MemeSource(
            source_id=new_id("s"),
            meme_id=meme_id,
            platform="manual",
            post_title="海綿寶寶",
            top_comments=["笑死這就是我"],
        ),
    )
    return meme


class TestAnnotateMeme:
    def test_happy_path_persists_annotation(self, conn, data_dir, seeded_meme):
        vlm = vlm_returning()

        result = annotate_meme(conn, vlm, seeded_meme, data_dir=data_dir)

        assert result is not None
        stored = repo.get_annotation(conn, seeded_meme.meme_id)
        assert stored.ocr_text == "我就爛"
        assert stored.emotions == ["擺爛", "理直氣壯"]
        assert stored.franchise == "海綿寶寶"  # 已正規化
        assert stored.model_version == f"{ANNOTATION_PROMPT_VERSION}@{vlm.model}"
        assert repo.get_meme(conn, seeded_meme.meme_id).status == "active"

        # 上下文注入 user turn
        call = vlm.calls[0]
        assert "海綿寶寶" in call["user_text"] and "笑死這就是我" in call["user_text"]

    def test_logs_vlm_call(self, conn, data_dir, seeded_meme):
        annotate_meme(conn, vlm_returning(), seeded_meme, data_dir=data_dir)
        stats = repo.vlm_call_stats(conn)
        assert any(s["status"] == "ok" for s in stats)

    def test_model_override_recorded_in_version(self, conn, data_dir, seeded_meme):
        annotate_meme(conn, vlm_returning(), seeded_meme, data_dir=data_dir,
                      model="meta/llama-4-maverick")
        stored = repo.get_annotation(conn, seeded_meme.meme_id)
        assert stored.model_version.endswith("@meta/llama-4-maverick")

    def test_non_json_response_marks_pending_review(self, conn, data_dir, seeded_meme):
        vlm = StubVlm("抱歉，我無法標註這張圖片。")
        result = annotate_meme(conn, vlm, seeded_meme, data_dir=data_dir, retries=1)
        assert result is None
        assert repo.get_annotation(conn, seeded_meme.meme_id) is None
        assert repo.get_meme(conn, seeded_meme.meme_id).status == "pending_review"

    def test_non_meme_marks_pending_review(self, conn, data_dir, seeded_meme):
        annotate_meme(conn, vlm_returning(is_meme=False, confidence=0.95),
                      seeded_meme, data_dir=data_dir)
        # 標註仍落庫（人工複核要看得到判定內容），但狀態轉待審
        assert repo.get_annotation(conn, seeded_meme.meme_id) is not None
        assert repo.get_meme(conn, seeded_meme.meme_id).status == "pending_review"

    def test_very_low_confidence_marks_pending_review(self, conn, data_dir, seeded_meme):
        # 門檻 0.5：真正很低（< 0.5）才進複核
        annotate_meme(conn, vlm_returning(confidence=0.4), seeded_meme, data_dir=data_dir)
        assert repo.get_meme(conn, seeded_meme.meme_id).status == "pending_review"

    def test_moderate_confidence_auto_approved(self, conn, data_dir, seeded_meme):
        # 模型對正常梗圖多給 0.6（實證：積壓佇列 79/92 剛好 0.6，全是 is_meme=true 好圖）
        annotate_meme(conn, vlm_returning(confidence=0.6), seeded_meme, data_dir=data_dir)
        assert repo.get_meme(conn, seeded_meme.meme_id).status == "active"
