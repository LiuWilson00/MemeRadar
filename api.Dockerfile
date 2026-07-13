# MemeRadar API 服務（FastAPI + BGE-M3 本地 embedding）。
# Zeabur monorepo：此服務 Root Directory = 專案根，Zeabur 依服務名 "api" 匹配本檔。
# 前端為另一個靜態服務（Root = console/），不在此映像內。
FROM python:3.12-slim

# psycopg / pillow / torch 的系統依賴
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先裝 CPU 版 torch，避免把數 GB 的 CUDA 拉進映像
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 依賴（含 local-embedding extra：sentence-transformers）
COPY pyproject.toml README.md ./
COPY memeradar ./memeradar
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir -e ".[local-embedding]"

# 把 BGE-M3 權重烘進映像：省掉 ~2.3GB runtime 下載、冷啟更快、不依賴 runtime 外網
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"

ENV MEMERADAR_DATA_DIR=/data

# 啟動：先跑 migration（基準版含 CREATE EXTENSION vector），再起 uvicorn 綁 0.0.0.0:$PORT
CMD alembic upgrade head && \
    uvicorn "memeradar.api.app:create_app" --factory --host 0.0.0.0 --port ${PORT:-8080}
