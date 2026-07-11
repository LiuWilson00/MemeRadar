"""SQLite 連線與 migration 管理。

- ``connect()``：開啟連線（預設路徑 ``{MEMERADAR_DATA_DIR}/memeradar.sqlite3``），
  啟用外鍵約束，rows 以名稱存取。
- ``migrate()``：套用 ``migrations/*.sql``（依檔名排序），已套用者記錄於
  ``schema_migrations``，可重複執行（冪等）。
- CLI：``python -m memeradar.shared.db`` 直接初始化 / 升級資料庫。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from memeradar.shared.config import get_settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def default_db_path() -> Path:
    return get_settings().memeradar_data_dir / "memeradar.sqlite3"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(path) if path is not None else default_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    """套用未執行的 migration，回傳本次套用的版本清單。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    newly_applied: list[str] = []
    for script in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = script.stem
        if version in applied:
            continue
        conn.executescript(script.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        conn.commit()
        newly_applied.append(version)
    # executescript 會重置部分 pragma，保險起見重新啟用外鍵
    conn.execute("PRAGMA foreign_keys = ON")
    return newly_applied


def main() -> None:
    # Windows 主控台預設編碼常非 UTF-8，避免中文輸出亂碼
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    path = default_db_path()
    conn = connect(path)
    try:
        applied = migrate(conn)
    finally:
        conn.close()
    if applied:
        print(f"已套用 migration：{', '.join(applied)}（{path}）")
    else:
        print(f"資料庫已是最新（{path}）")


if __name__ == "__main__":
    main()
