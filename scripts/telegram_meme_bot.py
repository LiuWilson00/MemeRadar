#!/usr/bin/env python
"""Telegram 梗圖 bot（最小可跑 PoC）：被 @ 就依對話上下文回一張梗圖。

流程：long polling 收訊息 → 判斷該不該回（私聊任意訊息 / 群組被 @ 或被回覆）→
取上下文（若這則是「回覆某則訊息 + @bot」，就用被回覆的那則當「對方講的話」）→
打 MemeRadar /recommend（fast_mode，秒回）→ 下載 top 梗圖 → sendPhoto 回覆。

只依賴 requests，不 import 專案內部——純走 HTTP，跑哪都行、不需公開 webhook/HTTPS。

跑法：
    1) 跟 @BotFather 申請 bot，拿 token。
    2) （群組用）在 @BotFather 對這個 bot 下 /setprivacy → **Disable**，
       這樣 bot 才收得到群組裡「回覆別人 + @bot」的訊息（含被回覆的原文）。
       只想「被 @ 才回」的話，privacy 開著也可，但拿不到被回覆訊息的原文。
    3) 設環境變數後執行：
       export TELEGRAM_BOT_TOKEN=123456:abc...
       export MEMERADAR_API=https://memeradarapi.zeabur.app
       export MEMERADAR_ADMIN=admin:你的後台密碼   # /recommend 走後台 Basic auth
       python scripts/telegram_meme_bot.py

用法：把 bot 加進群 → 有人「回覆某則訊息並 @你的bot」→ bot 依那則內容回一張梗圖。
（私聊直接跟 bot 講話也會回，方便測試。）

正式化備註：PoC 為求簡單用後台 /recommend（無每日配額）。上線建議改走公開 /tasks
+ 每個 Telegram 使用者一個 client_id（各自配額），或發服務金鑰，別把後台密碼放進 bot。
"""

from __future__ import annotations

import os
import sys
import time

import requests

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API = os.environ.get("MEMERADAR_API", "https://memeradarapi.zeabur.app").rstrip("/")
ADMIN = os.environ.get("MEMERADAR_ADMIN", "")  # "user:pass"；/recommend 為後台端點
TG = f"https://api.telegram.org/bot{TG_TOKEN}"


def _tg(method: str, **params):
    """呼叫 Telegram Bot API（JSON）。sendPhoto 上傳檔案另走 multipart，不用這個。"""
    r = requests.post(f"{TG}/{method}", json=params, timeout=70)
    r.raise_for_status()
    return r.json()["result"]


def recommend_meme(context_text: str) -> bytes | None:
    """打 MemeRadar：把「對方講的話」當上下文 → 下載 top 梗圖 bytes；找不到回 None。"""
    auth = tuple(ADMIN.split(":", 1)) if ADMIN else None
    body = {
        "input_type": "text",
        "conversation": [{"speaker": "other", "text": context_text[:500]}],
        "fast_mode": True,  # 秒回；精準模式 ~25s 太慢，不適合聊天
        "client_id": "telegram-bot",
    }
    resp = requests.post(f"{API}/recommend", json=body, auth=auth, timeout=30)
    if resp.status_code != 200:
        print(f"[recommend] HTTP {resp.status_code}: {resp.text[:160]}", file=sys.stderr)
        return None
    results = resp.json().get("results", [])
    if not results:
        return None
    # image_url 是相對路徑（/memes/{id}/image）；?dl=1 由 API 直送位元組
    img = requests.get(f"{API}{results[0]['image_url']}?dl=1", timeout=30)
    img.raise_for_status()
    return img.content


def context_for(msg: dict, me: dict) -> str | None:
    """判斷要不要回並回傳「拿去找梗圖的上下文文字」；不回則 None。

    - 私聊：任意文字都回，上下文＝該訊息。
    - 群組：被 @bot 或直接回覆 bot 才回。若這則同時是「回覆某則訊息」，
      上下文優先取被回覆的那則（＝對方講的話，我要回敬）；否則取 @ 那則自己的話。
    """
    if msg.get("from", {}).get("id") == me.get("id"):
        return None  # 別回自己（防迴圈）
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


def handle(msg: dict, me: dict) -> None:
    context = context_for(msg, me)
    if not context:
        return
    chat_id = msg["chat"]["id"]
    try:
        _tg("sendChatAction", chat_id=chat_id, action="upload_photo")
        image = recommend_meme(context)
        if image is None:
            _tg("sendMessage", chat_id=chat_id, reply_to_message_id=msg["message_id"],
                text="找不到合適的梗圖 😅 換句話再試？")
            return
        r = requests.post(
            f"{TG}/sendPhoto",
            data={"chat_id": chat_id, "reply_to_message_id": msg["message_id"]},
            files={"photo": ("meme.jpg", image)},
            timeout=60,
        )
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001 單則失敗不中斷整體
        print(f"[reply] {exc!r}", file=sys.stderr)


def main() -> int:
    if not TG_TOKEN:
        print("✗ 請設 TELEGRAM_BOT_TOKEN（跟 @BotFather 申請）", file=sys.stderr)
        return 1
    me = _tg("getMe")
    print(f"🤖 @{me['username']} 上線；MemeRadar={API}（Ctrl-C 結束）")
    offset = None
    while True:
        try:
            updates = _tg("getUpdates", offset=offset, timeout=60,
                          allowed_updates=["message"])
        except Exception as exc:  # noqa: BLE001 網路抖動 → 稍等再拉
            print(f"[poll] {exc!r}", file=sys.stderr)
            time.sleep(3)
            continue
        for up in updates:
            offset = up["update_id"] + 1
            if up.get("message"):
                handle(up["message"], me)


if __name__ == "__main__":
    raise SystemExit(main())
