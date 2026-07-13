"""P1-4 測試：檢索文件組裝 + embedding 介面（規格：docs/03 §3）。

驗收對應：
- `embed()` 可切換後端 → 後端註冊表 + 不同 model_id 的向量可並存
- 模板與模型版本入庫 → embeddings.model 存「模型|模板版本」簽名
"""

import importlib.util

import pytest

from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Meme, MemeAnnotation, new_id
from memeradar.understanding.embedding import (
    embed_pending_memes,
    embedding_signature,
    get_embedder,
)
from memeradar.understanding.retrieval_doc import (
    RETRIEVAL_DOC_VERSION,
    build_retrieval_document,
)


def make_annotation(meme_id: str, **overrides) -> MemeAnnotation:
    fields = {
        "meme_id": meme_id,
        "model_version": "labeler-v1@claude-sonnet-5",
        "is_meme": True,
        "ocr_text": "我就爛",
        "description": "海綿寶寶攤手站立，表情理直氣壯",
        "characters": ["海綿寶寶"],
        "franchise": "海綿寶寶",
        "emotions": ["擺爛", "理直氣壯"],
        "usage_hints": ["被指責時理直氣壯自嘲", "表達躺平態度"],
        "categories": ["卡通動畫"],
        "confidence": 0.93,
    }
    fields.update(overrides)
    return MemeAnnotation(**fields)


class TestRetrievalDocument:
    def test_template_format_usage_hints_first(self):
        doc = build_retrieval_document(make_annotation("m_x"))
        assert doc == (
            "用途：被指責時理直氣壯自嘲\n"
            "用途：表達躺平態度\n"
            "情緒：擺爛、理直氣壯\n"
            "圖中文字：我就爛\n"
            "畫面：海綿寶寶攤手站立，表情理直氣壯\n"
            "角色：海綿寶寶；出處：海綿寶寶"
        )

    def test_empty_ocr_line_omitted(self):
        doc = build_retrieval_document(make_annotation("m_x", ocr_text=""))
        assert "圖中文字" not in doc

    def test_no_characters_and_no_franchise(self):
        doc = build_retrieval_document(
            make_annotation("m_x", characters=[], franchise=None)
        )
        assert doc.endswith("角色：無")
        assert "出處" not in doc

    def test_deterministic(self):
        ann = make_annotation("m_x")
        assert build_retrieval_document(ann) == build_retrieval_document(ann)


class FakeEmbedder:
    model_id = "fake-embed@v1"

    def __init__(self):
        self.seen: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[float(len(t)), 1.0, -0.5] for t in texts]


class AnotherFakeEmbedder(FakeEmbedder):
    model_id = "fake-embed@v2"


class TestEmbedderInterface:
    def test_signature_couples_model_and_doc_version(self):
        assert embedding_signature(FakeEmbedder()) == f"fake-embed@v1|{RETRIEVAL_DOC_VERSION}"

    def test_unknown_backend_raises_with_available_list(self):
        with pytest.raises(ValueError, match="bge-m3"):
            get_embedder("nope")

    def test_bge_m3_constructs_lazily(self):
        # 建構不載入模型（重依賴 lazy import），model_id 正確
        embedder = get_embedder("bge-m3")
        assert embedder.model_id == "bge-m3"

    @pytest.mark.skipif(
        importlib.util.find_spec("sentence_transformers") is not None,
        reason="已安裝 sentence-transformers，錯誤提示路徑不適用",
    )
    def test_bge_m3_embed_without_package_gives_install_hint(self):
        embedder = get_embedder("bge-m3")
        with pytest.raises(RuntimeError, match="local-embedding"):
            embedder.embed(["測試"])


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


def seed_annotated_meme(conn, *, is_meme=True, status="active") -> Meme:
    meme = Meme(meme_id=new_id("m"), image_uri="x.png", sha256=new_id("h")[:64].ljust(64, "0"))
    repo.insert_meme(conn, meme)
    repo.upsert_annotation(conn, make_annotation(meme.meme_id, is_meme=is_meme))
    if status != "active":
        repo.set_status(conn, meme.meme_id, status)
    return meme


class TestEmbedPendingMemes:
    def test_embeds_and_stores_versioned_signature(self, conn):
        meme = seed_annotated_meme(conn)
        embedder = FakeEmbedder()

        count = embed_pending_memes(conn, embedder)

        assert count == 1
        embs = repo.get_embeddings(conn, meme.meme_id, kind="text_retrieval")
        assert len(embs) == 1
        assert embs[0].model == f"fake-embed@v1|{RETRIEVAL_DOC_VERSION}"  # 模板與模型版本入庫
        # 向量確實來自檢索文件
        expected_doc = build_retrieval_document(repo.get_annotation(conn, meme.meme_id))
        assert embedder.seen == [expected_doc]
        assert embs[0].vector == pytest.approx([float(len(expected_doc)), 1.0, -0.5])

    def test_rerun_is_idempotent(self, conn):
        seed_annotated_meme(conn)
        embedder = FakeEmbedder()
        assert embed_pending_memes(conn, embedder) == 1
        assert embed_pending_memes(conn, embedder) == 0

    def test_backend_switch_creates_parallel_vectors(self, conn):
        meme = seed_annotated_meme(conn)
        embed_pending_memes(conn, FakeEmbedder())
        count_v2 = embed_pending_memes(conn, AnotherFakeEmbedder())

        assert count_v2 == 1  # 換後端 → 簽名不同 → 需重新向量化
        models = {e.model for e in repo.get_embeddings(conn, meme.meme_id)}
        assert models == {
            f"fake-embed@v1|{RETRIEVAL_DOC_VERSION}",
            f"fake-embed@v2|{RETRIEVAL_DOC_VERSION}",
        }

    def test_excludes_non_meme_pending_and_unannotated(self, conn):
        seed_annotated_meme(conn, is_meme=False)  # 非梗圖
        seed_annotated_meme(conn, status="pending_review")  # 待審
        unannotated = Meme(meme_id=new_id("m"), image_uri="u.png", sha256="e" * 64)
        repo.insert_meme(conn, unannotated)  # 未標註

        assert embed_pending_memes(conn, FakeEmbedder()) == 0

    def test_limit_and_batching(self, conn):
        for _ in range(3):
            seed_annotated_meme(conn)
        embedder = FakeEmbedder()
        assert embed_pending_memes(conn, embedder, limit=2) == 2
        assert embed_pending_memes(conn, embedder) == 1  # 補完剩下一張
