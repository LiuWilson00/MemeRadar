"""FastAPI 應用（契約：docs/01 §5.2）。

- ``create_app(deps)`` 工廠：測試注入 stub client / fake embedder，
  正式執行用 ``create_app()``（讀 settings、BGE-M3 lazy 載入）。
- 請求路徑走 PostgreSQL 連線池（db.get_pool）；背景任務用一次性長連線。
- 公開昂貴端點（/recommend、/tasks）依 IP 限流（RateLimiter）。
- 啟動：``python -m memeradar.api``。
"""

from __future__ import annotations

import base64
import binascii
import random
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psycopg
import psycopg.errors
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from memeradar.api.pipeline import run_fast_recommendation, run_recommendation
from memeradar.api.ratelimit import RateLimiter
from memeradar.api.schemas import (
    BugReportRequest,
    ChatFeedbackRequest,
    ChatRequest,
    ClientErrorRequest,
    CommentRequest,
    CommentUpdateRequest,
    DedupResolutionRequest,
    EventRequest,
    FeedbackRequest,
    GoogleAuthRequest,
    LibraryUploadRequest,
    LikeRequest,
    ModelSettingsRequest,
    NicknameRequest,
    ParseScreenshotRequest,
    RecommendRequest,
    ReportRequest,
    ReportResolutionRequest,
    ReviewAnnotationRequest,
    UploadMemeRequest,
)
from memeradar.ingestion.dedup import merge_duplicate_into
from memeradar.ingestion.seed_import import import_image_bytes
from memeradar.matching.intent import IntentRefusedError
from memeradar.matching.screenshot import ScreenshotParseError, parse_screenshot
from memeradar.shared import repository as repo
from memeradar.shared.auth import issue_session, verify_session
from memeradar.shared.db import connect, get_pool, migrate
from memeradar.shared.models import Embedding, FeedbackEvent, new_id
from memeradar.shared.taxonomy import get_taxonomy
from memeradar.understanding.annotator import annotate_meme
from memeradar.understanding.embedding import Embedder, embed_pending_memes, embedding_signature
from memeradar.understanding.nvidia_vlm import VlmExhaustedError
from memeradar.understanding.opponent import OpponentMemeRefusedError
from memeradar.understanding.retrieval_doc import build_retrieval_document

_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}

_TASK_LABELS = {
    "annotation": "梗圖標註",
    "intent": "對話意圖分析",
    "rerank": "重排序",
    "screenshot": "截圖解析",
    "opponent": "對方梗圖解讀",
}


@dataclass
class Deps:
    client: Any  # anthropic client（意圖 / rerank / 截圖 / 對方梗圖）
    vlm: Any  # NvidiaVlm（標註）
    embedder: Embedder
    db_path: Path
    data_dir: Path
    admin_username: str = ""  # 後台登入；空 = 不設防
    admin_password: str = ""
    cors_origins: tuple[str, ...] = ()  # 允許跨源的前端網域（本地留空＝走 vite proxy）
    r2_public_base_url: str = ""  # 有值 = 圖片改由 R2 CDN 服務（302 導向）
    rate_limiter: Any = None  # RateLimiter | None；None = 不限流（測試預設）
    # 背景任務排程器：接一個 no-arg callable。None = 用內建 thread pool；
    # 測試注入 ``lambda fn: fn()`` 讓非同步任務同步跑完。每請求讀取，故可事後覆寫。
    run_async: Any = None
    # Google 登入：token_verifier 為 callable(credential)->claims，驗無效丟 ValueError；
    # session_secret 用來簽我方 JWT。三者皆空 = 未啟用使用者登入。
    google_client_id: str = ""
    session_secret: str = ""
    token_verifier: Any = None
    # 未登入者每日推薦次數上限（登入者不限）；僅在登入啟用（session_secret）時生效。
    anon_daily_quota: int = 5
    # 每位登入使用者每日上傳共用圖庫的上限（防洗版）；0 = 不限。
    user_upload_daily_quota: int = 10
    # 是否啟動背景標註 worker（大量匯入時先入庫、標註丟背景）；測試預設關。
    enable_annotation_worker: bool = False
    # 快速模式用（跳過 VLM）：ocr=NvidiaOcr（Nemotron OCR v2）、classifier=ZeroShotClassifier
    # （NV-CLIP 沒字圖分類）。無 NVIDIA key 時為 None（fast_mode 請求會落背景任務 error）。
    ocr: Any = None
    classifier: Any = None


def _default_deps() -> Deps:
    import anthropic

    from memeradar.api.google_auth import build_google_verifier
    from memeradar.shared.config import get_settings
    from memeradar.understanding.annotator import build_default_vlm
    from memeradar.understanding.embedding import get_embedder

    settings = get_settings()
    api_key = settings.anthropic_api_key
    ocr, classifier = _build_fast_clients(settings)
    return Deps(
        client=anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic(),
        vlm=build_default_vlm(),
        embedder=get_embedder(settings.embedding_backend),
        db_path=settings.memeradar_data_dir,  # 連線改由 DATABASE_URL；此欄僅為相容保留
        data_dir=settings.memeradar_data_dir,
        admin_username=settings.admin_username,
        admin_password=settings.admin_password,
        cors_origins=tuple(settings.cors_origin_list()),
        r2_public_base_url=settings.r2_public_base_url,
        rate_limiter=(
            RateLimiter(settings.rate_limit_per_min, 60.0)
            if settings.rate_limit_per_min > 0
            else None
        ),
        google_client_id=settings.google_client_id,
        session_secret=settings.session_secret,
        token_verifier=(
            build_google_verifier(settings.google_client_id)
            if settings.google_client_id else None
        ),
        anon_daily_quota=settings.anon_daily_quota,
        user_upload_daily_quota=settings.user_upload_daily_quota,
        enable_annotation_worker=True,
        ocr=ocr,
        classifier=classifier,
    )


