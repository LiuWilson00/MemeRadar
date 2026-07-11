"""P1-6 測試：CLI 檢索驗證工具（一句話 query → Top-K 排版輸出）。"""

import pytest

from memeradar.matching.cli import format_hits, run_query
from memeradar.matching.search import SearchFilters
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Embedding, Meme, MemeAnnotation, new_id

SIGNATURE = "fake-embed@v1|doc-v1"


class FakeEmbedder:
    model_id = "fake-embed@v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


def seed(conn, vector, *, ocr="我就爛", franchise="海綿寶寶", hints=("被指責時自嘲",)) -> Meme:
    meme = Meme(meme_id=new_id("m"), image_uri="x.png", sha256=new_id("h").ljust(64, "0")[:64])
    repo.insert_meme(conn, meme)
    repo.upsert_annotation(
        conn,
        MemeAnnotation(
            meme_id=meme.meme_id,
            model_version="labeler-v1@claude-sonnet-5",
            ocr_text=ocr,
            description="測試",
            franchise=franchise,
            emotions=["擺爛"],
            usage_hints=list(hints),
            categories=["卡通動畫"],
            confidence=0.9,
        ),
    )
    repo.add_embedding(
        conn,
        Embedding(meme_id=meme.meme_id, kind="text_retrieval", model=SIGNATURE, vector=vector),
    )
    return meme


class TestRunQuery:
    def test_embeds_query_and_returns_ordered_hits(self, conn):
        best = seed(conn, [1.0, 0.0])
        seed(conn, [0.0, 1.0])

        hits = run_query(conn, FakeEmbedder(), "想擺爛", k=10, filters=SearchFilters())

        assert hits[0].meme_id == best.meme_id
        assert hits[0].similarity == pytest.approx(1.0)

    def test_filters_are_applied(self, conn):
        sponge = seed(conn, [1.0, 0.0], franchise="海綿寶寶")
        seed(conn, [1.0, 0.0], franchise="甄嬛傳")

        hits = run_query(
            conn,
            FakeEmbedder(),
            "想擺爛",
            k=10,
            filters=SearchFilters(franchises=("海綿寶寶",)),
        )
        assert [h.meme_id for h in hits] == [sponge.meme_id]

    def test_min_similarity_passthrough(self, conn):
        seed(conn, [0.0, 1.0])  # cos = 0
        hits = run_query(
            conn, FakeEmbedder(), "想擺爛", k=10, filters=SearchFilters(), min_similarity=0.35
        )
        assert hits == []


class TestFormatHits:
    def test_output_contains_rank_score_and_tags(self, conn):
        seed(conn, [1.0, 0.0], ocr="我就爛", hints=("被指責時自嘲", "表達躺平"))
        hits = run_query(conn, FakeEmbedder(), "想擺爛", k=10, filters=SearchFilters())

        text = format_hits(hits, signature=SIGNATURE, indexed_count=1)

        assert "#1" in text
        assert "1.000" in text  # 相似度
        assert "我就爛" in text  # OCR
        assert "擺爛" in text  # 情緒
        assert "被指責時自嘲" in text  # 用途（首條）
        assert "海綿寶寶" in text  # 出處

    def test_empty_index_hints_to_run_embedding(self):
        text = format_hits([], signature=SIGNATURE, indexed_count=0)
        assert SIGNATURE in text
        assert "memeradar.understanding.embedding" in text  # 指引先跑向量化

    def test_filtered_out_hints_to_relax(self):
        text = format_hits([], signature=SIGNATURE, indexed_count=42)
        assert "42" in text
        assert "min-similarity" in text  # 指引放寬門檻或過濾
