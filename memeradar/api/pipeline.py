"""線上推薦管線：intent → 多路檢索 → rerank → 組裝回應 + 落庫。

框架無關（不 import FastAPI），端點層保持薄。
rerank 拒答時退回純向量排序（``debug.rerank_fallback = true``），
服務不因單一模型呼叫失敗而空手。
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from memeradar.api.schemas import RecommendRequest, TurnIn
from memeradar.matching.intent import (
    DEFAULT_INTENT_MODEL,
    ConversationTurn,
    analyze_conversation,
)
from memeradar.matching.rerank import (
    DEFAULT_RERANK_MODEL,
    RankedMeme,
    RankingParams,
    RerankRefusedError,
    rank_candidates,
)
from memeradar.matching.retrieval import Candidate, RetrievalParams, retrieve_candidates
from memeradar.matching.screenshot import parse_screenshot
from memeradar.matching.search import SearchFilters, SqliteBruteForceSearcher
from memeradar.shared import repository as repo
from memeradar.shared.models import RecommendationLog, new_id
from memeradar.understanding.embedding import Embedder, embedding_signature
from memeradar.understanding.opponent import analyze_opponent_meme, build_battle_turn

VECTOR_FALLBACK_REASON = "（rerank 暫不可用，依向量相似度排序）"


def _vector_fallback(candidates: list[Candidate], top_n: int) -> list[RankedMeme]:
    return [
        RankedMeme(
            meme_id=c.meme_id,
            rank=rank,
            annotation=c.annotation,
            matched_strategy=c.matched_strategies[0],
            matched_tags=tuple(c.annotation.emotions),
            reason=VECTOR_FALLBACK_REASON,
            scores={"vector": c.similarity, "rerank": c.similarity, "final": c.similarity},
        )
        for rank, c in enumerate(candidates[:top_n], start=1)
    ]


def run_recommendation(
    conn: sqlite3.Connection,
    client,
    embedder: Embedder,
    request: RecommendRequest,
    *,
    image_bytes: bytes | None = None,
    vlm=None,  # NVIDIA VLM（截圖 / 對方梗圖解析用）
) -> dict[str, Any]:
    timings: dict[str, int] = {}
    t_start = time.perf_counter()

    conversation = request.conversation
    screenshot_debug: dict | None = None
    opponent_debug: dict | None = None
    if request.input_type == "screenshot":
        # 截圖僅在記憶體處理、不落庫（docs/06 §1）；log 只存解析後文字
        t0 = time.perf_counter()
        parsed = parse_screenshot(vlm, image_bytes or b"")
        timings["screenshot_parse"] = int((time.perf_counter() - t0) * 1000)
        conversation = [TurnIn(speaker=t.speaker, text=t.text) for t in parsed.conversation]
        screenshot_debug = parsed.model_dump()
    elif request.input_type == "meme_battle":
        # 梗圖大戰：理解對方梗圖（僅記憶體、不落庫），合成一則 other 輪次走既有管線
        t0 = time.perf_counter()
        opponent = analyze_opponent_meme(vlm, image_bytes or b"")  # 拒答由端點層轉 422
        timings["opponent_meme"] = int((time.perf_counter() - t0) * 1000)
        conversation = [TurnIn(speaker="other", text=build_battle_turn(opponent))]
        opponent_debug = opponent.model_dump()

    turns = [ConversationTurn(t.speaker, t.text) for t in conversation]
    t0 = time.perf_counter()
    intent = analyze_conversation(client, turns)  # IntentRefusedError 由端點層轉 422
    timings["intent"] = int((time.perf_counter() - t0) * 1000)

    filters = SearchFilters(
        franchises=tuple(request.filters.franchises),
        categories=tuple(request.filters.categories),
        exclude_nsfw=request.filters.exclude_nsfw,
    )
    signature = embedding_signature(embedder)
    searcher = SqliteBruteForceSearcher(conn, signature=signature)
    t0 = time.perf_counter()
    retrieval = retrieve_candidates(
        searcher,
        embedder,
        intent.strategies,
        filters=filters,
        params=RetrievalParams(
            candidate_k=request.params.candidate_k,
            min_similarity=request.params.min_similarity,
        ),
    )
    timings["retrieval"] = int((time.perf_counter() - t0) * 1000)

    rerank_fallback = False
    t0 = time.perf_counter()
    vectors = repo.get_vectors(
        conn,
        kind="text_retrieval",
        model=signature,
        meme_ids=[c.meme_id for c in retrieval.candidates],
    )
    try:
        ranked = rank_candidates(
            client,
            intent,
            retrieval.candidates,
            params=RankingParams(
                top_n=request.params.top_n,
                diversity=request.params.diversity,
                hotness_weight=request.params.hotness_weight,
            ),
            vectors_by_id=vectors,
        )
    except RerankRefusedError:
        rerank_fallback = True
        ranked = _vector_fallback(retrieval.candidates, request.params.top_n)
    timings["rerank"] = int((time.perf_counter() - t0) * 1000)

    query_id = new_id("q")
    results = [
        {
            "meme_id": r.meme_id,
            "image_url": f"/memes/{r.meme_id}/image",
            "rank": r.rank,
            "scores": r.scores,
            "matched_strategy": r.matched_strategy,
            "matched_tags": list(r.matched_tags),
            "reason": r.reason,
        }
        for r in ranked
    ]
    top_ids = {r.meme_id for r in ranked}
    candidates_debug = [
        {
            "meme_id": c.meme_id,
            "ocr_text": c.annotation.ocr_text,
            "vector": c.similarity,
            "per_strategy": c.per_strategy_similarity,
            "in_top": c.meme_id in top_ids,
        }
        for c in retrieval.candidates
    ]
    latency_ms = int((time.perf_counter() - t_start) * 1000)

    repo.insert_recommendation_log(
        conn,
        RecommendationLog(
            query_id=query_id,
            conversation=[t.model_dump() for t in conversation],
            intent_result=intent.model_dump(mode="json"),
            params_snapshot={
                "filters": request.filters.model_dump(),
                "params": request.params.model_dump(),
                "embedding_signature": signature,
                # 記錄產生此推薦的 LLM 模型，供回饋驗證模型選擇（如 haiku vs sonnet）
                "models": {"intent": DEFAULT_INTENT_MODEL, "rerank": DEFAULT_RERANK_MODEL},
            },
            candidates=candidates_debug,
            final_results=results,
            latency_ms=latency_ms,
            timings={**timings, "total": latency_ms},
            input_type=request.input_type,
            client_id=request.client_id,
        ),
    )

    debug: dict[str, Any] = {
        "queries": [s.query for s in intent.strategies],
        "candidates": candidates_debug,
        "per_strategy_hits": retrieval.per_strategy_hits,
        "rerank_fallback": rerank_fallback,
        "timings_ms": {**timings, "total": latency_ms},
    }
    if screenshot_debug is not None:
        debug["screenshot_parse"] = screenshot_debug
    if opponent_debug is not None:
        debug["opponent_meme"] = opponent_debug

    return {
        "query_id": query_id,
        "intent": intent.model_dump(mode="json"),
        "results": results,
        "debug": debug,
    }
