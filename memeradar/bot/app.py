"""Telegram 梗圖 bot（webhook 版，部署用）：Telegram 推 update → 依對話上下文回梗圖。

與 scripts/telegram_meme_bot.py（long polling PoC）同邏輯，改成 webhook + FastAPI，適合託管
（Zeabur 一個服務）。收到 update 立刻回 200、實際處理丟背景，避免拖住 Telegram 的送單。
純走 HTTP 打 MemeRadar API（不碰 DB），所以映像輕、也不占 API 的連線池。

環境變數：
  TELEGRAM_BOT_TOKEN       必填（@BotFather 給的）
  MEMERADAR_API            MemeRadar API base（預設公開網址；可改私網 http://api.zeabur.internal:8080）
  MEMERADAR_BOT_TOKEN      bot 專用憑證（**建議用這個**；只開 /recommend）
  MEMERADAR_ADMIN          "user:pass"；僅在沒設 BOT_TOKEN 時退回使用（權限過大）
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

#: 使用者看到的兩種結果必須分得出來。原本不管 401、500 還是查無結果都回同一句
#: 「找不到合適的梗圖」，於是「後端掛了」跟「這句話沒有對應的梗」長得一模一樣——
#: 2026-08-02 查 bot 故障時，後端明明健康，卻只能翻 Zeabur 的 stderr 才知道真正狀態碼。
NO_MATCH_REPLY = "找不到合適的梗圖 😅 換句話再試？"
FAILURE_REPLY = "我的梗圖雷達秀逗了 🛠️ 不是你的問題，等我一下再試。"


class RecommendOutcome:
    """區分三種結果：成功拿到圖 / 查無結果 / 呼叫失敗。

    ``failed`` 才是「我們壞了」；``image is None and not failed`` 是「真的沒梗圖」。
    ``detail`` 留技術細節（狀態碼、回應片段）給維運，不給使用者看。
    """

    __slots__ = ("image", "failed", "detail")

    def __init__(self, image: bytes | None = None, *, failed: bool = False,
                 detail: str | None = None):
        self.image = image
        self.failed = failed
        self.detail = detail


def _config() -> dict:
    return {
        "token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "api": os.environ.get("MEMERADAR_API", "https://memeradarapi.zeabur.app").rstrip("/"),
        "admin": os.environ.get("MEMERADAR_ADMIN", ""),
        # bot 專用憑證：只開 /recommend。優先用它，不必再握後台管理員密碼。
        "bot_token": os.environ.get("MEMERADAR_BOT_TOKEN", ""),
        "secret": os.environ.get("TELEGRAM_WEBHOOK_SECRET", ""),
        "public_url": os.environ.get("PUBLIC_URL", "").rstrip("/"),
    }


def recommend_meme(cfg: dict, context_text: str) -> RecommendOutcome:
    """把「對方講的話」當上下文 → 下載 top 梗圖 bytes。

    回 :class:`RecommendOutcome`，把「呼叫失敗」與「查無結果」分開——這兩件事對使用者
    和對維運的意義完全不同，不能共用一個 ``None``。
    """
    # 優先用 bot 專用憑證；沒設才退回 admin Basic（換發期間兩者都能通，不斷線）
    headers = {"X-Bot-Token": cfg["bot_token"]} if cfg.get("bot_token") else None
    auth = None if headers else (
        tuple(cfg["admin"].split(":", 1)) if cfg["admin"] else None)
    body = {
        "input_type": "text",
        "conversation": [{"speaker": "other", "text": context_text[:500]}],
        "fast_mode": True,  # 秒回；精準模式太慢不適合聊天
        "client_id": "telegram-bot",
    }
    try:
        resp = requests.post(f"{cfg['api']}/recommend", json=body,
                             auth=auth, headers=headers, timeout=30)
    except requests.RequestException as exc:
        detail = f"連不上 API：{type(exc).__name__}"
        print(f"[recommend] {detail}", file=sys.stderr)
        return RecommendOutcome(failed=True, detail=detail)
    if resp.status_code != 200:
        # 401 幾乎都是憑證沒設或不對——點名**這次實際用的**那個變數，免得照著改錯的
        hint = ""
        if resp.status_code == 401:
            hint = ("（檢查 MEMERADAR_BOT_TOKEN）" if headers
                    else "（檢查 MEMERADAR_ADMIN）")
        detail = f"HTTP {resp.status_code}{hint}: {resp.text[:160]}"
        print(f"[recommend] {detail}", file=sys.stderr)
        return RecommendOutcome(failed=True, detail=detail)
    results = resp.json().get("results", [])
    if not results:
        return RecommendOutcome()  # 真的沒有適合的梗圖，不是故障
    try:
        img = requests.get(f"{cfg['api']}{results[0]['image_url']}?dl=1", timeout=30)
        img.raise_for_status()
    except requests.RequestException as exc:
        detail = f"下載圖片失敗：{type(exc).__name__}"
        print(f"[recommend] {detail}", file=sys.stderr)
        return RecommendOutcome(failed=True, detail=detail)
    return RecommendOutcome(img.content)


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
        outcome = recommend_meme(cfg, context)
        if outcome.image is None:
            requests.post(f"{tg}/sendMessage", timeout=20, json={
                "chat_id": chat_id, "reply_to_message_id": reply_id,
                "text": FAILURE_REPLY if outcome.failed else NO_MATCH_REPLY})
            return
        requests.post(
            f"{tg}/sendPhoto", timeout=60,
            data={"chat_id": chat_id, "reply_to_message_id": reply_id},
            files={"photo": ("meme.jpg", outcome.image)},
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
