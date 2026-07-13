"""FastAPI 應用（契約：docs/01 §5.2）。

- ``create_app(deps)`` 工廠：測試注入 stub client / fake embedder，
  正式執行用 ``create_app()``（讀 settings、BGE-M3 lazy 載入）。
- 每請求一條 SQLite 連線（sync 端點跑 threadpool，連線不跨執行緒共用）。
- 啟動：``python -m memeradar.api``。
"""

from __future__ import annotations

import base64
import binascii
import sqlite3
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from memeradar.api.pipeline import run_recommendation
from memeradar.api.schemas import (
    DedupResolutionRequest,
    FeedbackRequest,
    ParseScreenshotRequest,
    RecommendRequest,
    ReviewAnnotationRequest,
    UploadMemeRequest,
)
from memeradar.ingestion.dedup import merge_duplicate_into
from memeradar.ingestion.seed_import import import_image_bytes
from memeradar.matching.intent import IntentRefusedError
from memeradar.matching.screenshot import ScreenshotParseError, parse_screenshot
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Embedding, FeedbackEvent, new_id
from memeradar.shared.taxonomy import get_taxonomy
from memeradar.understanding.annotator import annotate_meme
from memeradar.understanding.embedding import Embedder, embed_pending_memes, embedding_signature
from memeradar.understanding.opponent import OpponentMemeRefusedError
from memeradar.understanding.retrieval_doc import build_retrieval_document

_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}


@dataclass
class Deps:
    client: Any  # anthropic client（意圖 / rerank / 截圖 / 對方梗圖）
    vlm: Any  # NvidiaVlm（標註）
    embedder: Embedder
    db_path: Path
    data_dir: Path
    admin_username: str = ""  # 後台登入；空 = 不設防
    admin_password: str = ""
    # 背景任務排程器：接一個 no-arg callable。None = 用內建 thread pool；
    # 測試注入 ``lambda fn: fn()`` 讓非同步任務同步跑完。每請求讀取，故可事後覆寫。
    run_async: Any = None


def _default_deps() -> Deps:
    import anthropic

    from memeradar.shared.config import get_settings
    from memeradar.shared.db import default_db_path
    from memeradar.understanding.annotator import build_default_vlm
    from memeradar.understanding.embedding import DEFAULT_BACKEND, get_embedder

    settings = get_settings()
    api_key = settings.anthropic_api_key
    return Deps(
        client=anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic(),
        vlm=build_default_vlm(),
        embedder=get_embedder(DEFAULT_BACKEND),
        db_path=default_db_path(),
        data_dir=settings.memeradar_data_dir,
        admin_username=settings.admin_username,
        admin_password=settings.admin_password,
    )


# 前台（手機 client）需要的公開路徑；其餘一律歸後台（admin）
_PUBLIC_EXACT = {
    "/health", "/recommend", "/feedback", "/meta", "/tasks", "/docs", "/openapi.json"
}


def _is_public(method: str, path: str) -> bool:
    import re

    if path in _PUBLIC_EXACT:
        return True
    # 梗圖圖片：手機端要顯示，公開（僅 GET）
    if method == "GET" and re.match(r"^/memes/[^/]+/image$", path) is not None:
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


