"""P1-1 標註器測試（規格：docs/03 §2）。

不打真實 API：以 stub client 驗證 orchestration（prompt 組裝、上下文注入、
版本化、pending_review 規則）；schema 合法性由 pydantic 模型自身驗證。
"""

import pytest
from PIL import Image
from pydantic import ValidationError

from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Meme, MemeSource, new_id
from memeradar.understanding.annotator import (
    ANNOTATION_PROMPT_VERSION,
    DEFAULT_ANNOTATION_MODEL,
    AnnotationResult,
    annotate_meme,
    build_system_prompt,
    build_user_content,
    media_type_for,
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
        assert [e.value for e in result.emotions] == ["擺爛", "理直氣壯"]

    def test_unknown_emotion_rejected(self):
        # 封閉集：taxonomy 之外的情緒直接驗證失敗（enum 也會進 API 的 JSON schema）
        with pytest.raises(ValidationError):
            AnnotationResult(**valid_payload(emotions=["開心到飛起"]))

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

    def test_user_content_image_first_then_text_with_context(self):
        content = build_user_content(
            image_bytes=b"fake-bytes",
            media_type="image/png",
            context_text="標題：上班的我\n熱門留言：笑死這就是我",
        )
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "base64"
        assert content[0]["source"]["media_type"] == "image/png"
        assert content[1]["type"] == "text"
        assert "上班的我" in content[1]["text"]
        assert "旁證" in content[1]["text"]  # 上下文標明為旁證而非事實

    def test_user_content_without_context(self):
        content = build_user_content(image_bytes=b"x", media_type="image/png", context_text=None)
        assert len(content) == 2
        assert "旁證" not in content[1]["text"]


class TestMediaType:
    def test_known_extensions(self):
        assert media_type_for("images/a.png") == "image/png"
        assert media_type_for("images/a.jpg") == "image/jpeg"
        assert media_type_for("images/a.webp") == "image/webp"

    def test_unknown_extension_raises(self):
        with pytest.raises(ValueError):
            media_type_for("images/a.gif")


# ── annotate_meme orchestration（stub client）─────────────────────────


class StubResponse:
    def __init__(self, parsed_output, stop_reason="end_turn"):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason


class StubClient:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []
        outer = self

        class _Messages:
            def parse(self, **kwargs):
                outer.calls.append(kwargs)
                return outer.response

        self.messages = _Messages()


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
        client = StubClient(StubResponse(AnnotationResult(**valid_payload())))

        result = annotate_meme(conn, client, seeded_meme, data_dir=data_dir)

        assert result is not None
        stored = repo.get_annotation(conn, seeded_meme.meme_id)
        assert stored.ocr_text == "我就爛"
        assert stored.emotions == ["擺爛", "理直氣壯"]  # 存回純字串
        assert stored.franchise == "海綿寶寶"  # 已正規化
        assert stored.model_version == f"{ANNOTATION_PROMPT_VERSION}@{DEFAULT_ANNOTATION_MODEL}"
        assert repo.get_meme(conn, seeded_meme.meme_id).status == "active"

        # 呼叫參數：預設模型 + structured output schema + 上下文注入
        call = client.calls[0]
        assert call["model"] == DEFAULT_ANNOTATION_MODEL
        assert call["output_format"] is AnnotationResult
        user_text = call["messages"][0]["content"][1]["text"]
        assert "海綿寶寶" in user_text and "笑死這就是我" in user_text

    def test_refusal_marks_pending_review_without_annotation(self, conn, data_dir, seeded_meme):
        client = StubClient(StubResponse(parsed_output=None, stop_reason="refusal"))

        result = annotate_meme(conn, client, seeded_meme, data_dir=data_dir)

        assert result is None
        assert repo.get_annotation(conn, seeded_meme.meme_id) is None
        assert repo.get_meme(conn, seeded_meme.meme_id).status == "pending_review"

    def test_non_meme_marks_pending_review(self, conn, data_dir, seeded_meme):
        client = StubClient(
            StubResponse(AnnotationResult(**valid_payload(is_meme=False, confidence=0.95)))
        )
        annotate_meme(conn, client, seeded_meme, data_dir=data_dir)
        # 標註仍落庫（人工複核要看得到判定內容），但狀態轉待審
        assert repo.get_annotation(conn, seeded_meme.meme_id) is not None
        assert repo.get_meme(conn, seeded_meme.meme_id).status == "pending_review"

    def test_very_low_confidence_marks_pending_review(self, conn, data_dir, seeded_meme):
        # 門檻 0.5：真正很低（< 0.5）才進複核
        client = StubClient(StubResponse(AnnotationResult(**valid_payload(confidence=0.4))))
        annotate_meme(conn, client, seeded_meme, data_dir=data_dir)
        assert repo.get_meme(conn, seeded_meme.meme_id).status == "pending_review"

    def test_moderate_confidence_auto_approved(self, conn, data_dir, seeded_meme):
        # 模型對正常梗圖多給 0.6（實證：積壓佇列 79/92 剛好 0.6，全是 is_meme=true 好圖）
        # → 0.6 不該進複核，否則佇列被正常梗圖灌爆
        client = StubClient(StubResponse(AnnotationResult(**valid_payload(confidence=0.6))))
        annotate_meme(conn, client, seeded_meme, data_dir=data_dir)
        assert repo.get_meme(conn, seeded_meme.meme_id).status == "active"
