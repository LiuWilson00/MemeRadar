# MemeRadar API 服務（FastAPI + BGE-M3 本地 embedding）。
# Zeabur monorepo：此服務 Root Directory = 專案根，Zeabur 依服務名 "api" 匹配本檔。
# 前端為另一個靜態服務（Root = console/），不在此映像內。
FROM python:3.12-slim

# psycopg / pillow / torch 的系統依賴
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── 重依賴 + 模型權重先裝好（與原始碼無關 → Docker 快取；改 code 重部署不必重抓 BGE） ──
# 先裝 CPU 版 torch，避免把數 GB 的 CUDA 拉進映像
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
# 單獨裝 sentence-transformers（供下一層下載 BGE；與原始碼無關才能被快取）
RUN pip install --no-cache-dir "sentence-transformers>=3.0"
# 把 BGE-M3 權重烘進映像：省掉 ~2.3GB runtime 下載、冷啟更快、不依賴 runtime 外網
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"

# ── 專案原始碼 + 其餘依賴（openai / psycopg / fastapi / alembic…；改 code 只重跑這段） ──
COPY pyproject.toml README.md ./
COPY memeradar ./memeradar
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir -e ".[local-embedding]"

ENV MEMERADAR_DATA_DIR=/data

# 啟動：先跑 migration（基準版含 CREATE EXTENSION vector），再起 uvicorn 綁 0.0.0.0:$PORT
CMD alembic upgrade head && \
    uvicorn "memeradar.api.app:create_app" --factory --host 0.0.0.0 --port ${PORT:-8080}
