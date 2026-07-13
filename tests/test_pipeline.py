"""P3-5 測試：全自動管線（抓取 → 過濾 → 去重 → 標註 → 裁決 → 向量化）。

以 fake adapter / fetcher / stub client / fake embedders 全程離線驗證；
驗收核心：批次報表數字對帳。
"""

import io
import json

import pytest
from PIL import Image, ImageDraw

from memeradar.ingestion.base import Candidate
from memeradar.ingestion.pipeline import run_pipeline
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate

SIGNATURE = "fake-embed@v1|doc-v1"


def png(seed: int, size=(400, 400)) -> bytes:
    img = Image.new("RGB", size, (seed * 40 % 255, 90, 160))
    d = ImageDraw.Draw(img)
    for i in range(5):
        x = (seed * 47 + i * 71) % 300
        d.ellipse((x, i * 60, x + 70, i * 60 + 50), fill=(255 - i * 30, seed * 20 % 255, i * 45))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def candidate(post_id: str, urls: list[str], *, upvotes=500, platform="reddit") -> Candidate:
    return Candidate(
        platform=platform,
        post_id=post_id,
        post_url=f"https://reddit.com/{post_id}",
        post_title=f"貼文 {post_id}",
        top_comments=["笑死"],
        upvotes=upvotes,
        posted_at="2026-07-11T00:00:00+00:00",
        images=[{"url": u, "order": i} for i, u in enumerate(urls)],
    )


class FakeAdapter:
    def __init__(self, name: str, candidates: list[Candidate], *, fail: bool = False):
        self.name = name
        self.candidates = candidates
        self.fail = fail

    def fetch(self, watermark):
        if self.fail:
            raise RuntimeError("模擬來源改版")
        return self.candidates, "2026-07-11T12:00:00+00:00"


class StubVlm:
    """標註 stub（NVIDIA VLM 介面）：回傳原始 JSON 文字，OCR 依圖片內容路由。"""

    model = "qwen/test"

    def __init__(self, ocr_by_marker: dict[str, str] | None = None):
        self.ocr_by_marker = ocr_by_marker or {}

    def annotate(self, image_b64, media_type, system, user_text, **kwargs):
        ocr = "預設文字"
        for marker, text in self.ocr_by_marker.items():
            if image_b64.startswith(marker[:24]) or marker in image_b64[:400]:
                ocr = text
                break
        return json.dumps({
            "is_meme": True, "nsfw": False, "ocr_text": ocr, "description": "測試",
            "characters": [], "franchise": "海綿寶寶", "template_name": None,
            "emotions": ["無奈"], "usage_hints": ["測試用途"], "categories": ["卡通動畫"],
            "confidence": 0.9,
        })


class FakeEmbedder:
    model_id = "fake-embed@v1"

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


@pytest.fixture
def env(tmp_path):
    conn = connect(tmp_path / "db.sqlite3")
    migrate(conn)
    yield conn, tmp_path
    conn.close()


def make_fetcher(mapping: dict[str, bytes]):
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return mapping[url]

    fetch.calls = calls
    return fetch


