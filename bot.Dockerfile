# MemeRadar Telegram Bot 服務（webhook）。
# Zeabur monorepo：此服務 Root Directory = 專案根，Zeabur 依服務名 "bot" 匹配本檔。
# 純走 HTTP 打 MemeRadar API（不碰 DB / 不跑 migration），映像輕。
FROM python:3.12-slim

# psycopg 等套件在 pip install -e . 時需要（與 api 共用同一份 pyproject）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY memeradar ./memeradar
RUN pip install --no-cache-dir -e .

# webhook 服務：收 Telegram update → 依上下文回梗圖
CMD uvicorn "memeradar.bot.app:create_app" --factory --host 0.0.0.0 --port ${PORT:-8080}