def _build_fast_clients(settings):
    """快速模式的 NVIDIA client（同一組 key）：OCR + 沒字圖分類器。無 key 則回 (None, None)。"""
    keys = settings.nvidia_keys()
    if not keys:
        return None, None
    from memeradar.shared.taxonomy import get_taxonomy
    from memeradar.understanding.nvclip import NvClip, ZeroShotClassifier
    from memeradar.understanding.ocr import NvidiaOcr

    tax = get_taxonomy()
    # 零樣本詞彙：情緒為主（NV-CLIP 對表情敏感）+ 已知分類，與梗圖標註語彙對齊
    vocab = list(dict.fromkeys([*tax.emotions, *tax.known_categories]))
    return NvidiaOcr(keys), ZeroShotClassifier(NvClip(keys), vocab)


# 前台（手機 client）需要的公開路徑；其餘一律歸後台（admin）
# 註：/auth/* 為前台使用者登入，非後台 admin，故列公開（其自身以 Bearer 把關）。
# 註：/recommend（同步、直打 VLM）刻意「不」公開——前台一律走有配額的 /tasks，
# /recommend 僅供後台除錯（admin Basic）。放公開會讓匿名者繞過每日配額直打 VLM。
_PUBLIC_EXACT = {
    "/health", "/feedback", "/meta", "/tasks", "/events", "/leaderboard", "/chat",
    "/auth/google", "/auth/me", "/auth/nickname", "/library/memes", "/gallery",
    "/docs", "/openapi.json",
}

# /events 接受的事件類型（白名單，防亂塞）
_ALLOWED_EVENTS = {"download", "category", "search"}


def _is_public(method: str, path: str) -> bool:
    import re

    if path in _PUBLIC_EXACT:
        return True
    # 梗圖圖片：手機端要顯示，公開（僅 GET）
    if method == "GET" and re.match(r"^/memes/[^/]+/image$", path) is not None:
        return True
    # 檢舉：前台任何人都能檢舉一張梗圖（僅 POST）
    if method == "POST" and re.match(r"^/memes/[^/]+/report$", path) is not None:
        return True
    # 前台錯誤回報：POST 公開（GET 讀取限後台）
    if method == "POST" and path == "/client-errors":
        return True
    # 問題回報：POST 公開（GET 讀取限後台）
    if method == "POST" and path == "/bug-reports":
        return True
    # 梗友回饋：POST 公開（GET 讀取限後台）
    if method == "POST" and path == "/chat/feedback":
        return True
    # 探索圖庫：按讚 / 彈幕留言（前台，各種方法）
    if re.match(r"^/memes/[^/]+/(like|comments)$", path) is not None:
        return True
    if re.match(r"^/memes/[^/]+/comments/[^/]+$", path) is not None:
        return True
    # 任務進度查詢：前台輪詢，公開（僅 GET）
    return method == "GET" and re.match(r"^/tasks/[^/]+$", path) is not None


def _task_label(request: RecommendRequest) -> str:
    """歷史列表用的短標題：截圖 / 梗圖大戰 給固定字；純文字取對話首句。"""
    if request.input_type == "screenshot":
        return "截圖對話"
    if request.input_type == "meme_battle":
        return "梗圖大戰"
    for turn in request.conversation:
        text = turn.text.strip()
        if text:
            return text[:24] + ("…" if len(text) > 24 else "")
    return "對話"


def _client_key(request: Request) -> str:
    """限流的 key：優先取 X-Forwarded-For 第一段（Zeabur 等反代後才是真實 IP）。"""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _enforce_rate_limit(deps: Deps, request: Request) -> None:
    if deps.rate_limiter is not None and not deps.rate_limiter.allow(_client_key(request)):
        raise HTTPException(status_code=429, detail="請求過於頻繁，請稍後再試")


def _run_task(deps: Deps, task_id: str, request: RecommendRequest,
              image_bytes: bytes | None) -> None:
    """背景執行一筆推薦任務，寫回 done/error（自開連線；任何例外都收斂為 error）。"""
    conn = connect(deps.db_path)
    try:
        repo.set_task_status(conn, task_id, "running")
        if request.fast_mode:
            result = run_fast_recommendation(
                conn, deps.ocr, deps.classifier, deps.embedder, request,
                image_bytes=image_bytes,
            )
        else:
            result = run_recommendation(
                conn, deps.vlm, deps.embedder, request,
                image_bytes=image_bytes, models=repo.get_task_models(conn),
            )
        repo.set_task_status(conn, task_id, "done", result=result)
    except IntentRefusedError:
        repo.set_task_status(conn, task_id, "error", error="模型基於安全政策拒絕分析此對話")
    except ScreenshotParseError as exc:
        repo.set_task_status(conn, task_id, "error", error=f"截圖解析失敗：{exc}")
    except OpponentMemeRefusedError:
        repo.set_task_status(conn, task_id, "error", error="模型基於安全政策拒絕解析對方梗圖")
    except Exception as exc:  # noqa: BLE001 背景任務不可讓工作執行緒崩潰
        repo.set_task_status(conn, task_id, "error", error=f"推薦失敗：{exc}")
    finally:
        conn.close()


def _persist_image(conn: psycopg.Connection, meme_id: str, image_uri: str, content: bytes) -> None:
    """圖檔落地：有 R2 憑證就上傳 R2（CDN 服務）；否則存進 DB image_data（免 volume）。"""
    from memeradar.shared.config import get_settings

    settings = get_settings()
    if settings.r2_upload_enabled():
        from memeradar.shared import storage

        storage.put_image(settings, image_uri, content)
    else:
        conn.execute("UPDATE memes SET image_data = %s WHERE meme_id = %s", (content, meme_id))
        conn.commit()


def _public_user(user: dict) -> dict:
    """對外只回這幾個欄位（隱去 google_sub 等內部欄位）。"""
    return {k: user.get(k) for k in ("user_id", "email", "name", "picture", "role", "nickname")}


def _pick_chat_meme(hits: list, exclude: set[str]):
    """從檢索結果挑一張回應：濾掉這輪已回過的，再依相似度加權隨機（偏高相似度但保留多樣）。
    全被排除就回退到不排除（寧可重複，也要回一張）。回 None 表示完全沒得回。"""
    if not hits:
        return None
    candidates = [h for h in hits if h.meme_id not in exclude] or hits
    weights = [max(h.similarity, 0.01) ** 2 for h in candidates]
    return random.choices(candidates, weights=weights, k=1)[0]


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if header and header.startswith("Bearer "):
        return header[7:].strip()
    return None


