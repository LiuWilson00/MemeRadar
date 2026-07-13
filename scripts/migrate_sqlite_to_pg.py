"""一次性資料搬遷：SQLite → PostgreSQL（schema 由 Alembic 建好後執行）。

- JSON 文字欄位 → JSONB（以 ::jsonb 轉型）
- embeddings.vector（JSON 文字）→ pgvector ``vector`` 型別（以 ::vector 轉型）
- 依外鍵順序插入；可重複執行（先 TRUNCATE 資料表，不動 alembic_version）

用法：
    python scripts/migrate_sqlite_to_pg.py [sqlite_path]
    # sqlite_path 預設 data/memeradar.sqlite3；PG 連線取自 settings.database_url
"""

from __future__ import annotations

import sqlite3
import sys

import psycopg

from memeradar.shared.config import get_settings

# 插入順序（被參照的表在前）
TABLE_ORDER = [
    "memes",
    "meme_annotations",
    "embeddings",
    "meme_sources",
    "recommendation_logs",
    "feedback_events",
    "dedup_reviews",
    "vlm_calls",
    "tasks",
    "settings",
    "crawl_state",
    "crawl_health",
]

# JSON 欄位在 PG 仍是 TEXT（直接照抄字串）；只有 vector 需轉 pgvector 型別
VECTOR_COLS = {"embeddings": {"vector"}}


def _placeholder(table: str, col: str) -> str:
    if col in VECTOR_COLS.get(table, ()):
        return "%s::vector"
    return "%s"


def migrate(sqlite_path: str, pg_url: str) -> None:
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    with psycopg.connect(pg_url) as dst:
        # 先清空資料表（CASCADE 一次搞定外鍵），不動 alembic_version
        dst.execute(f"TRUNCATE {', '.join(TABLE_ORDER)} RESTART IDENTITY CASCADE")
        for table in TABLE_ORDER:
            cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            if not rows:
                print(f"  {table:22s} 0")
                continue
            placeholders = ", ".join(_placeholder(table, c) for c in cols)
            insert = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
            dst.cursor().executemany(insert, [tuple(r) for r in rows])
            print(f"  {table:22s} {len(rows)}")
        dst.commit()
    src.close()


def main() -> None:
    sqlite_path = sys.argv[1] if len(sys.argv) > 1 else "data/memeradar.sqlite3"
    pg_url = get_settings().database_url
    print(f"SQLite: {sqlite_path}  →  PG: {pg_url.rsplit('@', 1)[-1]}")
    migrate(sqlite_path, pg_url)
    print("完成。")


if __name__ == "__main__":
    main()
