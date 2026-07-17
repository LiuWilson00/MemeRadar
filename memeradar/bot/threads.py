"""Threads 梗圖 bot（webhook 版）：別人 @ 你的 Threads 帳號 → 依那則內容公開回一張梗圖。

與 Telegram 版同一套大腦（打 MemeRadar /recommend），差別只在：
- 觸發來源是 Meta 的 **mention webhook**（不是 Telegram update）。
- Threads 不能直接傳圖檔，要給**公開圖片網址**——我們取 MemeRadar 圖片端點 302 的目標
  （R2 公開網址）當 image_url。
- 回覆走 Threads 兩段式：建 IMAGE container（image_url + reply_to_id）→ 輪詢 FINISHED → publish。

環境變數：
  THREADS_ACCESS_TOKEN   長期使用者存取權杖（OAuth 拿到）
  THREADS_USER_ID        你的 Threads user id（graph.threads.net 的 {user-id}）
  MEMERADAR_API          MemeRadar API base（預設公開網址）
  MEMERADAR_ADMIN        "user:pass"，/recommend 後台 Basic auth
  THREADS_VERIFY_TOKEN   webhook 驗證用（Meta GET 挑戰時比對）
  THREADS_APP_SECRET     驗 X-Hub-Signature-256（強烈建議設）

啟動：uvicorn memeradar.bot.threads:create_app --factory --host 0.0.0.0 --port $PORT

⚠️ 圖片規格：Threads 只吃 JPEG/PNG、寬 320–1440px、≤8MB、長寬比 ≤10:1。WebP 或太小/太長的
   梗圖 container 會建失敗（本檔會記 log 跳過）；要穩就在 MemeRadar 端先篩/轉檔（未做）。
⚠️ mention webhook 的實際欄位結構以官方為準；本檔用寬鬆解析並把原始 payload 記 log，
   收到第一則真實 mention 後可依 log 微調 _extract_mentions。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import sys
import time

import requests
from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

GRAPH = "https://graph.threads.net/v1.0"
_HANDLE = re.compile(r"@\w[\w.]*")


def _config() -> dict:
    return {
        "token": os.environ.get("THREADS_ACCESS_TOKEN", ""),
        "user_id": os.environ.get("THREADS_USER_ID", ""),
        "api": os.environ.get("MEMERADAR_API", "https://memeradarapi.zeabur.app").rstrip("/"),
        "admin": os.environ.get("MEMERADAR_ADMIN", ""),
        "verify": os.environ.get("THREADS_VERIFY_TOKEN", ""),
        "app_secret": os.environ.get("THREADS_APP_SECRET", ""),
    }


def recommend_meme_url(cfg: dict, context_text: str) -> str | None:
    """context → top 梗圖的「公開圖片網址」（R2）。Threads image_url 要能被 Meta 直接抓。"""
    auth = tuple(cfg["admin"].split(":", 1)) if cfg["admin"] else None
    body = {
        "input_type": "text",
        "conversation": [{"speaker": "other", "text": context_text[:500]}],
        "fast_mode": True,
        "client_id": "threads-bot",
    }
    resp = requests.post(f"{cfg['api']}/recommend", json=body, auth=auth, timeout=30)
    if resp.status_code != 200:
        print(f"[recommend] HTTP {resp.status_code}: {resp.text[:160]}", file=sys.stderr)
        return None
    results = resp.json().get("results", [])
    if not results:
        return None
    # 圖片端點會 302 導向 R2 公開網址；取 Location 當 image_url（別給 302 端點本身，Meta 不一定跟）
    r = requests.get(f"{cfg['api']}{results[0]['image_url']}", allow_redirects=False, timeout=20)
    if r.status_code in (301, 302, 307, 308):
        return r.headers.get("Location")
    print(f"[recommend] 圖片端點沒 302（status={r.status_code}）——需 R2 公開網址才能貼 Threads",
          file=sys.stderr)
    return None


def post_image_reply(cfg: dict, reply_to_id: str, image_url: str) -> None:
    """Threads 兩段式發圖回覆：建 IMAGE container（帶 reply_to_id）→ 等 FINISHED → publish。"""
    base = f"{GRAPH}/{cfg['user_id']}"
    tok = cfg["token"]
    c = requests.post(f"{base}/threads", timeout=30, params={
        "media_type": "IMAGE", "image_url": image_url,
        "reply_to_id": reply_to_id, "access_token": tok})
    if c.status_code != 200:
        print(f"[threads] 建 container 失敗 {c.status_code}: {c.text[:200]}", file=sys.stderr)
        return
    creation_id = c.json()["id"]
    # Meta 需時間抓圖 + 處理；輪詢 container 狀態到 FINISHED 再 publish
    for _ in range(15):
        time.sleep(2)
        s = requests.get(f"{base}/{creation_id}", timeout=20,
                         params={"fields": "status", "access_token": tok}).json()
        status = s.get("status")
        if status == "FINISHED":
            break
        if status == "ERROR":
            print(f"[threads] container 處理失敗（可能圖片規格不符）: {s}", file=sys.stderr)
            return
    p = requests.post(f"{base}/threads_publish", timeout=30,
                      params={"creation_id": creation_id, "access_token": tok})
    if p.status_code != 200:
        print(f"[threads] publish 失敗 {p.status_code}: {p.text[:200]}", file=sys.stderr)


def _extract_mentions(payload: dict) -> list[tuple[str, str]]:
    """從 webhook payload 寬鬆抽出 (被 @ 的貼文 id, 文字)。欄位以官方為準，這裡容錯多取幾種。"""
    out: list[tuple[str, str]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            v = change.get("value", {}) if isinstance(change, dict) else {}
            post_id = v.get("id") or v.get("media_id") or v.get("post_id") or entry.get("id")
            text = v.get("text") or v.get("message") or v.get("caption") or ""
            if post_id:
                out.append((str(post_id), text))
    return out


def process(cfg: dict, payload: dict) -> None:
    """背景處理 webhook：抽 mention → 依文字選圖 → 公開回覆到那則。"""
    print(f"[webhook] payload={payload}", file=sys.stderr)  # 首次上線用來對欄位
    for post_id, text in _extract_mentions(payload):
        context = _HANDLE.sub("", text).strip() or text.strip()
        if not context:
            continue
        try:
            image_url = recommend_meme_url(cfg, context)
            if not image_url:
                continue
            post_image_reply(cfg, post_id, image_url)
        except Exception as exc:  # noqa: BLE001 單則失敗不中斷
            print(f"[process] {exc!r}", file=sys.stderr)


def _verify_signature(app_secret: str, body: bytes, header: str) -> bool:
    if not app_secret:
        return True  # 未設就不驗（不建議）
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def create_app() -> FastAPI:
    cfg = _config()
    if not (cfg["token"] and cfg["user_id"]):
        raise RuntimeError("缺 THREADS_ACCESS_TOKEN / THREADS_USER_ID")
    app = FastAPI(title="MemeRadar Threads Bot")

    @app.get("/")
    def health() -> dict:
        return {"status": "ok", "user_id": cfg["user_id"]}

    @app.get("/webhook", response_class=PlainTextResponse)
    def verify(request: Request):
        """Meta 註冊 webhook 時的挑戰：verify_token 對就回傳 challenge。"""
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == cfg["verify"]:
            return PlainTextResponse(q.get("hub.challenge", ""))
        return PlainTextResponse("forbidden", status_code=403)

    @app.post("/webhook")
    async def webhook(request: Request, background_tasks: BackgroundTasks):
        body = await request.body()
        sig = request.headers.get("x-hub-signature-256", "")
        if not _verify_signature(cfg["app_secret"], body, sig):
            return Response(status_code=403)
        import json
        payload = json.loads(body or b"{}")
        background_tasks.add_task(process, cfg, payload)  # 立刻回 200、背景處理
        return {"ok": True}

    return app