def _resolve_user(deps: Deps, request: Request, conn: psycopg.Connection) -> dict | None:
    """從 Authorization: Bearer 解出登入使用者；未登入 / 無效回 None。"""
    if not deps.session_secret:
        return None
    token = _bearer_token(request)
    if not token:
        return None
    user_id = verify_session(token, deps.session_secret)
    if not user_id:
        return None
    return repo.get_user(conn, user_id)


def annotate_one_pending(deps: Deps, conn: psycopg.Connection) -> bool:
    """背景標註佇列的一次工作單元：撿一張未標註的 active 梗圖，標註＋向量化。

    回傳是否有處理到（False = 佇列空）。標註本身可能拒答→轉 pending_review，
    或限流耗盡→拋例外（由呼叫端吞掉，下輪再試）。
    """
    pending = repo.list_active_unannotated(conn, limit=1)
    if not pending:
        return False
    meme = pending[0]
    try:
        annotation = annotate_meme(conn, deps.vlm, meme, data_dir=deps.data_dir)
    except VlmExhaustedError:
        raise  # 限流耗盡＝暫時性：維持 active，交給 worker 等下輪重試
    except Exception as exc:  # noqa: BLE001 永久性錯誤（圖檔遺失/損毀等）
        # 轉 pending_review 排除出佇列，避免同一張壞圖無限重試阻塞其他 105 張
        repo.set_status(conn, meme.meme_id, "pending_review")
        print(
            f"[annotate] {meme.meme_id} 標註失敗，轉 pending_review：{exc!r}",
            file=sys.stderr, flush=True,
        )
        return True
    if annotation is not None and annotation.is_meme:
        embed_pending_memes(conn, deps.embedder)
    return True


def _annotation_worker(deps: Deps) -> None:
    """常駐背景執行緒：慢慢把待標註的梗圖標註完（照 NVIDIA 限流節奏）。"""
    while True:
        did = False
        conn = connect(deps.db_path)
        try:
            did = annotate_one_pending(deps, conn)
        except Exception:  # noqa: BLE001 限流耗盡等 → 留待下輪，別讓 worker 掛掉
            did = False
        finally:
            conn.close()
        time.sleep(1.0 if did else 8.0)  # 有活就快點跑；沒活就緩一緩


def _check_basic_auth(header: str | None, user: str, password: str) -> bool:
    import binascii
    import secrets

    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    got_user, _, got_pass = decoded.partition(":")
    return secrets.compare_digest(got_user, user) and secrets.compare_digest(got_pass, password)


