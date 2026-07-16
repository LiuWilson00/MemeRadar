"""Seed 匯入腳本測試（P0-3 工具；驗收：入庫腳本可重複執行）。"""

from pathlib import Path

import pytest
from PIL import Image

from memeradar.ingestion.seed_import import import_image_bytes, import_seed_folder
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def data_dir(tmp_path) -> Path:
    return tmp_path / "data"


def make_image(path: Path, size=(400, 400), color=(200, 30, 30)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def test_imports_images_and_copies_to_store(conn, data_dir, tmp_path):
    seed = tmp_path / "seed"
    make_image(seed / "海綿寶寶" / "a.png")
    make_image(seed / "b.jpg", color=(30, 200, 30))

    report = import_seed_folder(conn, seed, data_dir=data_dir)

    assert report.imported == 2
    assert report.skipped_duplicate == 0
    assert repo.count_memes(conn) == 2

    # 圖檔複製進物件儲存，image_uri 為相對路徑且可解析回檔案
    meme = repo.find_meme_by_sha256(conn, _sha256_of(seed / "海綿寶寶" / "a.png"))
    assert meme is not None
    assert (data_dir / meme.image_uri).exists()
    assert meme.width == 400 and meme.height == 400
    assert meme.status == "active"

    # 子資料夾名成為 manual source 的上下文提示
    sources = repo.list_sources(conn, meme.meme_id)
    assert len(sources) == 1
    assert sources[0].platform == "manual"
    assert sources[0].post_title == "海綿寶寶"

    # 根目錄圖片無資料夾提示
    root_meme = repo.find_meme_by_sha256(conn, _sha256_of(seed / "b.jpg"))
    assert repo.list_sources(conn, root_meme.meme_id)[0].post_title is None

    # 配平統計：每資料夾張數
    assert report.per_folder == {"海綿寶寶": 1, ".": 1}


def test_rerun_is_idempotent(conn, data_dir, tmp_path):
    seed = tmp_path / "seed"
    make_image(seed / "a.png")
    make_image(seed / "b.png", color=(0, 0, 250))

    first = import_seed_folder(conn, seed, data_dir=data_dir)
    second = import_seed_folder(conn, seed, data_dir=data_dir)

    assert first.imported == 2
    assert second.imported == 0
    assert second.skipped_duplicate == 2
    assert repo.count_memes(conn) == 2


def test_same_content_different_filename_deduped(conn, data_dir, tmp_path):
    seed = tmp_path / "seed"
    make_image(seed / "a.png")
    (seed / "copy_of_a.png").write_bytes((seed / "a.png").read_bytes())

    report = import_seed_folder(conn, seed, data_dir=data_dir)

    assert report.imported == 1
    assert report.skipped_duplicate == 1
    assert repo.count_memes(conn) == 1


def test_unsupported_files_ignored(conn, data_dir, tmp_path):
    seed = tmp_path / "seed"
    make_image(seed / "a.png")
    (seed / "notes.txt").write_text("不是圖", encoding="utf-8")
    make_image(seed / "anim.gif")  # GIF 為 Phase 1 範圍外（docs/02 §5）

    report = import_seed_folder(conn, seed, data_dir=data_dir)

    assert report.imported == 1
    assert report.skipped_unsupported == 2
    assert repo.count_memes(conn) == 1


def test_small_image_warns_but_imports(conn, data_dir, tmp_path):
    seed = tmp_path / "seed"
    make_image(seed / "tiny.png", size=(100, 100))

    report = import_seed_folder(conn, seed, data_dir=data_dir)

    # 人工精選集：尺寸不達爬蟲門檻僅警告不擋（策展人已把關）
    assert report.imported == 1
    assert any("tiny.png" in w for w in report.warnings)


def test_corrupt_image_skipped_with_warning(conn, data_dir, tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "broken.png").write_bytes(b"not really a png")

    report = import_seed_folder(conn, seed, data_dir=data_dir)

    assert report.imported == 0
    assert report.errors == 1
    assert repo.count_memes(conn) == 0


class TestImportImageBytes:
    def test_imports_single_image_with_source_title(self, conn, data_dir, tmp_path):
        make_image(tmp_path / "a.png")
        content = (tmp_path / "a.png").read_bytes()

        meme, status = import_image_bytes(
            conn, content, data_dir=data_dir, source_title="Console 上傳"
        )

        assert status == "imported"
        assert meme is not None
        assert (data_dir / meme.image_uri).exists()
        assert repo.list_sources(conn, meme.meme_id)[0].post_title == "Console 上傳"

    def test_first_source_counts_into_engagement(self, conn, data_dir, tmp_path):
        """首次匯入即按 Σ(來源互動分) 起算（docs/06 §3.1），與 merge 轉移邏輯一致。"""
        from memeradar.ingestion.dedup import hotness_gain

        make_image(tmp_path / "a.png")
        content = (tmp_path / "a.png").read_bytes()

        meme, _ = import_image_bytes(conn, content, data_dir=data_dir, upvotes=99)

        got = repo.get_meme(conn, meme.meme_id)
        assert got.engagement == pytest.approx(hotness_gain(99))
        assert got.last_seen_at is not None
        assert got.hotness > 0

    def test_duplicate_returns_existing_meme(self, conn, data_dir, tmp_path):
        make_image(tmp_path / "a.png")
        content = (tmp_path / "a.png").read_bytes()
        first, _ = import_image_bytes(conn, content, data_dir=data_dir)

        dup, status = import_image_bytes(conn, content, data_dir=data_dir)

        assert status == "duplicate"
        assert dup.meme_id == first.meme_id
        assert repo.count_memes(conn) == 1

    def test_concurrent_same_image_returns_duplicate_not_error(
        self, conn, data_dir, tmp_path, monkeypatch
    ):
        """並發匯入同圖競態：pre-check 過了但 insert 撞 sha256 UNIQUE → 當 duplicate 回既有，
        而非讓 UniqueViolation 冒成 500。模擬法：讓第一次 find（pre-check）裝作沒有。"""
        make_image(tmp_path / "a.png")
        content = (tmp_path / "a.png").read_bytes()
        first, _ = import_image_bytes(conn, content, data_dir=data_dir)  # 並發的另一請求先成功

        real = repo.find_meme_by_sha256
        calls = {"n": 0}

        def fake(c, sha):
            calls["n"] += 1
            return None if calls["n"] == 1 else real(c, sha)  # 第一次(pre-check)裝作查不到

        monkeypatch.setattr(repo, "find_meme_by_sha256", fake)
        dup, status = import_image_bytes(conn, content, data_dir=data_dir)

        assert status == "duplicate"
        assert dup.meme_id == first.meme_id
        assert repo.count_memes(conn) == 1

    def test_corrupt_bytes_return_error(self, conn, data_dir):
        meme, status = import_image_bytes(conn, b"not an image", data_dir=data_dir)
        assert status == "error"
        assert meme is None

    def test_unsupported_format_rejected(self, conn, data_dir, tmp_path):
        from PIL import Image

        Image.new("RGB", (300, 300)).save(tmp_path / "a.gif")
        meme, status = import_image_bytes(
            conn, (tmp_path / "a.gif").read_bytes(), data_dir=data_dir
        )
        assert status == "unsupported"
        assert meme is None


def test_imported_source_urls_filters_by_platform(conn, data_dir, tmp_path):
    """爬蟲下載前預先去重用：回某來源平台已入庫的 post_url 集合。"""
    make_image(tmp_path / "a.png")
    import_image_bytes(
        conn, (tmp_path / "a.png").read_bytes(), data_dir=data_dir,
        platform="memes_tw", post_url="https://memes.tw/wtf/123",
    )
    make_image(tmp_path / "b.png", color=(0, 0, 250))
    import_image_bytes(
        conn, (tmp_path / "b.png").read_bytes(), data_dir=data_dir,
        platform="other", post_url="https://other/9",
    )

    urls = repo.imported_source_urls(conn, "memes_tw")

    assert urls == {"https://memes.tw/wtf/123"}


def _sha256_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
