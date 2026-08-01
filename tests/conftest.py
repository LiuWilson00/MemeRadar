"""測試共用設定：對「拋棄式」PostgreSQL（pgvector）容器跑，與本機開發庫隔離。

- session 級：起一個 pgvector 容器，設 DATABASE_URL、以 Alembic 建 schema。
  （絕不對開發庫跑測試——會 TRUNCATE 掉真實梗圖。）
- function 級（autouse）：每個測試前清空所有資料表，達到隔離。
需要本機 Docker；容器映像沿用 docker-compose 的 pgvector/pgvector:pg16。
"""

from __future__ import annotations

import os

import pytest
from testcontainers.postgres import PostgresContainer

# 清空清單改成向 DB 問，不再手寫維護：漏掉一張表不會報錯，只會讓測試之間**默默共用資料**，
# 症狀是「單獨跑會過、整包跑就掛」這種最難查的假失敗（2026-08-02 新增 blog_posts 時中招）。
_SKIP_TABLES = {"alembic_version"}


def _all_tables(conn) -> str:
    rows = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    ).fetchall()
    names = sorted(r["tablename"] for r in rows if r["tablename"] not in _SKIP_TABLES)
    return ", ".join(names)


@pytest.fixture(scope="session", autouse=True)
def _pg_test_db():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        url = (
            f"postgresql://{pg.username}:{pg.password}"
            f"@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/{pg.dbname}"
        )
        os.environ["DATABASE_URL"] = url
        # 測試用維度不一的小向量 → 跳過「固定 1024 維 + HNSW」遷移（那純為正式效能優化）
        os.environ["MEMERADAR_SKIP_VECTOR_INDEX"] = "1"
        # 開發者 .env 裡的**真** R2 憑證會被 Settings 讀進來，於是 load_meme_image_bytes
        # 走 R2 分支、對正式 bucket 撈測試造的假 meme_id → NoSuchKey，整批圖片相關測試
        # 在有 .env 的機器上紅、在 CI 上綠。測試一律不碰外部物件儲存。
        for var in ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID",
                    "R2_SECRET_ACCESS_KEY", "R2_PUBLIC_BASE_URL"):
            os.environ[var] = ""

        from memeradar.shared.config import get_settings
        from memeradar.shared.db import close_pool, ensure_schema

        get_settings.cache_clear()
        ensure_schema()  # Alembic upgrade head（含 CREATE EXTENSION vector）
        yield
        close_pool()  # 收掉連線池，避免測試結束殘留連線
        get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clean_tables(_pg_test_db):
    """每個測試前清空資料表（隔離）。autouse 且 function 級，先於各測試的 seed fixture。"""
    from memeradar.shared.db import connect

    conn = connect()
    conn.execute(f"TRUNCATE {_all_tables(conn)} RESTART IDENTITY CASCADE")
    conn.commit()
    conn.close()
    yield
