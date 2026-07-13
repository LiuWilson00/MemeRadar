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

# 依外鍵順序無關；TRUNCATE ... CASCADE 一次清空
_TABLES = (
    "memes, meme_annotations, embeddings, meme_sources, recommendation_logs, "
    "feedback_events, dedup_reviews, vlm_calls, tasks, settings, crawl_state, crawl_health"
)


@pytest.fixture(scope="session", autouse=True)
def _pg_test_db():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        url = (
            f"postgresql://{pg.username}:{pg.password}"
            f"@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/{pg.dbname}"
        )
        os.environ["DATABASE_URL"] = url

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
    conn.execute(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE")
    conn.commit()
    conn.close()
    yield
