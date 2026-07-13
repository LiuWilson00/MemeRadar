"""P3-3 測試：去重三層漏斗（規格：docs/02 §4、§8）。

L1/L2 用真實 SHA256 / pHash（本地快速）；L3 邏輯用注入的假 image embedder，
CLIP 語意（同模板不同字不誤殺）另以真模型煙霧驗證。
"""

import io

import pytest
from PIL import Image, ImageDraw

from memeradar.ingestion.dedup import (
    Deduplicator,
    absorb_duplicate,
    hotness_gain,
    maybe_upgrade_image,
    resolve_pending_reviews,
)
from memeradar.ingestion.seed_import import import_image_bytes
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.hotness import compute_hotness
from memeradar.shared.models import MemeAnnotation, MemeSource, new_id


def png_bytes(draw_fn=None, size=(400, 400), color=(200, 40, 40)) -> bytes:
    img = Image.new("RGB", size, color)
    if draw_fn:
        draw_fn(ImageDraw.Draw(img))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_recompress(content: bytes, quality: int = 60) -> bytes:
    buffer = io.BytesIO()
    Image.open(io.BytesIO(content)).convert("RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def scene(seed: int):
    """畫出彼此差異大的圖（避免純色圖 pHash 全零誤撞）。"""

    def draw(d: ImageDraw.ImageDraw):
        for i in range(6):
            x = (seed * 37 + i * 61) % 320
            y = (seed * 53 + i * 89) % 320
            fill = (i * 40, 255 - i * 30, seed * 25)
            d.ellipse((x, y, x + 60 + seed * 7, y + 40 + i * 9), fill=fill)
            d.rectangle((y, x, y + 30 + i * 11, x + 50), outline=(255, 255, 255), width=3)

    return draw


class FakeImageEmbedder:
    """依內容路由固定向量，控制 L3 相似度。"""

    model_id = "fake-clip@v1"

    def __init__(self, routes: dict[bytes, list[float]]):
        self.routes = routes

    def embed_image(self, content: bytes) -> list[float]:
        return self.routes[content]


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


def import_and_register(conn, data_dir, dedup, content):
    meme, status = import_image_bytes(conn, content, data_dir=data_dir)
    assert status == "imported"
    dedup.register(meme, content)
    return meme


class TestThreeLayerFunnel:
    def test_l1_exact_bytes_caught_by_sha256(self, conn, data_dir):
        dedup = Deduplicator(conn)
        original = png_bytes(scene(1))
        meme = import_and_register(conn, data_dir, dedup, original)

        result = dedup.check(original)

        assert result.decision == "duplicate"
        assert result.layer == "sha256"
        assert result.matched_meme_id == meme.meme_id

    def test_l2_recompressed_goes_to_review(self, conn, data_dir):
        # pHash 對「同模板不同字」盲目（真實 CLIP 煙霧實證，漢明距離可為 0），
        # 故 L2 命中不自動判重，一律進佇列，待標註後以 OCR 裁決
        dedup = Deduplicator(conn)
        original = png_bytes(scene(1))
        meme = import_and_register(conn, data_dir, dedup, original)

        result = dedup.check(jpeg_recompress(original))

        assert result.decision == "review"
        assert result.layer == "phash"
        assert result.matched_meme_id == meme.meme_id

    def test_different_image_is_new(self, conn, data_dir):
        dedup = Deduplicator(conn)
        import_and_register(conn, data_dir, dedup, png_bytes(scene(1)))

        result = dedup.check(png_bytes(scene(5), color=(20, 20, 200)))

        assert result.decision == "new"

    def test_l3_high_similarity_goes_to_review_not_auto(self, conn, data_dir):
        # 真實煙霧實證：同模板不同字 CLIP 可達 0.993 → 高分也不得自動判重
        original = png_bytes(scene(1))
        variant = png_bytes(scene(5), color=(20, 20, 200))  # pHash 不同 → 交給 L3
        embedder = FakeImageEmbedder({original: [1.0, 0.0], variant: [0.99, 0.141]})
        dedup = Deduplicator(conn, image_embedder=embedder)
        meme = import_and_register(conn, data_dir, dedup, original)

        result = dedup.check(variant)

        assert result.decision == "review"
        assert result.layer == "clip"
        assert result.matched_meme_id == meme.meme_id
        assert result.score == pytest.approx(0.99, abs=0.001)

    def test_l3_below_review_band_is_new(self, conn, data_dir):
        original = png_bytes(scene(1))
        variant = png_bytes(scene(5), color=(20, 20, 200))
        embedder = FakeImageEmbedder({original: [1.0, 0.0], variant: [0.80, 0.60]})
        dedup = Deduplicator(conn, image_embedder=embedder)
        import_and_register(conn, data_dir, dedup, original)

        assert dedup.check(variant).decision == "new"

    def test_without_clip_embedder_l3_skipped(self, conn, data_dir):
        dedup = Deduplicator(conn)  # 無 image_embedder → 只跑 L1/L2
        import_and_register(conn, data_dir, dedup, png_bytes(scene(1)))
        assert dedup.check(png_bytes(scene(5), color=(20, 20, 200))).decision == "new"

    def test_register_persists_phash_and_vector(self, conn, data_dir):
        original = png_bytes(scene(1))
        embedder = FakeImageEmbedder({original: [1.0, 0.0]})
        dedup = Deduplicator(conn, image_embedder=embedder)
        meme = import_and_register(conn, data_dir, dedup, original)

        assert repo.get_meme(conn, meme.meme_id).phash  # pHash 已寫回
        vectors = repo.get_embeddings(conn, meme.meme_id, kind="image_dedup")
        assert len(vectors) == 1
        assert vectors[0].vector == pytest.approx([1.0, 0.0])


class TestAbsorbAndUpgrade:
    def test_absorb_merges_source_and_accumulates_hotness(self, conn, data_dir):
        dedup = Deduplicator(conn)
        meme = import_and_register(conn, data_dir, dedup, png_bytes(scene(1)))

        absorb_duplicate(
            conn,
            meme.meme_id,
            MemeSource(
                source_id=new_id("s"),
                meme_id=meme.meme_id,
                platform="reddit",
                post_url="https://reddit.com/r/memes/p9",
                post_title="又見這張",
                top_comments=["笑死"],
                upvotes=900,
            ),
        )

        sources = repo.list_sources(conn, meme.meme_id)
        assert len(sources) == 2  # 原 manual + 新 reddit
        assert sources[-1].platform == "reddit"
        # 同圖再現＝「這梗還活著」：互動分累加、刷新最後出現時間（docs/06 §3.1）
        # engagement = 首次匯入 manual 來源 + 再現的 reddit(900 讚)
        after = repo.get_meme(conn, meme.meme_id)
        assert after.engagement == pytest.approx(hotness_gain(None) + hotness_gain(900))
        assert after.last_seen_at is not None
        assert after.hotness == pytest.approx(
            compute_hotness(after.engagement, after.last_seen_at)
        )

    def test_hotness_gain_monotonic_and_log_scaled(self):
        assert hotness_gain(0) == pytest.approx(1.0)  # 重複出現本身就是訊號
        assert hotness_gain(9) == pytest.approx(2.0)
        assert hotness_gain(999) == pytest.approx(4.0)
        assert hotness_gain(None) == pytest.approx(1.0)

    def test_upgrade_replaces_with_higher_resolution(self, conn, data_dir):
        dedup = Deduplicator(conn)
        low = png_bytes(scene(1), size=(200, 200))
        meme = import_and_register(conn, data_dir, dedup, low)
        high = png_bytes(scene(1), size=(800, 800))

        upgraded = maybe_upgrade_image(conn, meme.meme_id, high, data_dir=data_dir)

        assert upgraded is True
        refreshed = repo.get_meme(conn, meme.meme_id)
        assert refreshed.width == 800
        assert (data_dir / refreshed.image_uri).exists()
        assert repo.find_meme_by_sha256(conn, refreshed.sha256).meme_id == meme.meme_id

    def test_upgrade_skipped_for_lower_resolution(self, conn, data_dir):
        dedup = Deduplicator(conn)
        meme = import_and_register(conn, data_dir, dedup, png_bytes(scene(1), size=(400, 400)))

        assert maybe_upgrade_image(
            conn, meme.meme_id, png_bytes(scene(1), size=(100, 100)), data_dir=data_dir
        ) is False
        assert repo.get_meme(conn, meme.meme_id).width == 400


class TestReviewQueue:
    def test_add_and_list_pending_reviews(self, conn, data_dir):
        dedup = Deduplicator(conn)
        a = import_and_register(conn, data_dir, dedup, png_bytes(scene(1)))
        b = import_and_register(conn, data_dir, dedup, png_bytes(scene(5), color=(0, 0, 220)))

        repo.add_dedup_review(conn, meme_id=b.meme_id, matched_meme_id=a.meme_id,
                              layer="clip", score=0.94)

        pending = repo.list_dedup_reviews(conn)
        assert len(pending) == 1
        assert pending[0]["meme_id"] == b.meme_id
        assert pending[0]["matched_meme_id"] == a.meme_id
        assert pending[0]["score"] == pytest.approx(0.94)
        assert pending[0]["resolution"] == "pending"


def annotate(conn, meme_id: str, ocr: str) -> None:
    repo.upsert_annotation(conn, MemeAnnotation(
        meme_id=meme_id, model_version="v", ocr_text=ocr,
        emotions=["無奈"], usage_hints=["測試"], categories=["卡通動畫"],
    ))


class TestPostAnnotationResolution:
    """標註後自動裁決（docs/02 §4 備援方案升級為主方案）：
    視覺相近（已在佇列）+ OCR 相同 → 合併；OCR 不同（同模板不同字）→ 判為不同。"""

    def _queued_pair(self, conn, data_dir):
        dedup = Deduplicator(conn)
        kept = import_and_register(conn, data_dir, dedup, png_bytes(scene(1)))
        dup = import_and_register(conn, data_dir, dedup, png_bytes(scene(2)))
        repo.add_source(conn, MemeSource(
            source_id=new_id("s"), meme_id=dup.meme_id, platform="reddit",
            post_url="https://reddit.com/p2", upvotes=99,
        ))
        repo.add_dedup_review(conn, meme_id=dup.meme_id, matched_meme_id=kept.meme_id,
                              layer="phash", score=0.0)
        return kept, dup

    def test_same_ocr_auto_merges(self, conn, data_dir):
        kept, dup = self._queued_pair(conn, data_dir)
        annotate(conn, kept.meme_id, "我就爛")
        annotate(conn, dup.meme_id, " 我就爛　")  # 空白差異視為相同

        stats = resolve_pending_reviews(conn)

        assert stats["merged"] == 1
        assert repo.get_meme(conn, dup.meme_id).status == "removed"
        # 來源已搬到保留的主圖、熱度累加
        platforms = {s.platform for s in repo.list_sources(conn, kept.meme_id)}
        assert "reddit" in platforms
        kept_after = repo.get_meme(conn, kept.meme_id)
        # 保留者自己的 manual 來源 + 轉移重複者全部來源（manual + reddit 99 讚）
        assert kept_after.engagement == pytest.approx(
            2 * hotness_gain(None) + hotness_gain(99)
        )
        assert kept_after.last_seen_at is not None
        assert kept_after.hotness > 0
        assert repo.list_dedup_reviews(conn, resolution="merged")

    def test_different_ocr_resolved_distinct(self, conn, data_dir):
        kept, dup = self._queued_pair(conn, data_dir)
        annotate(conn, kept.meme_id, "我就爛")
        annotate(conn, dup.meme_id, "太神啦")  # 同模板不同字

        stats = resolve_pending_reviews(conn)

        assert stats["distinct"] == 1
        assert repo.get_meme(conn, dup.meme_id).status == "active"  # 不誤殺
        assert repo.list_dedup_reviews(conn, resolution="distinct")

    def test_unannotated_stays_pending(self, conn, data_dir):
        self._queued_pair(conn, data_dir)  # 尚未標註

        stats = resolve_pending_reviews(conn)

        assert stats["pending"] == 1
        assert len(repo.list_dedup_reviews(conn)) == 1
