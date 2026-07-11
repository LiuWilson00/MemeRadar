"""FastAPI 應用（契約：docs/01 §5.2）。

- ``create_app(deps)`` 工廠：測試注入 stub client / fake embedder，
  正式執行用 ``create_app()``（讀 settings、BGE-M3 lazy 載入）。
- 每請求一條 SQLite 連線（sync 端點跑 threadpool，連線不跨執行緒共用）。
- 啟動：``python -m memeradar.api``。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from memeradar.api.pipeline import run_recommendation
from memeradar.api.schemas import FeedbackRequest, RecommendRequest
from memeradar.matching.intent import IntentRefusedError
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import FeedbackEvent, new_id
from memeradar.shared.taxonomy import get_taxonomy
from memeradar.understanding.embedding import Embedder

_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}


@dataclass
class Deps:
    client: Any  # anthropic client（或測試 stub）
    embedder: Embedder
    db_path: Path
    data_dir: Path


def _default_deps() -> Deps:
    import anthropic

    from memeradar.shared.config import get_settings
    from memeradar.shared.db import default_db_path
    from memeradar.understanding.embedding import DEFAULT_BACKEND, get_embedder

    settings = get_settings()
    api_key = settings.anthropic_api_key
    return Deps(
        client=anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic(),
        embedder=get_embedder(DEFAULT_BACKEND),
        db_path=default_db_path(),
        data_dir=settings.memeradar_data_dir,
    )


def create_app(deps: Deps | None = None) -> FastAPI:
    if deps is None:
        deps = _default_deps()
        # 啟動即暖機：BGE-M3 冷載入約 7s，付在伺服器啟動而非第一個使用者請求
        deps.embedder.embed(["暖機"])

    startup_conn = connect(deps.db_path)
    migrate(startup_conn)
    startup_conn.close()

    app = FastAPI(title="MemeRadar API", version="0.1.0")

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(deps.db_path)
        try:
            yield conn
        finally:
            conn.close()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/recommend")
    def recommend(request: RecommendRequest, conn: sqlite3.Connection = Depends(get_conn)):
        if request.input_type == "screenshot":
            raise HTTPException(status_code=501, detail="截圖解析將於 P2-5 提供")
        if not request.conversation:
            raise HTTPException(status_code=422, detail="conversation 不可為空")
        try:
            return run_recommendation(conn, deps.client, deps.embedder, request)
        except IntentRefusedError:
            raise HTTPException(
                status_code=422, detail="模型基於安全政策拒絕分析此對話"
            ) from None

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
            "categories": [c.label for c in taxonomy.categories],
            "strategies": [s.label for s in taxonomy.strategies],
        }

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
