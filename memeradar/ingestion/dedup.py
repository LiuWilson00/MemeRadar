"""P3-3 去重三層漏斗（docs/02 §4，判定策略依真實驗證修訂）。

三層由粗到細、由廉到貴：

| 層 | 方法 | 攔截對象 | 判定 |
|----|------|---------|------|
| L1 | SHA256 | 完全相同檔案 | 相等 → **duplicate（唯一自動判重）** |
| L2 | pHash 漢明距離 ≤ 8 | 重壓縮、輕微縮放、轉檔 | → review（延後裁決） |
| L3 | CLIP 餘弦 ≥ 0.92 | 浮水印、裁邊、色調 | → review（延後裁決） |

**為何 L2/L3 不自動判重**（2026-07-11 真 CLIP 煙霧實證）：pHash 對小面積
文字差異盲目——「同模板不同字」漢明距離可為 0；CLIP 亦可達 0.993。
兩者都無法區分「重壓縮的同一張」與「同模板不同字」，而後者是不同梗圖
（docs/02 §4 邊界）。故採用文件的備援方案為主方案：**標註完成後以
「視覺相近（已在佇列）＋ OCR 相同」自動合併、OCR 不同自動判為不同**
（:func:`resolve_pending_reviews`），無需人工即可正確裁決絕大多數案例。

判為重複時不是丟棄：以 :func:`absorb_duplicate` 合併來源 metadata 並
累加熱度（重複出現本身是「這梗還活著」的訊號，docs/06 §3.1），
:func:`maybe_upgrade_image` 以較高解析度版本替換主圖。
"""

from __future__ import annotations

import hashlib
import io
import math
import sqlite3
from dataclasses import dataclass

import imagehash
from PIL import Image

from memeradar.shared import repository as repo
from memeradar.shared.models import Embedding, Meme, MemeSource

DEFAULT_PHASH_MAX_DISTANCE = 8  # docs/02 §4 起始閾值，依誤判調整
DEFAULT_CLIP_REVIEW_THRESHOLD = 0.92
CLIP_MODEL_ID = "clip-vit-b32"


@dataclass(frozen=True)
class DedupResult:
    decision: str  # "new" | "duplicate" | "review"
    matched_meme_id: str | None = None
    layer: str | None = None  # "sha256" | "phash" | "clip"
    score: float | None = None  # phash 層為漢明距離、clip 層為餘弦相似度


def _cosine(a: list[float], b: list[float]) -> float:
    dot = norm_a = norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


def _phash_of(content: bytes) -> imagehash.ImageHash:
    with Image.open(io.BytesIO(content)) as img:
        return imagehash.phash(img)


class ClipImageEmbedder:
    """CLIP 影像向量（僅供去重 / 以圖搜圖，非檢索主軸——docs/03 §3.2）。

    需 extras ``[local-embedding]``；首次使用下載 clip-ViT-B-32 權重（約 600MB）。
    """

    model_id = CLIP_MODEL_ID

    def __init__(self, device: str | None = None):
        self._device = device
        self._model = None

    def _ensure_loaded(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    '影像去重需要 sentence-transformers：請執行 pip install -e ".[local-embedding]"'
                ) from exc
            self._model = SentenceTransformer("clip-ViT-B-32", device=self._device)
        return self._model

    def embed_image(self, content: bytes) -> list[float]:
        model = self._ensure_loaded()
        with Image.open(io.BytesIO(content)) as img:
            [vector] = model.encode([img.convert("RGB")], normalize_embeddings=True)
        return vector.tolist()


class Deduplicator:
    """三層去重檢查器。快取既有 pHash / 向量，register 時同步更新。"""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        image_embedder=None,  # 缺省時跳過 L3（僅 L1/L2）
        phash_max_distance: int = DEFAULT_PHASH_MAX_DISTANCE,
        clip_review_threshold: float = DEFAULT_CLIP_REVIEW_THRESHOLD,
    ):
        self._conn = conn
        self._embedder = image_embedder
        self._phash_max = phash_max_distance
        self._review = clip_review_threshold
        self._phashes: dict[str, imagehash.ImageHash] | None = None
        self._vectors: dict[str, list[float]] | None = None

    def _known_phashes(self) -> dict[str, imagehash.ImageHash]:
        if self._phashes is None:
            self._phashes = {
                meme_id: imagehash.hex_to_hash(value)
                for meme_id, value in repo.list_phashes(self._conn)
            }
        return self._phashes

    def _known_vectors(self) -> dict[str, list[float]]:
        if self._vectors is None:
            self._vectors = repo.list_embeddings_by_kind(
                self._conn, kind="image_dedup", model=self._embedder.model_id
            )
        return self._vectors

    def check(self, content: bytes) -> DedupResult:
        # L1：完全相同檔案（唯一可安全自動判重的層）
        sha256 = hashlib.sha256(content).hexdigest()
        existing = repo.find_meme_by_sha256(self._conn, sha256)
        if existing is not None:
            return DedupResult("duplicate", existing.meme_id, "sha256")

        # L2：感知雜湊（重壓縮 / 縮放 / 轉檔）——可能是同模板不同字，延後裁決
        candidate_hash = _phash_of(content)
        best_id, best_distance = None, self._phash_max + 1
        for meme_id, known in self._known_phashes().items():
            distance = candidate_hash - known
            if distance < best_distance:
                best_id, best_distance = meme_id, distance
        if best_id is not None and best_distance <= self._phash_max:
            return DedupResult("review", best_id, "phash", float(best_distance))

        # L3：CLIP 語意（浮水印 / 裁邊 / 色調）——同上，延後裁決
        if self._embedder is not None:
            vector = self._embedder.embed_image(content)
            best_id, best_similarity = None, -1.0
            for meme_id, known in self._known_vectors().items():
                similarity = _cosine(vector, known)
                if similarity > best_similarity:
                    best_id, best_similarity = meme_id, similarity
            if best_id is not None and best_similarity >= self._review:
                return DedupResult("review", best_id, "clip", best_similarity)

        return DedupResult("new")

    def register(self, meme: Meme, content: bytes) -> None:
        """新圖入庫後登記 pHash 與影像向量，供之後的比對使用。"""
        candidate_hash = _phash_of(content)
        repo.set_phash(self._conn, meme.meme_id, str(candidate_hash))
        self._known_phashes()[meme.meme_id] = candidate_hash

        if self._embedder is not None:
            vector = self._embedder.embed_image(content)
            repo.add_embedding(
                self._conn,
                Embedding(
                    meme_id=meme.meme_id,
                    kind="image_dedup",
                    model=self._embedder.model_id,
                    vector=vector,
                ),
            )
            self._known_vectors()[meme.meme_id] = vector


