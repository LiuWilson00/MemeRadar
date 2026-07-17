"""Telegram 梗圖 bot（webhook 版，部署用）：Telegram 推 update → 依對話上下文回梗圖。

與 scripts/telegram_meme_bot.py（long polling PoC）同邏輯，改成 webhook + FastAPI，適合託管
（Zeabur 一個服務）。收到 update 立刻回 200、實際處理丟背景，避免拖住 Telegram 的送單。
純走 HTTP 打 MemeRadar API（不碰 DB），所以映像輕、也不占 API 的連線池。

環境變數：
  TELEGRAM_BOT_TOKEN       必填（@BotFather 給的）
  MEMERADAR_API            MemeRadar API base（預設公開網址；可改私網 http://api.zeabur.internal:8080）
  MEMERADAR_ADMIN          "user:pass"，/recommend 的後台 Basic auth
  TELEGRAM_WEBHOOK_SECRET  webhook 驗證密鑰（Telegram 每次帶在 header；強烈建議設）
  PUBLIC_URL               本服務公開網址（Zeabur 給的 domain）；設了就啟動時自動 setWebhook

啟動：uvicorn memeradar.bot.app:create_app --factory --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import os
import sys

import requests
from fastapi import BackgroundTasks, FastAPI, Header, Request, Response

TG_API = "https://api.telegram.org/bot{token}"


def _config() -> dict:
    return {
        "token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "api": os.environ.get("MEMERADAR_API", "https://memeradarapi.zeabur.app").rstrip("/"),
        "admin": os.environ.get("MEMERADAR_ADMIN", ""),
        "secret": os.environ.get("TELEGRAM_WEBHOOK_SECRET", ""),
        "public_url": os.environ.get("PUBLIC_URL", "").rstrip("/"),
    }


def recommend_meme(cfg: dict, context_text: str) -> bytes | None:
    """把「對方講的話」當上下文 → 下載 top 梗圖 bytes；找不到回 None。"""
    auth = tuple(cfg["admin"].split(":", 1)) if cfg["admin"] else None
    body = {
        "input_type": "text",
        "conversation": [{"speaker": "other", "text": context_text[:500]}],
        "fast_mode": True,  # 秒回；精準模式太慢不適合聊天
        "client_id": "telegram-bot",
    }
    resp = requests.post(f"{cfg['api']}/recommend", json=body, auth=auth, timeout=30)
    if resp.status_code != 200:
        print(f"[recommend] HTTP {resp.status_code}: {resp.text[:160]}", file=sys.stderr)
        return None
    results = resp.json().get("results", [])
    if not results:
        return None
    img = requests.get(f"{cfg['api']}{results[0]['image_url']}?dl=1", timeout=30)
    img.raise_for_status()
    return img.content


def context_for(msg: dict, me: dict) -> str | None:
    """判斷要不要回並回傳上下文文字（同 PoC）。私聊任意訊息；群組被 @ 或被回覆才回，
    有 reply 就用被回覆那則（對方講的話）。"""
    if msg.get("from", {}).get("id") == me.get("id"):
        return None
    chat_type = msg.get("chat", {}).get("type")
    text = msg.get("text") or msg.get("caption") or ""
    reply_to = msg.get("reply_to_message") or {}
    reply_text = reply_to.get("text") or reply_to.get("caption") or ""
    if chat_type == "private":
        return text.strip() or None
    username = "@" + me.get("username", "")
    mentioned = username.lower() in text.lower()
    replied_to_bot = reply_to.get("from", {}).get("id") == me.get("id")
    if not (mentioned or replied_to_bot):
        return None
    cleaned = text.replace(username, "").strip()
    return reply_text.strip() or cleaned or None


def process_update(cfg: dict, me: dict, update: dict) -> None:
    """背景處理一則 update：找上下文 → 選圖 → sendPhoto 回覆。"""
    msg = update.get("message")
    if not msg:
        return
    context = context_for(msg, me)
    if not context:
        return
    tg = TG_API.format(token=cfg["token"])
    chat_id = msg["chat"]["id"]
    reply_id = msg["message_id"]
    try:
        requests.post(f"{tg}/sendChatAction",
                      json={"chat_id": chat_id, "action": "upload_photo"}, timeout=20)
        image = recommend_meme(cfg, context)
        if image is None:
            requests.post(f"{tg}/sendMessage", timeout=20, json={
                "chat_id": chat_id, "reply_to_message_id": reply_id,
                "text": "找不到合適的梗圖 😅 換句話再試？"})
            return
        requests.post(
            f"{tg}/sendPhoto", timeout=60,
            data={"chat_id": chat_id, "reply_to_message_id": reply_id},
            files={"photo": ("meme.jpg", image)},
        )
    except Exception as exc:  # noqa: BLE001 單則失敗不中斷服務
        print(f"[reply] {exc!r}", file=sys.stderr)


def create_app() -> FastAPI:
    cfg = _config()
    if not cfg["token"]:
        raise RuntimeError("缺 TELEGRAM_BOT_TOKEN")
    tg = TG_API.format(token=cfg["token"])
    me = requests.get(f"{tg}/getMe", timeout=20).json()["result"]

    # 有 PUBLIC_URL 就自動把 webhook 指到本服務（帶 secret_token 供驗證）
    if cfg["public_url"]:
        params: dict = {"url": f"{cfg['public_url']}/webhook", "allowed_updates": ["message"]}
        if cfg["secret"]:
            params["secret_token"] = cfg["secret"]
        r = requests.post(f"{tg}/setWebhook", json=params, timeout=20)
        print(f"[setWebhook] {r.json()}")
    else:
        print("[warn] 未設 PUBLIC_URL，略過自動 setWebhook（請手動註冊）", file=sys.stderr)

    app = FastAPI(title="MemeRadar Telegram Bot")

    @app.get("/")
    def health() -> dict:
        return {"status": "ok", "bot": me.get("username")}

    @app.post("/webhook")
    async def webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_telegram_bot_api_secret_token: str = Header(default=""),
    ):
        # 驗 secret：擋掉隨機對 webhook 灌假 update 的人
        if cfg["secret"] and x_telegram_bot_api_secret_token != cfg["secret"]:
            return Response(status_code=403)
        update = await request.json()
        background_tasks.add_task(process_update, cfg, me, update)  # 立刻回 200、背景處理
        return {"ok": True}

    return app