def _run_task(deps: Deps, task_id: str, request: RecommendRequest,
              image_bytes: bytes | None) -> None:
    """背景執行一筆推薦任務，寫回 done/error（自開連線；任何例外都收斂為 error）。"""
    conn = connect(deps.db_path)
    try:
        repo.set_task_status(conn, task_id, "running")
        result = run_recommendation(
            conn, deps.vlm, deps.embedder, request, image_bytes=image_bytes
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
        # 啟動即暖機：BGE-M3 冷載入約 7s，付在伺服器啟動而非第一個使用者請求
        deps.embedder.embed(["暖機"])

    startup_conn = connect(deps.db_path)
    migrate(startup_conn)
    startup_conn.close()

    app = FastAPI(title="MemeRadar API", version="0.1.0")

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

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(deps.db_path)
        try:
            yield conn
        finally:
            conn.close()

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
    def recommend(request: RecommendRequest, conn: sqlite3.Connection = Depends(get_conn)):
        image_bytes: bytes | None = None
        if request.input_type in ("screenshot", "meme_battle"):
            image_bytes = _decode_image(request.image)
        elif not request.conversation:
            raise HTTPException(status_code=422, detail="conversation 不可為空")
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
    def submit_task(request: RecommendRequest, conn: sqlite3.Connection = Depends(get_conn)):
        """送出非同步推薦：立刻回 task_id，實際運算在背景跑（user 可離開再回來查）。"""
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
                   conn: sqlite3.Connection = Depends(get_conn)):
        """某 client 的歷史任務（新到舊，精簡欄位，不夾帶完整 result）。"""
        return repo.list_tasks_by_client(conn, client_id, limit=limit)

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str, conn: sqlite3.Connection = Depends(get_conn)):
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
    def feedback(request: FeedbackRequest, conn: sqlite3.Connection = Depends(get_conn)):
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
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=404, detail="query_id 或 meme_id 不存在"
            ) from None
        return {"feedback_id": event.feedback_id}

    @app.get("/history")
    def history(limit: int = 50, offset: int = 0,
                conn: sqlite3.Connection = Depends(get_conn)):
        return repo.list_recommendation_logs(conn, limit=limit, offset=offset)

    @app.get("/history/{query_id}")
    def history_detail(query_id: str, conn: sqlite3.Connection = Depends(get_conn)):
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
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        rows = repo.list_memes_with_annotations(
            conn, franchise=franchise, category=category, emotion=emotion,
            status=status, limit=limit,
        )
        for row in rows:
            row["image_url"] = f"/memes/{row['meme_id']}/image"
        return rows

    @app.post("/memes")
    def upload_meme(request: UploadMemeRequest, conn: sqlite3.Connection = Depends(get_conn)):
        """手動上傳（seed 匯入口）：匯入 → 立即標註 → 立即向量化，完成即可檢索。"""
        content = _decode_image(request.image)
        meme, status = import_image_bytes(
            conn, content, data_dir=deps.data_dir, source_title=request.title_hint
        )
        if status == "duplicate":
            raise HTTPException(status_code=409, detail=f"圖片已存在（{meme.meme_id}）")
        if status in ("error", "unsupported"):
            raise HTTPException(
                status_code=422, detail="無法讀取圖片（僅支援 PNG / JPEG / WebP）"
            )
        annotation = annotate_meme(
            conn, deps.vlm, meme, data_dir=deps.data_dir, model=request.model
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
            "image_url": f"/memes/{meme.meme_id}/image",
        }

    @app.post("/review/annotations/{meme_id}")
    def review_annotation(
        meme_id: str,
        request: ReviewAnnotationRequest,
        conn: sqlite3.Connection = Depends(get_conn),
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
    def dedup_queue(conn: sqlite3.Connection = Depends(get_conn)):
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
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        review = repo.get_dedup_review(conn, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="裁決項不存在")
        if request.resolution == "merged":
            merge_duplicate_into(conn, review["meme_id"], review["matched_meme_id"])
        repo.set_dedup_review_resolution(conn, review_id, request.resolution)
        return {"review_id": review_id, "resolution": request.resolution}

    @app.get("/report/feedback")
    def feedback_report(conn: sqlite3.Connection = Depends(get_conn)):
        from memeradar.shared.reporting import build_feedback_report

        return build_feedback_report(conn)

    @app.get("/vlm/models")
    def vlm_models() -> dict:
        """標註可用的 NVIDIA vision 模型清單 + 目前預設（Console 切換按鈕用）。"""
        from memeradar.understanding.nvidia_vlm import VISION_MODELS

        current = getattr(deps.vlm, "model", None)
        return {"models": VISION_MODELS, "default": current}

    @app.get("/memes/{meme_id}/image")
    def meme_image(meme_id: str, conn: sqlite3.Connection = Depends(get_conn)):
        meme = repo.get_meme(conn, meme_id)
        if meme is None:
            raise HTTPException(status_code=404, detail="梗圖不存在")
        path = deps.data_dir / meme.image_uri
        if not path.exists():
            raise HTTPException(status_code=404, detail="圖檔遺失")
        media_type = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=media_type)

    @app.get("/meta")
    def meta(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
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
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