class TestPipeline:
    def test_end_to_end_counts_reconcile(self, env):
        conn, tmp = env
        images = {
            "https://i.redd.it/a.png": png(1),
            "https://i.redd.it/b.png": png(2, size=(150, 150)),  # 尺寸過小
            "https://i.redd.it/a2.png": png(1),  # 與 a 完全相同 → sha 重複
        }
        adapter = FakeAdapter("reddit", [
            candidate("p1", ["https://i.redd.it/a.png"]),
            candidate("p2", ["https://i.redd.it/b.png"]),
            candidate("p3", ["https://i.redd.it/a2.png"]),
            candidate("p4", ["https://i.redd.it/x.gif"]),  # 格式規則
            candidate("p5", ["https://i.redd.it/c.png"], upvotes=3),  # 互動門檻
        ])

        report = run_pipeline(
            conn, [adapter],
            data_dir=tmp / "data",
            vlm=StubVlm(),
            embedder=FakeEmbedder(),
            image_fetcher=make_fetcher(images),
        )

        assert report.imported == 1
        assert report.duplicates == 1
        assert sum(report.rejected.values()) == 3  # 尺寸過小 + 格式 + 互動門檻
        assert report.annotated == 1
        assert report.embedded == 1
        assert report.failures == 0
        # 對帳：處理過的圖片 = 入庫 + 重複 + 佇列 + 規則淘汰(僅圖片層) + 失敗
        assert report.reconciles()

        # sha 重複被吸收：來源合併 + 熱度累加
        kept = repo.find_meme_by_sha256(
            conn, __import__("hashlib").sha256(png(1)).hexdigest()
        )
        assert len(repo.list_sources(conn, kept.meme_id)) == 2
        assert repo.get_meme(conn, kept.meme_id).hotness > 0

        # 水位已更新
        assert repo.get_watermark(conn, "reddit") == "2026-07-11T12:00:00+00:00"

    def test_review_path_resolved_after_annotation(self, env):
        conn, tmp = env
        original = png(3)
        recompressed = io.BytesIO()
        Image.open(io.BytesIO(original)).convert("RGB").save(
            recompressed, format="JPEG", quality=60
        )
        images = {
            "https://i.redd.it/o.png": original,
            "https://i.redd.it/r.jpg": recompressed.getvalue(),
        }
        adapter = FakeAdapter("reddit", [
            candidate("p1", ["https://i.redd.it/o.png"]),
            candidate("p2", ["https://i.redd.it/r.jpg"]),
        ])

        report = run_pipeline(
            conn, [adapter],
            data_dir=tmp / "data",
            vlm=StubVlm(),  # 兩張 OCR 皆為預設文字 → 相同 → 自動合併
            embedder=FakeEmbedder(),
            image_fetcher=make_fetcher(images),
        )

        assert report.imported == 2  # 兩張都先入庫（review 延後裁決）
        assert report.queued_review == 1
        assert report.review_resolution["merged"] == 1  # 標註後 OCR 相同 → 合併
        assert repo.count_memes(conn, status="removed") == 1
        assert report.reconciles()

    def test_adapter_failure_isolated_and_health_tracked(self, env):
        conn, tmp = env
        good = FakeAdapter("reddit", [candidate("p1", ["https://i.redd.it/a.png"])])
        bad = FakeAdapter("dcard", [], fail=True)

        report = run_pipeline(
            conn, [bad, good],
            data_dir=tmp / "data",
            vlm=StubVlm(),
            embedder=FakeEmbedder(),
            image_fetcher=make_fetcher({"https://i.redd.it/a.png": png(1)}),
        )

        assert report.imported == 1  # 好的來源不受影響
        assert report.adapter_errors == {"dcard": "模擬來源改版"}
        assert repo.get_crawl_failures(conn, "dcard") == 1
        assert repo.get_watermark(conn, "dcard") is None  # 失敗不推水位

        # 連續第 3 次失敗觸發告警旗標
        run_pipeline(conn, [bad], data_dir=tmp / "data", vlm=StubVlm(),
                     embedder=FakeEmbedder(), image_fetcher=make_fetcher({}))
        report3 = run_pipeline(conn, [bad], data_dir=tmp / "data", vlm=StubVlm(),
                               embedder=FakeEmbedder(), image_fetcher=make_fetcher({}))
        assert repo.get_crawl_failures(conn, "dcard") == 3
        assert "dcard" in report3.alerts[0]

        # 成功後歸零
        ok_dcard = FakeAdapter("dcard", [])
        run_pipeline(conn, [ok_dcard], data_dir=tmp / "data", vlm=StubVlm(),
                     embedder=FakeEmbedder(), image_fetcher=make_fetcher({}))
        assert repo.get_crawl_failures(conn, "dcard") == 0

    def test_download_failure_counted_not_fatal(self, env):
        conn, tmp = env

        def flaky(url: str) -> bytes:
            raise OSError("連線逾時")

        adapter = FakeAdapter("reddit", [candidate("p1", ["https://i.redd.it/a.png"])])
        report = run_pipeline(
            conn, [adapter], data_dir=tmp / "data", vlm=StubVlm(),
            embedder=FakeEmbedder(), image_fetcher=flaky,
        )

        assert report.failures == 1
        assert report.imported == 0
        assert report.reconciles()

    def test_crawled_source_metadata_persisted(self, env):
        conn, tmp = env
        adapter = FakeAdapter("reddit", [candidate("p1", ["https://i.redd.it/a.png"])])
        run_pipeline(
            conn, [adapter], data_dir=tmp / "data", vlm=StubVlm(),
            embedder=FakeEmbedder(),
            image_fetcher=make_fetcher({"https://i.redd.it/a.png": png(1)}),
        )

        [row] = repo.list_memes_with_annotations(conn)
        [source] = repo.list_sources(conn, row["meme_id"])
        assert source.platform == "reddit"
        assert source.post_url == "https://reddit.com/p1"
        assert source.post_title == "貼文 p1"
        assert source.top_comments == ["笑死"]
        assert source.upvotes == 500
