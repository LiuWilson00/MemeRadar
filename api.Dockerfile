# MemeRadar API 服務（FastAPI）。
# Zeabur monorepo：此服務 Root Directory = 專案根，Zeabur 依服務名 "api" 匹配本檔。
# 前端為另一個靜態服務（Root = console/），不在此映像內。
#
# embedding 走 NVIDIA hosted bge-m3（與本地向量完全相同），故映像不含 torch / 本地模型，
# 記憶體與體積極省（~150MB，不再 OOM）。離線本地開發才需 [local-embedding] extra。
FROM python:3.12-slim

# psycopg / pillow 的系統依賴
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY memeradar ./memeradar
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir -e .

ENV MEMERADAR_DATA_DIR=/data

# 啟動：先跑 migration（基準版含 CREATE EXTENSION vector），再起 uvicorn 綁 0.0.0.0:$PORT
CMD alembic upgrade head && \
    uvicorn "memeradar.api.app:create_app" --factory --host 0.0.0.0 --port ${PORT:-8080}
