"""Alembic 環境設定。

連線 URL 單一來源：``memeradar.shared.config`` 的 ``database_url``（libpq 格式），
在此改寫成 SQLAlchemy + psycopg3 的 ``postgresql+psycopg://`` scheme。
本專案資料存取走原生 psycopg（非 ORM），故不設 target_metadata、不用 autogenerate；
migration 以手寫 op.execute() 撰寫。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from memeradar.shared.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _sqlalchemy_url() -> str:
    url = get_settings().database_url
    # libpq (postgresql://) → SQLAlchemy + psycopg3 driver
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


config.set_main_option("sqlalchemy.url", _sqlalchemy_url())

target_metadata = None  # 手寫 migration，不用 autogenerate


def run_migrations_offline() -> None:
    context.configure(
        url=_sqlalchemy_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
