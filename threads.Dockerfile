# MemeRadar Threads Bot 服務（webhook）。
# Zeabur monorepo：此服務 Root Directory = 專案根，Zeabur 依服務名 "threads" 匹配本檔。
# 純走 HTTP（Threads Graph API + MemeRadar API），不碰 DB。
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY memeradar ./memeradar
RUN pip install --no-cache-dir -e .

CMD uvicorn "memeradar.bot.threads:create_app" --factory --host 0.0.0.0 --port ${PORT:-8080}
