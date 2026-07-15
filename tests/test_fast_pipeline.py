"""快速模式管線測試：OCR / NV-CLIP → 向量檢索，全程無 VLM/LLM。

回應形狀須與精準模式一致（query_id / intent / results / debug）。
"""

from __future__ import annotations

import pytest

from memeradar.api.pipeline import run_fast_recommendation
from memeradar.api.schemas import RecommendRequest
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Embedding, Meme, MemeAnnotation, new_id

SIGNATURE = "fake-embed@v1|doc-v1"


class StubOcr:
    def __init__(self, text: str):
        self.text = text
        self.calls = 0

    def ocr(self, image_bytes: bytes) -> str:
        self.calls += 1
        return self.text


class StubClassifier:
    def __init__(self, labels: list[str]):
        self.labels = labels
        self.calls = 0

    def classify(self, image_bytes: bytes, *, top_k: int = 3, min_score: float = 0.0):
        self.calls += 1
        return self.labels[:top_k]


class RoutedEmbedder:
    model_id = "fake-embed@v1"

    def __init__(self, routes: dict[str, list[float]]):
        self.routes = routes
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.routes.get(t, [0.0, 0.0]) for t in texts]


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


def seed(conn, vector, *, franchise="海綿寶寶") -> Meme:
    meme = Meme(meme_id=new_id("m"), image_uri="x.png", sha256=new_id("h").ljust(64, "0")[:64])
    repo.insert_meme(conn, meme)
    repo.upsert_annotation(
        conn,
        MemeAnnotation(
            meme_id=meme.meme_id,
            model_version="labeler-v1@claude-sonnet-5",
            ocr_text="x",
            description="測試",
            franchise=franchise,
            emotions=["無奈"],
            usage_hints=["測試"],
            categories=["卡通動畫"],
            confidence=0.9,
        ),
    )
    repo.add_embedding(
        conn,
        Embedding(meme_id=meme.meme_id, kind="text_retrieval", model=SIGNATURE, vector=vector),
    )
    return meme


def _request(**kw) -> RecommendRequest:
    return RecommendRequest(input_type=kw.pop("input_type", "screenshot"), client_id="c1", **kw)


class TestFastPipeline:
    def test_ocr_path_retrieves_and_shapes_like_normal(self, conn):
        target = seed(conn, [1.0, 0.0])
        ocr = StubOcr("老闆又要我加班")
        embedder = RoutedEmbedder({"老闆又要我加班": [1.0, 0.0]})

        out = run_fast_recommendation(
            conn, ocr, StubClassifier([]), embedder, _request(), image_bytes=b"\x89PNGxx"
        )

        # 回應形狀與精準模式一致
        assert set(out) == {"query_id", "intent", "results", "debug"}
        assert out["results"][0]["meme_id"] == target.meme_id
        assert out["debug"]["fast"]["source"] == "ocr"
        assert out["debug"]["fast"]["ocr_text"] == "老闆又要我加班"
        assert out["debug"]["rerank_fallback"] is False
        assert ocr.calls == 1

    def test_textless_image_falls_back_to_nvclip(self, conn):
        target = seed(conn, [1.0, 0.0])
        ocr = StubOcr("")  # 沒字
        classifier = StubClassifier(["生氣", "無奈"])
        embedder = RoutedEmbedder({"生氣 無奈": [1.0, 0.0]})

        out = run_fast_recommendation(
            conn, ocr, classifier, embedder, _request(), image_bytes=b"\x89PNGxx"
        )

        assert out["debug"]["fast"]["source"] == "nvclip"
        assert out["debug"]["fast"]["labels"] == ["生氣", "無奈"]
        assert out["results"][0]["meme_id"] == target.meme_id
        assert classifier.calls == 1

    def test_ocr_used_when_text_present_classifier_not_called(self, conn):
        seed(conn, [1.0, 0.0])
        classifier = StubClassifier(["生氣"])
        run_fast_recommendation(
            conn, StubOcr("有字內容"), classifier,
            RoutedEmbedder({"有字內容": [1.0, 0.0]}), _request(), image_bytes=b"png",
        )
        assert classifier.calls == 0  # 有字就不走 NV-CLIP

    def test_text_input_uses_conversation(self, conn):
        target = seed(conn, [0.0, 1.0])
        embedder = RoutedEmbedder({"我今天心情爆好": [0.0, 1.0]})
        req = _request(
            input_type="text", conversation=[{"speaker": "other", "text": "我今天心情爆好"}]
        )

        out = run_fast_recommendation(conn, StubOcr(""), StubClassifier([]), embedder, req)

        assert out["debug"]["fast"]["source"] == "text"
        assert out["results"][0]["meme_id"] == target.meme_id

    def test_textless_image_degrades_when_classifier_unavailable(self, conn):
        # NV-CLIP 未啟用（classify 拋 404）→ 沒字圖退回空結果，任務不崩潰
        seed(conn, [1.0, 0.0])

        class Broken:
            def classify(self, image_bytes, *, top_k=3, min_score=0.0):
                raise RuntimeError("404 Function not found for account")

        out = run_fast_recommendation(
            conn, StubOcr(""), Broken(), RoutedEmbedder({}), _request(), image_bytes=b"png"
        )
        assert out["debug"]["fast"]["source"] == "nvclip"
        assert out["debug"]["fast"]["labels"] == []
        assert out["results"] == []

    def test_textless_image_degrades_when_classifier_is_none(self, conn):
        seed(conn, [1.0, 0.0])
        out = run_fast_recommendation(
            conn, StubOcr(""), None, RoutedEmbedder({}), _request(), image_bytes=b"png"
        )
        assert out["results"] == []

    def test_empty_input_returns_empty_results_no_crash(self, conn):
        seed(conn, [1.0, 0.0])
        embedder = RoutedEmbedder({})
        req = _request(input_type="text", conversation=[])

        out = run_fast_recommendation(conn, StubOcr(""), StubClassifier([]), embedder, req)

        assert out["results"] == []
        assert out["intent"]["strategies"] == []
        assert embedder.calls == []  # 無策略 → 不浪費 embed
