"""PostgreSQL 連線與 schema 管理。

- ``connect()``：開啟 psycopg 連線（DSN 取自 settings.database_url），rows 以
  名稱存取（dict_row）。外鍵由 PG 預設強制。
- ``ensure_schema()`` / ``migrate()``：以 Alembic 升到最新版（冪等；每個 process
  只跑一次）。正式部署以 ``alembic upgrade head`` 為準，此處供程式/測試方便呼叫。
- CLI：``python -m memeradar.shared.db`` 直接升級資料庫。

歷史備註：本層原為 SQLite；上生產環境改用 PostgreSQL + pgvector（見 alembic/）。
向量與 JSON 欄位皆以文字往返（pgvector 的 '[..]' 亦為合法 JSON），故 repository
的 _dumps/_loads 與既有讀寫邏輯大致沿用。
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from memeradar.shared.config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_schema_ready = False
_pool = None  # psycopg_pool.ConnectionPool（惰性建立）
_pool_dsn: str | None = None


def get_pool():
    """請求路徑用的連線池（短連線、頻繁）；背景任務仍用 connect() 開一次性長連線。

    惰性建立，DSN 變更時重建（測試切到不同容器 DSN 時）。連線借出/歸還由
    ``pool.connection()`` context manager 管理，離開時自動 commit/rollback 並歸還。
    """
    global _pool, _pool_dsn
    from psycopg_pool import ConnectionPool

    dsn = get_settings().database_url
    if _pool is None or _pool_dsn != dsn:
        if _pool is not None:
            _pool.close()
        _pool = ConnectionPool(
            dsn, min_size=1, max_size=20, open=True,
            # timeout=10：拿不到連線時最多等 10s 就拋 PoolTimeout（快速失敗、放掉執行緒），
            # 免得「等連線的人」把執行緒池一條條吃光、連 /health 都排不到（假死的最後一哩）。
            timeout=10.0,
            kwargs={
                "row_factory": dict_row,
                # 後盾：借出的連線若交易閒置逾 60s（例如卡在外部呼叫）由 DB 端中止、放回池子。
                "options": "-c idle_in_transaction_session_timeout=60000",
            },
        )
        _pool_dsn = dsn
    return _pool


def close_pool() -> None:
    global _pool, _pool_dsn
    if _pool is not None:
        _pool.close()
        _pool = None
        _pool_dsn = None


def connect(dsn: str | Path | None = None) -> psycopg.Connection:
    """開啟 PG 連線。dsn 給 str 的 postgres URL 時採用，否則（None / 舊的檔案路徑
    參數）一律用 settings.database_url——讓既有 ``connect(path)`` 呼叫沿用不改。"""
    url = (
        dsn if isinstance(dsn, str) and dsn.startswith("postgres")
        else get_settings().database_url
    )
    # 後盾：交易閒置逾 60s（例如請求卡在外部 LLM 呼叫時仍握著連線）由 DB 端自動中止，
    # 釋放連線並讓該請求報錯收場——單一慢/掛住的請求就不會把連線與 worker 無限占住。
    return psycopg.connect(
        url, row_factory=dict_row, autocommit=False,
        options="-c idle_in_transaction_session_timeout=60000",
    )


def ensure_schema() -> None:
    """以 Alembic 升到 head（冪等；每個 process 只實際跑一次）。"""
    global _schema_ready
    if _schema_ready:
        return
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    _schema_ready = True


def migrate(conn: psycopg.Connection | None = None) -> list[str]:
    """相容保留：確保 schema 為最新（實際由 Alembic 管理）。conn 參數不再需要。"""
    ensure_schema()
    return []


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ensure_schema()
    print(f"資料庫已升到最新（{get_settings().database_url.rsplit('@', 1)[-1]}）")


if __name__ == "__main__":
    main()