def create_app(deps: Deps | None = None) -> FastAPI:
    if deps is None:
        deps = _default_deps()
        # 啟動即暖機（本地 BGE 冷載入 / 驗證 NVIDIA embedding 連線）；best-effort，
        # 暫時性錯誤不擋啟動。
        try:
            deps.embedder.embed(["暖機"])
        except Exception:  # noqa: BLE001
            pass

    # 跨源部署（有設 CORS_ORIGINS）卻沒設後台帳密 → 後台端點全裸；直接拒啟（fail closed）。
    if deps.cors_origins and not (deps.admin_username and deps.admin_password):
        raise RuntimeError(
            "偵測到跨源部署（CORS_ORIGINS 已設）但未設 ADMIN_USERNAME/ADMIN_PASSWORD——"
            "後台端點（上傳/複核/設定/報表/檢舉/儀表板）將完全不設防。請設好後台帳密再啟動。"
        )

    startup_conn = connect(deps.db_path)
    migrate(startup_conn)
    # 背景任務池不跨程序重啟：把上次殘留的 pending/running 標成 error，前台才不會永遠輪詢
    repo.abort_orphan_tasks(startup_conn)
    startup_conn.close()

    # 背景標註 worker：大量匯入時「先入庫、標註丟背景」，這條常駐執行緒慢慢消化佇列。
    if deps.enable_annotation_worker:
        threading.Thread(target=_annotation_worker, args=(deps,), daemon=True).start()

    app = FastAPI(title="MemeRadar API", version="0.1.0")

    @app.exception_handler(Exception)
    async def _capture_server_error(request: Request, exc: Exception):
        """未攔截的 500：traceback 寫進 client_errors（後台可見）+ stderr（Zeabur log）。

        HTTPException / 422 由 FastAPI 自己處理，不會進來。純 OOM 攔不到（process 被殺）。
        """
        import sys
        import traceback

        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        print(f"[500] {request.method} {request.url.path}\n{tb}", file=sys.stderr, flush=True)
        try:
            with get_pool().connection() as conn:
                repo.insert_client_error(
                    conn,
                    message=f"{type(exc).__name__}: {exc}"[:2000],
                    stack=tb[:8000],
                    url=f"{request.method} {request.url.path}",
                    user_agent=request.headers.get("user-agent"),
                    client_id="__server__",  # 標記為伺服器端錯誤（有別於前台回報）
                )
        except Exception:  # noqa: BLE001 記錄失敗不能再拋，否則遮蔽原始錯誤
            pass
        return JSONResponse(status_code=500, content={"detail": "內部錯誤"})

    from concurrent.futures import ThreadPoolExecutor

    # 免費端點延遲高（冷啟動可達數十秒），故推薦走背景任務；小池即可，
    # 免得多任務併發把 BGE / VLM 打爆（前台一次也只送一筆）。
    task_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="task")

    @app.middleware("http")
    async def admin_gate(request, call_next):
        """後台（admin）路徑需 env 帳密登入；前台公開路徑放行。帳密未設 = 不設防。"""
        if deps.admin_username and deps.admin_password and request.method != "OPTIONS":
            if not _is_public(request.method, request.url.path):
                header = request.headers.get("Authorization")
                if not _check_basic_auth(header, deps.admin_username, deps.admin_password):
                    return JSONResponse(
                        {"detail": "需要後台登入"},
                        status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="MemeRadar Admin"'},
                    )
        return await call_next(request)

    # CORS 加在 admin_gate 之後 → 成為最外層，連 401 回應都帶 CORS 標頭
    # （前端跨源送 admin Basic auth 時，才能讀到 401 而非被瀏覽器擋成網路錯誤）。
    if deps.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(deps.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def get_conn() -> Iterator[psycopg.Connection]:
        # 請求路徑走連線池（短連線）；context manager 會 commit/rollback 並歸還連線
        with get_pool().connection() as conn:
            yield conn

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    def _decode_image(image_b64: str | None) -> bytes:
        if not image_b64:
            raise HTTPException(status_code=422, detail="此輸入類型需提供 image（base64）")
        try:
            return base64.b64decode(image_b64, validate=True)
        except binascii.Error:
            raise HTTPException(status_code=422, detail="image 不是有效的 base64") from None

    @app.post("/recommend")
    def recommend(request: RecommendRequest, http_request: Request,
                  conn: psycopg.Connection = Depends(get_conn)):
        _enforce_rate_limit(deps, http_request)
        image_bytes: bytes | None = None
        if request.input_type in ("screenshot", "meme_battle"):
            image_bytes = _decode_image(request.image)
        elif not request.conversation:
            raise HTTPException(status_code=422, detail="conversation 不可為空")
        if request.fast_mode:
            return run_fast_recommendation(
                conn, deps.ocr, deps.classifier, deps.embedder, request,
                image_bytes=image_bytes,
            )
        try:
            return run_recommendation(
                conn, deps.vlm, deps.embedder, request, image_bytes=image_bytes
            )
        except IntentRefusedError:
            raise HTTPException(
                status_code=422, detail="模型基於安全政策拒絕分析此對話"
            ) from None
        except ScreenshotParseError as exc:
            raise HTTPException(status_code=422, detail=f"截圖解析失敗：{exc}") from None
        except OpponentMemeRefusedError:
            raise HTTPException(
                status_code=422, detail="模型基於安全政策拒絕解析對方梗圖"
            ) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"梗圖無法解析：{exc}") from None

    @app.post("/tasks", status_code=202)
    def submit_task(request: RecommendRequest, http_request: Request,
                    conn: psycopg.Connection = Depends(get_conn)):
        """送出非同步推薦：立刻回 task_id，實際運算在背景跑（user 可離開再回來查）。"""
        _enforce_rate_limit(deps, http_request)
        # 未登入者每日配額（僅在登入啟用時生效；登入者不受限，藉此鼓勵註冊）
        if deps.session_secret and deps.anon_daily_quota > 0:
            if _resolve_user(deps, http_request, conn) is None:
                used = repo.count_tasks_today(conn, request.client_id or "")
                if used >= deps.anon_daily_quota:
                    raise HTTPException(status_code=429, detail={
                        "error": "quota_exceeded",
                        "limit": deps.anon_daily_quota,
                        "message": f"今天的免費次數用完了（{deps.anon_daily_quota}/"
                                   f"{deps.anon_daily_quota}），登入即可無限使用。",
                    })
        image_bytes: bytes | None = None
        if request.input_type in ("screenshot", "meme_battle"):
            image_bytes = _decode_image(request.image)  # 壞 base64 當場 422，不進背景
        elif not request.conversation:
            raise HTTPException(status_code=422, detail="conversation 不可為空")
        task_id = new_id("task")
        repo.create_task(
            conn, task_id, client_id=request.client_id or "",
            input_type=request.input_type, label=_task_label(request),
        )
        runner = deps.run_async if deps.run_async is not None else task_executor.submit
        runner(lambda: _run_task(deps, task_id, request, image_bytes))
        return {"task_id": task_id, "status": "pending"}

    @app.get("/tasks")
    def list_tasks(client_id: str, limit: int = 50,
                   conn: psycopg.Connection = Depends(get_conn)):
        """某 client 的歷史任務（新到舊，精簡欄位，不夾帶完整 result）。"""
        return repo.list_tasks_by_client(conn, client_id, limit=limit)

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str, conn: psycopg.Connection = Depends(get_conn)):
        """單一任務進度 / 結果（前台輪詢）。"""
        task = repo.get_task(conn, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任務不存在")
        return task

    @app.post("/parse-screenshot")
    def parse_screenshot_endpoint(request: ParseScreenshotRequest):
        """解析截圖供 Console 編修（截圖僅在記憶體處理，不落庫）。"""
        image_bytes = _decode_image(request.image)
        try:
            return parse_screenshot(deps.vlm, image_bytes).model_dump()
        except ScreenshotParseError as exc:
            raise HTTPException(status_code=422, detail=f"截圖解析失敗：{exc}") from None

    @app.post("/feedback")
    def feedback(request: FeedbackRequest, conn: psycopg.Connection = Depends(get_conn)):
        event = FeedbackEvent(
            feedback_id=new_id("f"),
            query_id=request.query_id,
            meme_id=request.meme_id,
            rank=request.rank,
            rating=request.rating,
            note=request.note,
        )
        try:
            repo.insert_feedback(conn, event)
        except psycopg.errors.IntegrityError:
            raise HTTPException(
                status_code=404, detail="query_id 或 meme_id 不存在"
            ) from None
        return {"feedback_id": event.feedback_id}

    @app.post("/events", status_code=202)
    def log_event(request: EventRequest, http_request: Request,
                  conn: psycopg.Connection = Depends(get_conn)):
        """前台行為事件（下載/選分類）。best-effort：型別不合或寫入失敗都不擋前台。"""
        _enforce_rate_limit(deps, http_request)
        if request.event_type not in _ALLOWED_EVENTS:
            raise HTTPException(status_code=422, detail="未知的事件類型")
        try:
            repo.insert_event(conn, request.event_type, client_id=request.client_id,
                              meme_id=request.meme_id, meta=request.meta)
        except psycopg.errors.Error:
            conn.rollback()  # meme_id 不存在等 → 忽略，不回報錯誤
        return {"ok": True}

    @app.get("/leaderboard")
    def leaderboard(limit: int = 20, conn: psycopg.Connection = Depends(get_conn)):
        """熱門梗圖榜（讚×3 + 下載）；公開，供前台 Modal。資料少時自然回短/空清單。"""
        rows = repo.leaderboard(conn, limit=min(max(1, limit), 50))
        for row in rows:
            row["image_url"] = f"/memes/{row['meme_id']}/image"
        return rows

    @app.post("/chat")
    def chat(request: ChatRequest, http_request: Request,
             conn: psycopg.Connection = Depends(get_conn)):
        """只會回梗圖的朋友：一則訊息 → 一張梗圖（embedding 檢索、無 LLM/VLM、秒回）。"""
        _enforce_rate_limit(deps, http_request)
        message = request.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="訊息不可為空")
        from memeradar.matching.search import SearchFilters, SqliteBruteForceSearcher
        from memeradar.understanding.embedding import embedding_signature

        query_vec = deps.embedder.embed([message])[0]
        searcher = SqliteBruteForceSearcher(conn, signature=embedding_signature(deps.embedder))
        hits = searcher.search(query_vec, k=15, filters=SearchFilters(exclude_nsfw=True))
        pick = _pick_chat_meme(hits, set(request.exclude or []))
        if pick is None:
            return {"meme": None, "similarity": None, "fallback": True}
        repo.insert_event(conn, "chat", client_id=request.client_id, meme_id=pick.meme_id)
        return {
            "meme": {
                "meme_id": pick.meme_id,
                "image_url": f"/memes/{pick.meme_id}/image",
                "ocr_text": pick.annotation.ocr_text,
                "franchise": pick.annotation.franchise,
            },
            "similarity": pick.similarity,
            "fallback": pick.similarity < 0.3,
        }

    @app.post("/chat/feedback", status_code=202)
    def chat_feedback(request: ChatFeedbackRequest, http_request: Request,
                      conn: psycopg.Connection = Depends(get_conn)):
        """對梗友的一則回覆評價（👍/👎）。公開、限流；存進 events 供之後優化選圖。"""
        _enforce_rate_limit(deps, http_request)
        if repo.get_meme(conn, request.meme_id) is None:
            raise HTTPException(status_code=404, detail="梗圖不存在")
        repo.insert_event(
            conn, "chat_feedback", client_id=request.client_id, meme_id=request.meme_id,
            meta={"rating": request.rating, "message": (request.message or "")[:500]},
        )
        return {"ok": True}

    @app.get("/chat/feedback")
    def list_chat_feedback(limit: int = 200, conn: psycopg.Connection = Depends(get_conn)):
        """後台：梗友回覆評價（新到舊），供優化選圖。"""
        return repo.list_chat_feedback(conn, limit=min(max(1, limit), 500))

    @app.get("/gallery")
    def gallery(client_id: str = "", seed: str = "", offset: int = 0, limit: int = 24,
                conn: psycopg.Connection = Depends(get_conn)):
        """探索圖庫一頁（瀑布流）：active 非 NSFW 梗圖，隨機但依 seed 穩定分頁。"""
        items = repo.list_gallery(
            conn, seed=seed or "default", offset=max(0, offset),
            limit=min(max(1, limit), 48), client_id=client_id)
        for it in items:
            it["image_url"] = f"/memes/{it['meme_id']}/image"
        return items

    @app.post("/memes/{meme_id}/like")
    def like_meme(meme_id: str, request: LikeRequest, http_request: Request,
                  conn: psycopg.Connection = Depends(get_conn)):
        """按讚 / 取消讚（回新的讚數與狀態）。"""
        _enforce_rate_limit(deps, http_request)
        if repo.get_meme(conn, meme_id) is None:
            raise HTTPException(status_code=404, detail="梗圖不存在")
        return repo.toggle_like(conn, meme_id, request.client_id)

    @app.get("/memes/{meme_id}/comments")
    def list_meme_comments(meme_id: str, client_id: str = "",
                           conn: psycopg.Connection = Depends(get_conn)):
        """某梗圖的彈幕留言（舊到新）。"""
        return repo.list_comments(conn, meme_id, client_id=client_id or None)

    @app.post("/memes/{meme_id}/comments", status_code=201)
    def add_meme_comment(meme_id: str, request: CommentRequest, http_request: Request,
                         conn: psycopg.Connection = Depends(get_conn)):
        """留一則彈幕（限流、長度上限見 schema）。"""
        _enforce_rate_limit(deps, http_request)
        if repo.get_meme(conn, meme_id) is None:
            raise HTTPException(status_code=404, detail="梗圖不存在")
        text = request.text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="留言不可為空")
        return repo.add_comment(
            conn, meme_id, request.client_id, request.author_name.strip() or "路人", text)

    @app.patch("/memes/{meme_id}/comments/{comment_id}")
    def edit_meme_comment(meme_id: str, comment_id: str, request: CommentUpdateRequest,
                          conn: psycopg.Connection = Depends(get_conn)):
        """編修自己的彈幕（client_id 需相符）。"""
        text = request.text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="留言不可為空")
        if not repo.update_comment(conn, comment_id, request.client_id, text):
            raise HTTPException(status_code=403, detail="只能編修自己的留言")
        return {"ok": True}

    @app.delete("/memes/{meme_id}/comments/{comment_id}")
    def delete_meme_comment(meme_id: str, comment_id: str, client_id: str = "",
                            conn: psycopg.Connection = Depends(get_conn)):
        """刪除自己的彈幕（client_id 需相符）。"""
        if not repo.delete_comment(conn, comment_id, client_id):
            raise HTTPException(status_code=403, detail="只能刪除自己的留言")
        return {"ok": True}

    @app.put("/auth/nickname")
    def set_nickname(request: NicknameRequest, http_request: Request,
                     conn: psycopg.Connection = Depends(get_conn)):
        """登入使用者設定顯示暱稱。"""
        user = _resolve_user(deps, http_request, conn)
        if user is None:
            raise HTTPException(status_code=401, detail="請先登入")
        nickname = request.nickname.strip()
        repo.set_user_nickname(conn, user["user_id"], nickname)
        return {"nickname": nickname}

    @app.post("/auth/google")
    def auth_google(request: GoogleAuthRequest,
                    conn: psycopg.Connection = Depends(get_conn)):
        """Google 登入：驗 ID token → 建/更新使用者 → 回我方 session token。"""
        if deps.token_verifier is None or not deps.session_secret:
            raise HTTPException(status_code=503, detail="使用者登入尚未設定")
        try:
            claims = deps.token_verifier(request.credential)
        except Exception:  # noqa: BLE001 驗證器對任何無效 token 皆丟例外
            raise HTTPException(status_code=401, detail="Google 登入驗證失敗") from None
        sub = claims.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Google 登入驗證失敗")
        user = repo.upsert_user(
            conn, google_sub=str(sub), email=claims.get("email"),
            name=claims.get("name"), picture=claims.get("picture"),
        )
        return {"token": issue_session(user["user_id"], deps.session_secret),
                "user": _public_user(user)}

    @app.get("/auth/me")
    def auth_me(http_request: Request, conn: psycopg.Connection = Depends(get_conn)):
        """回目前登入使用者；未帶有效 Bearer 則 401。"""
        user = _resolve_user(deps, http_request, conn)
        if user is None:
            raise HTTPException(status_code=401, detail="尚未登入")
        return _public_user(user)

    @app.get("/history")
    def history(limit: int = 50, offset: int = 0,
                conn: psycopg.Connection = Depends(get_conn)):
        return repo.list_recommendation_logs(conn, limit=limit, offset=offset)

    @app.get("/history/{query_id}")
    def history_detail(query_id: str, conn: psycopg.Connection = Depends(get_conn)):
        log = repo.get_recommendation_log(conn, query_id)
        if log is None:
            raise HTTPException(status_code=404, detail="查詢紀錄不存在")
        return asdict(log)

    @app.get("/memes")
    def list_memes(
        franchise: str | None = None,
        category: str | None = None,
        emotion: str | None = None,
        status: str | None = None,
        limit: int = 200,
        conn: psycopg.Connection = Depends(get_conn),
    ):
        rows = repo.list_memes_with_annotations(
            conn, franchise=franchise, category=category, emotion=emotion,
            status=status, limit=limit,
        )
        for row in rows:
            row["image_url"] = f"/memes/{row['meme_id']}/image"
        return rows

    @app.post("/memes")
    def upload_meme(request: UploadMemeRequest, conn: psycopg.Connection = Depends(get_conn)):
        """手動上傳（seed 匯入口）：匯入 → 立即標註 → 立即向量化，完成即可檢索。"""
        content = _decode_image(request.image)
        meme, status = import_image_bytes(
            conn, content, data_dir=deps.data_dir, source_title=request.title_hint
        )
        if status == "duplicate":
            raise HTTPException(status_code=409, detail=f"圖片已存在（{meme.meme_id}）")
        if status == "too_large":
            raise HTTPException(status_code=413, detail="圖片解析度太高，請用一般尺寸的梗圖")
        if status in ("error", "unsupported"):
            raise HTTPException(
                status_code=422, detail="無法讀取圖片（僅支援 PNG / JPEG / WebP）"
            )
        _persist_image(conn, meme.meme_id, meme.image_uri, content)
        # 大量匯入模式：只入庫，標註交給背景 worker（秒級回、不卡）；圖尚無標註/向量，
        # 故暫不進推薦池與圖庫，等背景標註完才浮現。
        if not request.annotate:
            return {
                "meme_id": meme.meme_id,
                "status": "imported",
                "meme_status": repo.get_meme(conn, meme.meme_id).status,
                "annotation": None,
                "embedded": False,
                "annotation_pending": True,
                "image_url": f"/memes/{meme.meme_id}/image",
            }
        # 模型優先序：此次請求指定 > 後台設定的標註模型 > VLM 預設
        model = request.model or repo.get_task_models(conn).get("annotation")
        annotation = annotate_meme(
            conn, deps.vlm, meme, data_dir=deps.data_dir, model=model
        )
        embedded = 0
        if annotation is not None and annotation.is_meme:
            embedded = embed_pending_memes(conn, deps.embedder)
        return {
            "meme_id": meme.meme_id,
            "status": "imported",
            "meme_status": repo.get_meme(conn, meme.meme_id).status,
            "annotation": asdict(annotation) if annotation is not None else None,
            "embedded": embedded > 0,
            "annotation_pending": False,
            "image_url": f"/memes/{meme.meme_id}/image",
        }

    @app.get("/annotation/pending")
    def annotation_pending(conn: psycopg.Connection = Depends(get_conn)):
        """待背景標註的張數（上傳頁顯示進度用）。"""
        return {"pending": repo.count_active_unannotated(conn)}

    @app.post("/library/memes", status_code=201)
    def library_upload(request: LibraryUploadRequest, http_request: Request,
                       conn: psycopg.Connection = Depends(get_conn)):
        """登入使用者上傳到共用圖庫：去重先於標註（省成本）→ 嚴格 NSFW 把關 → 乾淨即自動上架。"""
        from memeradar.ingestion.dedup import Deduplicator

        user = _resolve_user(deps, http_request, conn)
        if user is None:
            raise HTTPException(status_code=401, detail="請先登入才能貢獻梗圖")
        # 每日上傳上限（防洗版）——最便宜，先擋
        cap = deps.user_upload_daily_quota
        if cap > 0 and repo.count_uploads_today(conn, user["user_id"]) >= cap:
            raise HTTPException(status_code=429, detail={
                "error": "upload_quota_exceeded",
                "limit": cap,
                "message": f"今天的上傳上限（{cap} 張）到了，明天再來。",
            })
        content = _decode_image(request.image)
        # 去重先於標註：完全相同（sha256）或高度相似（phash）都擋，省一次 VLM 呼叫
        if Deduplicator(conn).check(content).layer in ("duplicate", "review"):
            raise HTTPException(status_code=409, detail="圖庫已有相同或非常相似的梗圖了")
        meme, status = import_image_bytes(
            conn, content, data_dir=deps.data_dir,
            source_title=request.title_hint, platform="user",
        )
        if status == "too_large":
            raise HTTPException(status_code=413, detail="圖片解析度太高，請用一般尺寸的梗圖")
        if status in ("error", "unsupported"):
            raise HTTPException(
                status_code=422, detail="無法讀取圖片（僅支援 PNG / JPEG / WebP）")
        if status == "duplicate":  # dedup 應已擋下，保險
            raise HTTPException(status_code=409, detail="圖片已存在")
        # 立即歸屬（含被拒者也計入每日配額，避免洗版者靠丟垃圾重試）
        repo.set_meme_uploaded_by(conn, meme.meme_id, user["user_id"])
        _persist_image(conn, meme.meme_id, meme.image_uri, content)
        annotation = annotate_meme(conn, deps.vlm, meme, data_dir=deps.data_dir)
        # 嚴格把關：拒答 / 非梗圖 / NSFW → 下架、不進推薦池
        reason = None
        if annotation is None:
            reason = "看不懂這張圖，換一張再試"
        elif not annotation.is_meme:
            reason = "這看起來不是梗圖"
        elif annotation.nsfw:
            reason = "偵測到不宜內容，無法上架"
        if reason is not None:
            repo.set_status(conn, meme.meme_id, "removed")
            raise HTTPException(status_code=422, detail=reason)
        # 乾淨 → 自動上架（覆蓋低信心的 pending_review）+ 向量化 + 登記 phash（供之後去重）
        repo.set_status(conn, meme.meme_id, "active")
        embed_pending_memes(conn, deps.embedder)
        Deduplicator(conn).register(meme, content)
        return {
            "meme_id": meme.meme_id,
            "status": "published",
            "image_url": f"/memes/{meme.meme_id}/image",
            "annotation": {"ocr_text": annotation.ocr_text, "franchise": annotation.franchise},
        }

    @app.post("/review/annotations/{meme_id}")
    def review_annotation(
        meme_id: str,
        request: ReviewAnnotationRequest,
        conn: psycopg.Connection = Depends(get_conn),
    ):
        """標註複核：修標籤（可選）+ 通過 / 淘汰；通過即重建檢索向量。"""
        meme = repo.get_meme(conn, meme_id)
        if meme is None:
            raise HTTPException(status_code=404, detail="梗圖不存在")

        annotation = repo.get_annotation(conn, meme_id)
        if request.patch is not None:
            if annotation is None:
                raise HTTPException(status_code=422, detail="尚未標註，無法修補標籤")
            changes = {
                key: value
                for key, value in request.patch.model_dump(mode="json").items()
                if value is not None
            }
            from dataclasses import replace

            annotation = replace(annotation, **changes)
            if not annotation.model_version.endswith("+human"):
                annotation = replace(
                    annotation, model_version=annotation.model_version + "+human"
                )
            repo.upsert_annotation(conn, annotation)

        if request.action == "approve":
            repo.set_status(conn, meme_id, "active")
            if annotation is not None and annotation.is_meme:
                # 標註可能已修改 → 立即重建檢索向量（add_embedding 為 upsert）
                [vector] = deps.embedder.embed([build_retrieval_document(annotation)])
                repo.add_embedding(
                    conn,
                    Embedding(
                        meme_id=meme_id,
                        kind="text_retrieval",
                        model=embedding_signature(deps.embedder),
                        vector=vector,
                    ),
                )
        else:
            repo.set_status(conn, meme_id, "removed")
        return {"meme_id": meme_id, "status": repo.get_meme(conn, meme_id).status}

    @app.get("/review/dedup")
    def dedup_queue(conn: psycopg.Connection = Depends(get_conn)):
        """去重裁決佇列：待人工判定的疑似重複配對（並排比對資料）。"""

        def summary(meme_id: str) -> dict:
            meme = repo.get_meme(conn, meme_id)
            annotation = repo.get_annotation(conn, meme_id)
            return {
                "meme_id": meme_id,
                "image_url": f"/memes/{meme_id}/image",
                "ocr_text": annotation.ocr_text if annotation else "",
                "status": meme.status if meme else "unknown",
            }

        return [
            {
                "review_id": row["review_id"],
                "layer": row["layer"],
                "score": row["score"],
                "created_at": row["created_at"],
                "meme": summary(row["meme_id"]),
                "matched": summary(row["matched_meme_id"]),
            }
            for row in repo.list_dedup_reviews(conn)
        ]

    @app.post("/review/dedup/{review_id}")
    def resolve_dedup(
        review_id: str,
        request: DedupResolutionRequest,
        conn: psycopg.Connection = Depends(get_conn),
    ):
        review = repo.get_dedup_review(conn, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="裁決項不存在")
        if request.resolution == "merged":
            merge_duplicate_into(conn, review["meme_id"], review["matched_meme_id"])
        repo.set_dedup_review_resolution(conn, review_id, request.resolution)
        return {"review_id": review_id, "resolution": request.resolution}

    @app.post("/memes/{meme_id}/report", status_code=202)
    def report_meme(meme_id: str, request: ReportRequest, http_request: Request,
                    conn: psycopg.Connection = Depends(get_conn)):
        """前台檢舉一張梗圖（任何人可用）。記為 report 事件，供後台審視。"""
        _enforce_rate_limit(deps, http_request)
        if repo.get_meme(conn, meme_id) is None:
            raise HTTPException(status_code=404, detail="梗圖不存在")
        user = _resolve_user(deps, http_request, conn)
        # distinct 計數用的檢舉人：優先前台匿名碼 > 登入者 > 連線 IP，確保非空
        reporter = (
            request.client_id or (user["user_id"] if user else None)
            or _client_key(http_request)
        )
        repo.insert_event(
            conn, "report", client_id=reporter, meme_id=meme_id,
            meta={"reason": request.reason} if request.reason else None,
        )
        return {"ok": True}

    @app.get("/review/reports")
    def list_reports(conn: psycopg.Connection = Depends(get_conn)):
        """後台：被檢舉且未處理的梗圖清單（依檢舉人數排序）。"""
        return repo.list_reported_memes(conn)

    @app.post("/review/reports/{meme_id}")
    def resolve_report(meme_id: str, request: ReportResolutionRequest,
                       conn: psycopg.Connection = Depends(get_conn)):
        """後台處理被檢舉的梗圖：remove=下架、dismiss=保留；兩者都清出待辦清單。"""
        if repo.get_meme(conn, meme_id) is None:
            raise HTTPException(status_code=404, detail="梗圖不存在")
        if request.action == "remove":
            repo.set_status(conn, meme_id, "removed")
        repo.resolve_reports(conn, meme_id)
        return {"meme_id": meme_id, "status": repo.get_meme(conn, meme_id).status}

    @app.post("/client-errors", status_code=202)
    def report_client_error(request: ClientErrorRequest, http_request: Request,
                            conn: psycopg.Connection = Depends(get_conn)):
        """前台回報一筆瀏覽器錯誤（公開、限流、長度上限；best-effort）。"""
        _enforce_rate_limit(deps, http_request)
        message = request.message.strip()[:1000]
        if not message:
            raise HTTPException(status_code=422, detail="message 不可為空")
        ua = http_request.headers.get("user-agent")
        repo.insert_client_error(
            conn, message=message,
            stack=(request.stack or "")[:4000] or None,
            url=(request.url or "")[:500] or None,
            user_agent=(ua or "")[:300] or None,
            client_id=request.client_id,
        )
        return {"ok": True}

    @app.get("/client-errors")
    def list_client_errors(limit: int = 100, conn: psycopg.Connection = Depends(get_conn)):
        """後台：最近的前台錯誤（新到舊）。"""
        return repo.list_client_errors(conn, limit=min(max(1, limit), 500))

    @app.post("/bug-reports", status_code=202)
    def submit_bug_report(request: BugReportRequest, http_request: Request,
                          conn: psycopg.Connection = Depends(get_conn)):
        """使用者主動回報問題（公開、限流）：描述 + 操作麵包屑 + 裝置資訊。"""
        _enforce_rate_limit(deps, http_request)
        description = request.description.strip()[:2000]
        if not description:
            raise HTTPException(status_code=422, detail="描述不可為空")
        ua = request.user_agent or http_request.headers.get("user-agent")
        repo.insert_bug_report(
            conn, description=description,
            breadcrumbs=request.breadcrumbs[:120],  # 只留最近 120 筆麵包屑
            url=(request.url or "")[:500] or None,
            user_agent=(ua or "")[:300] or None,
            client_id=request.client_id,
            meta=request.meta,
        )
        return {"ok": True}

    @app.get("/bug-reports")
    def list_bug_reports(limit: int = 200, conn: psycopg.Connection = Depends(get_conn)):
        """後台：使用者回報的問題（新到舊）。"""
        return repo.list_bug_reports(conn, limit=min(max(1, limit), 500))

    @app.get("/report/feedback")
    def feedback_report(conn: psycopg.Connection = Depends(get_conn)):
        from memeradar.shared.reporting import build_feedback_report

        return build_feedback_report(conn)

    @app.get("/report/dashboard")
    def dashboard(conn: psycopg.Connection = Depends(get_conn)):
        from memeradar.shared.reporting import build_dashboard

        return build_dashboard(conn)

    @app.get("/vlm/models")
    def vlm_models() -> dict:
        """標註可用的 NVIDIA vision 模型清單 + 目前預設（Console 切換按鈕用）。"""
        from memeradar.understanding.nvidia_vlm import VISION_MODELS

        current = getattr(deps.vlm, "model", None)
        return {"models": VISION_MODELS, "default": current}

    @app.get("/vlm/usage")
    def vlm_usage(conn: psycopg.Connection = Depends(get_conn)):
        """各 key × 狀態的呼叫數與平均延遲（後台監控用）。"""
        return repo.vlm_call_stats(conn)

    @app.get("/settings/models")
    def get_model_settings(conn: psycopg.Connection = Depends(get_conn)) -> dict:
        """各任務目前的模型設定 + 可選清單 + VLM 預設（後台設定頁用）。"""
        from memeradar.understanding.nvidia_vlm import VISION_MODELS

        configured = repo.get_task_models(conn)
        return {
            "tasks": [
                {"key": key, "label": _TASK_LABELS[key], "current": configured.get(key)}
                for key in repo.TASK_MODEL_KEYS
            ],
            "available": VISION_MODELS,
            "default": getattr(deps.vlm, "model", None),
        }

    @app.put("/settings/models")
    def put_model_settings(
        request: ModelSettingsRequest, conn: psycopg.Connection = Depends(get_conn)
    ) -> dict:
        """設定各任務模型；只接受已知任務鍵，值空 = 回預設。"""
        mapping = {k: v for k, v in request.models.items() if k in repo.TASK_MODEL_KEYS}
        repo.set_task_models(conn, mapping)
        return {"models": repo.get_task_models(conn)}

    @app.get("/memes/{meme_id}/image")
    def meme_image(meme_id: str, conn: psycopg.Connection = Depends(get_conn)):
        meme = repo.get_meme(conn, meme_id)
        if meme is None:
            raise HTTPException(status_code=404, detail="梗圖不存在")
        # 有設 R2 → 導向 CDN 公開網址（圖片位元組不再經過 API/DB）；快取此導向
        if deps.r2_public_base_url:
            from memeradar.shared.storage import public_url

            return RedirectResponse(
                public_url(deps.r2_public_base_url, meme.image_uri),
                status_code=302,
                headers={"Cache-Control": "public, max-age=86400"},
            )
        suffix = Path(meme.image_uri).suffix.lower()
        media_type = _MEDIA_TYPES.get(suffix, "application/octet-stream")
        # 否則優先服務 DB 內的 image_data（雲端免 volume）；再回退檔案系統（本地開發）
        row = conn.execute(
            "SELECT image_data FROM memes WHERE meme_id = %s", (meme_id,)
        ).fetchone()
        if row and row["image_data"] is not None:
            return Response(content=bytes(row["image_data"]), media_type=media_type)
        path = deps.data_dir / meme.image_uri
        if not path.exists():
            raise HTTPException(status_code=404, detail="圖檔遺失")
        return FileResponse(path, media_type=media_type)

    @app.get("/meta")
    def meta(conn: psycopg.Connection = Depends(get_conn)) -> dict:
        taxonomy = get_taxonomy()
        return {
            "franchises": [
                {"name": name, "count": count}
                for name, count in repo.franchise_counts(conn).items()
            ],
            # 分類為開放集：列出庫內實際出現的值（含模型自創），非整份 taxonomy
            "categories": list(repo.category_counts(conn).keys()),
            "strategies": [s.label for s in taxonomy.strategies],
            "emotions": list(taxonomy.emotions),
        }

    return app


def main() -> None:
    import os

    import uvicorn

    # 容器內須綁 0.0.0.0 才對外可達；port 吃平台注入的 $PORT（Zeabur 等），本地預設 8000
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