def hotness_gain(upvotes: int | None) -> float:
    """重複出現的熱度增量：出現本身 +1，互動數取對數（docs/06 §3.1 的 f）。"""
    return 1.0 + math.log10(1 + (upvotes or 0))


def absorb_duplicate(conn: sqlite3.Connection, existing_meme_id: str, source: MemeSource) -> None:
    """重複圖不丟棄：來源 metadata 追加到既有梗圖、熱度累加。"""
    merged = MemeSource(
        source_id=source.source_id,
        meme_id=existing_meme_id,  # 指向既有主圖
        platform=source.platform,
        post_url=source.post_url,
        post_title=source.post_title,
        top_comments=source.top_comments,
        upvotes=source.upvotes,
        posted_at=source.posted_at,
        crawled_at=source.crawled_at,
    )
    repo.add_source(conn, merged)
    repo.add_hotness(conn, existing_meme_id, hotness_gain(source.upvotes))


def _normalized_ocr(text: str) -> str:
    """OCR 比對用正規化：去除空白（含全形）後小寫。"""
    return "".join(text.split()).replace("　", "").lower()


def resolve_pending_reviews(conn: sqlite3.Connection) -> dict[str, int]:
    """標註後自動裁決佇列（docs/02 §4 備援方案為主方案）。

    兩張都已標註時：OCR 正規化相同 → merged（來源搬移、熱度累加、重複者下架）；
    不同 → distinct（同模板不同字，兩張都保留）。任一未標註 → 維持 pending。
    """
    stats = {"merged": 0, "distinct": 0, "pending": 0}
    for review in repo.list_dedup_reviews(conn, resolution="pending"):
        dup_ann = repo.get_annotation(conn, review["meme_id"])
        kept_ann = repo.get_annotation(conn, review["matched_meme_id"])
        if dup_ann is None or kept_ann is None:
            stats["pending"] += 1
            continue
        if _normalized_ocr(dup_ann.ocr_text) == _normalized_ocr(kept_ann.ocr_text):
            merge_duplicate_into(conn, review["meme_id"], review["matched_meme_id"])
            repo.set_dedup_review_resolution(conn, review["review_id"], "merged")
            stats["merged"] += 1
        else:
            repo.set_dedup_review_resolution(conn, review["review_id"], "distinct")
            stats["distinct"] += 1
    return stats


def merge_duplicate_into(conn: sqlite3.Connection, dup_meme_id: str, kept_meme_id: str) -> None:
    """把重複梗圖併入保留者：來源搬移、熱度累加、重複者下架。"""
    gain = sum(hotness_gain(s.upvotes) for s in repo.list_sources(conn, dup_meme_id)) or 1.0
    repo.move_sources(conn, from_meme_id=dup_meme_id, to_meme_id=kept_meme_id)
    repo.add_hotness(conn, kept_meme_id, gain)
    repo.set_status(conn, dup_meme_id, "removed")


def maybe_upgrade_image(
    conn: sqlite3.Connection, meme_id: str, content: bytes, *, data_dir
) -> bool:
    """新版本解析度較高時替換主圖（docs/02 §4 保留最高解析度）。"""
    meme = repo.get_meme(conn, meme_id)
    try:
        with Image.open(io.BytesIO(content)) as img:
            width, height = img.size
            image_format = img.format
    except OSError:
        return False
    if width * height <= (meme.width or 0) * (meme.height or 0):
        return False

    extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(image_format or "")
    if extension is None:
        return False
    new_uri = f"images/{meme_id}_hd{extension}"
    (data_dir / "images").mkdir(parents=True, exist_ok=True)
    (data_dir / new_uri).write_bytes(content)
    repo.update_meme_image(
        conn,
        meme_id,
        image_uri=new_uri,
        sha256=hashlib.sha256(content).hexdigest(),
        width=width,
        height=height,
    )
    return True
