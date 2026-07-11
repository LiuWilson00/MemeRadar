"""P1-5 整合測試：向量檢索 + metadata 過濾（規格：docs/04 §2.3）。

驗收：Top-K + franchise / category / NSFW 過濾查詢通過整合測試。
"""

import pytest

from memeradar.matching.search import SearchFilters, SqliteBruteForceSearcher
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Embedding, Meme, MemeAnnotation, new_id

SIGNATURE = "fake-embed@v1|doc-v1"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


def seed_meme(
    conn,
    vector,
    *,
    franchise="海綿寶寶",
    categories=("卡通動畫",),
    nsfw=False,
    is_meme=True,
    status="active",
    signature=SIGNATURE,
    usage_hints=("測試用途",),
) -> Meme:
    meme = Meme(meme_id=new_id("m"), image_uri="x.png", sha256=new_id("h").ljust(64, "0")[:64])
    repo.insert_meme(conn, meme)
    repo.upsert_annotation(
        conn,
        MemeAnnotation(
            meme_id=meme.meme_id,
            model_version="labeler-v1@claude-sonnet-5",
            is_meme=is_meme,
            nsfw=nsfw,
            ocr_text="",
            description="測試",
            franchise=franchise,
            emotions=["無奈"],
            usage_hints=list(usage_hints),
            categories=list(categories),
            confidence=0.9,
        ),
    )
    repo.add_embedding(
        conn,
        Embedding(meme_id=meme.meme_id, kind="text_retrieval", model=signature, vector=vector),
    )
    if status != "active":
        repo.set_status(conn, meme.meme_id, status)
    return meme


class TestTopKAndScoring:
    def test_orders_by_cosine_and_respects_k(self, conn):
        exact = seed_meme(conn, [1.0, 0.0])          # cos = 1.0
        close = seed_meme(conn, [0.9, 0.4358899])    # cos ≈ 0.9
        far = seed_meme(conn, [0.0, 1.0])            # cos = 0.0

        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        hits = searcher.search([1.0, 0.0], k=2, filters=SearchFilters())

        assert [h.meme_id for h in hits] == [exact.meme_id, close.meme_id]
        assert hits[0].similarity == pytest.approx(1.0)
        assert hits[1].similarity == pytest.approx(0.9, abs=1e-6)
        assert far.meme_id not in {h.meme_id for h in hits}

    def test_min_similarity_threshold(self, conn):
        seed_meme(conn, [1.0, 0.0])
        seed_meme(conn, [0.0, 1.0])  # cos = 0，低於門檻

        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        hits = searcher.search([1.0, 0.0], k=10, filters=SearchFilters(), min_similarity=0.35)

        assert len(hits) == 1

    def test_unnormalized_vectors_still_cosine(self, conn):
        # 餘弦不受向量長度影響（FakeEmbedder 等後端不保證 normalize）
        seed_meme(conn, [10.0, 0.0])
        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        hits = searcher.search([0.5, 0.0], k=1, filters=SearchFilters())
        assert hits[0].similarity == pytest.approx(1.0)

    def test_hit_carries_annotation_for_rerank(self, conn):
        seed_meme(conn, [1.0, 0.0], usage_hints=("被指責時自嘲",))
        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        hits = searcher.search([1.0, 0.0], k=1, filters=SearchFilters())
        assert hits[0].annotation.usage_hints == ["被指責時自嘲"]


class TestMetadataFilters:
    def test_franchise_filter_with_alias_normalization(self, conn):
        sponge = seed_meme(conn, [1.0, 0.0], franchise="海綿寶寶")
        seed_meme(conn, [1.0, 0.0], franchise="甄嬛傳")

        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        # 過濾條件用別名 "SpongeBob"，應正規化後命中「海綿寶寶」
        hits = searcher.search(
            [1.0, 0.0], k=10, filters=SearchFilters(franchises=("SpongeBob",))
        )
        assert [h.meme_id for h in hits] == [sponge.meme_id]

    def test_category_filter_json_membership(self, conn):
        cartoon = seed_meme(conn, [1.0, 0.0], categories=("卡通動畫",))
        seed_meme(conn, [1.0, 0.0], categories=("戲劇影視",))
        both = seed_meme(conn, [1.0, 0.0], categories=("戲劇影視", "卡通動畫"))

        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        hits = searcher.search(
            [1.0, 0.0], k=10, filters=SearchFilters(categories=("卡通動畫",))
        )
        assert {h.meme_id for h in hits} == {cartoon.meme_id, both.meme_id}

    def test_nsfw_excluded_by_default_and_includable(self, conn):
        safe = seed_meme(conn, [1.0, 0.0])
        spicy = seed_meme(conn, [1.0, 0.0], nsfw=True)

        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        default_hits = searcher.search([1.0, 0.0], k=10, filters=SearchFilters())
        assert {h.meme_id for h in default_hits} == {safe.meme_id}

        opt_in = searcher.search(
            [1.0, 0.0], k=10, filters=SearchFilters(exclude_nsfw=False)
        )
        assert {h.meme_id for h in opt_in} == {safe.meme_id, spicy.meme_id}

    def test_combined_filters(self, conn):
        target = seed_meme(conn, [1.0, 0.0], franchise="海綿寶寶", categories=("卡通動畫",))
        seed_meme(conn, [1.0, 0.0], franchise="海綿寶寶", categories=("卡通動畫",), nsfw=True)
        seed_meme(conn, [1.0, 0.0], franchise="甄嬛傳", categories=("卡通動畫",))
        seed_meme(conn, [1.0, 0.0], franchise="海綿寶寶", categories=("繪圖創作",))

        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        hits = searcher.search(
            [1.0, 0.0],
            k=10,
            filters=SearchFilters(
                franchises=("海綿寶寶",), categories=("卡通動畫",), exclude_nsfw=True
            ),
        )
        assert [h.meme_id for h in hits] == [target.meme_id]

    def test_empty_filters_return_all_active(self, conn):
        seed_meme(conn, [1.0, 0.0])
        seed_meme(conn, [0.5, 0.5], franchise="甄嬛傳", categories=("戲劇影視",))
        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        assert len(searcher.search([1.0, 0.0], k=10, filters=SearchFilters())) == 2


class TestIndexHygiene:
    def test_excludes_removed_pending_and_non_meme(self, conn):
        seed_meme(conn, [1.0, 0.0], status="removed")
        seed_meme(conn, [1.0, 0.0], status="pending_review")
        seed_meme(conn, [1.0, 0.0], is_meme=False)
        active = seed_meme(conn, [1.0, 0.0])

        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        hits = searcher.search([1.0, 0.0], k=10, filters=SearchFilters())
        assert [h.meme_id for h in hits] == [active.meme_id]

    def test_only_matching_signature_searched(self, conn):
        seed_meme(conn, [1.0, 0.0], signature="other-model|doc-v9")
        current = seed_meme(conn, [1.0, 0.0])

        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        hits = searcher.search([1.0, 0.0], k=10, filters=SearchFilters())
        assert [h.meme_id for h in hits] == [current.meme_id]

    def test_dimension_mismatch_raises(self, conn):
        seed_meme(conn, [1.0, 0.0])
        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        with pytest.raises(ValueError, match="維度"):
            searcher.search([1.0, 0.0, 0.0], k=1, filters=SearchFilters())
