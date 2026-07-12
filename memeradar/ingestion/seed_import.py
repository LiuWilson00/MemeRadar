"""人工 seed 梗圖匯入（docs/02 §2 資料源 P0：人工匯入）。

用法：把精選圖片放進資料夾（可依主題建子資料夾，如 ``seed/海綿寶寶/``），
執行 ``python -m memeradar.ingestion.seed_import <folder>``。

- 以 sha256 去重：重跑冪等，已入庫的圖直接跳過。
- 圖檔複製到 ``{data_dir}/images/{meme_id}{ext}``，DB 只存相對路徑。
- 子資料夾名記為 manual source 的 ``post_title``，供標註時當作上下文提示。
- 人工精選集不套用爬蟲的尺寸門檻（策展人已把關），過小僅警告。
- 報表含每資料夾張數，輔助 P0-3 的策略錨點配平檢查。
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from memeradar.shared import repository as repo
from memeradar.shared.config import get_settings
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Meme, MemeSource, new_id

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MIN_SHORT_SIDE = 200  # docs/02 §5 規則門檻；seed 僅警告
_FORMAT_EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


@dataclass
class ImportReport:
    imported: int = 0
    skipped_duplicate: int = 0
    skipped_unsupported: int = 0
    errors: int = 0
    warnings: list[str] = field(default_factory=list)
    per_folder: dict[str, int] = field(default_factory=dict)


def _normalized_ext(path: Path) -> str:
    ext = path.suffix.lower()
    return ".jpg" if ext == ".jpeg" else ext


def import_image_bytes(
    conn: sqlite3.Connection,
    content: bytes,
    *,
    data_dir: Path,
    source_title: str | None = None,
    platform: str = "manual",
    post_url: str | None = None,
    top_comments: list[str] | None = None,
    upvotes: int | None = None,
    posted_at: str | None = None,
) -> tuple[Meme | None, str]:
    """單張圖片入庫（seed 匯入與 Console 上傳共用核心）。

    回傳 ``(meme, status)``；status ∈ imported / duplicate / unsupported / error。
    duplicate 時回傳既有 meme。
    """
    sha256 = hashlib.sha256(content).hexdigest()
    existing = repo.find_meme_by_sha256(conn, sha256)
    if existing is not None:
        return existing, "duplicate"

    try:
        with Image.open(io.BytesIO(content)) as img:
            width, height = img.size
            image_format = img.format
    except (UnidentifiedImageError, OSError):
        return None, "error"

    extension = _FORMAT_EXTENSIONS.get(image_format or "")
    if extension is None:
        return None, "unsupported"

    images_dir = data_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    meme_id = new_id("m")
    (images_dir / f"{meme_id}{extension}").write_bytes(content)

    meme = Meme(
        meme_id=meme_id,
        image_uri=f"images/{meme_id}{extension}",
        sha256=sha256,
        width=width,
        height=height,
    )
    repo.insert_meme(conn, meme)
    repo.add_source(
        conn,
        MemeSource(
            source_id=new_id("s"),
            meme_id=meme_id,
            platform=platform,
            post_title=source_title,
            post_url=post_url,
            top_comments=top_comments or [],
            upvotes=upvotes,
            posted_at=posted_at,
        ),
    )
    # 首個來源即計入互動分：hotness 按 Σ(來源互動分) 起算（docs/06 §3.1），
    # 與 merge 轉移「重複者全部來源」的邏輯一致
    from memeradar.ingestion.dedup import hotness_gain
    from memeradar.shared.hotness import record_engagement

    record_engagement(conn, meme_id, hotness_gain(upvotes))
    return repo.get_meme(conn, meme_id), "imported"


def import_seed_folder(
    conn: sqlite3.Connection, folder: Path, data_dir: Path | None = None
) -> ImportReport:
    folder = Path(folder)
    data_dir = Path(data_dir) if data_dir is not None else get_settings().memeradar_data_dir

    report = ImportReport()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        if _normalized_ext(path) not in SUPPORTED_EXTENSIONS:
            report.skipped_unsupported += 1
            continue

        rel_dir = path.parent.relative_to(folder)
        folder_hint = None if str(rel_dir) == "." else str(rel_dir).replace("\\", "/")

        meme, status = import_image_bytes(
            conn, path.read_bytes(), data_dir=data_dir, source_title=folder_hint
        )
        if status == "duplicate":
            report.skipped_duplicate += 1
            continue
        if status in ("error", "unsupported"):
            report.errors += 1
            report.warnings.append(f"無法讀取圖片，已跳過：{path}")
            continue

        assert meme is not None
        if min(meme.width or 0, meme.height or 0) < MIN_SHORT_SIDE:
            report.warnings.append(f"尺寸過小（{meme.width}x{meme.height}）仍匯入：{path.name}")

        report.imported += 1
        key = folder_hint or "."
        report.per_folder[key] = report.per_folder.get(key, 0) + 1

    return report


def main(argv: list[str] | None = None) -> None:
    import argparse

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="匯入人工 seed 梗圖資料夾")
    parser.add_argument("folder", type=Path, help="含圖片的資料夾（可含主題子資料夾）")
    args = parser.parse_args(argv)

    conn = connect()
    try:
        migrate(conn)
        report = import_seed_folder(conn, args.folder)
    finally:
        conn.close()

    print(
        f"匯入 {report.imported} 張；重複跳過 {report.skipped_duplicate}；"
        f"不支援格式 {report.skipped_unsupported}；讀取失敗 {report.errors}"
    )
    if report.per_folder:
        print("各資料夾張數（配平參考）：")
        for name, count in sorted(report.per_folder.items()):
            print(f"  {name}: {count}")
    for warning in report.warnings:
        print(f"[警告] {warning}")


if __name__ == "__main__":
    main()
