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
    ConversationTurn,
    IntentResult,
    StrategyPlan,
    analyze_conversation,
)
from memeradar.matching.rerank import (
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
from memeradar.understanding.classifier import Classification
from memeradar.understanding.embedding import Embedder, embedding_signature
from memeradar.understanding.opponent import analyze_opponent_meme, build_battle_turn

VECTOR_FALLBACK_REASON = "（rerank 暫不可用，依向量相似度排序）"
FAST_REASON = "（快速模式：依語意相似度排序）"
# OCR 取出的文字須至少這麼多字元才算「有字」，否則視為無字圖走 NV-CLIP
_FAST_MIN_OCR_CHARS = 2


def _vector_fallback(
    candidates: list[Candidate], top_n: int, *, reason: str = VECTOR_FALLBACK_REASON
) -> list[RankedMeme]:
    return [
        RankedMeme(
            meme_id=c.meme_id,
            rank=rank,
            annotation=c.annotation,
            matched_strategy=c.matched_strategies[0],
            matched_tags=tuple(c.annotation.emotions),
            reason=reason,
            scores={"vector": c.similarity, "rerank": c.similarity, "final": c.similarity},
        )
        for rank, c in enumerate(candidates[:top_n], start=1)
    ]


def run_recommendation(
    conn: sqlite3.Connection,
    vlm,  # NVIDIA VLM（意圖 / rerank / 截圖 / 對方梗圖，全部走 NVIDIA）
    embedder: Embedder,
    request: RecommendRequest,
    *,
    image_bytes: bytes | None = None,
    models: dict[str, str] | None = None,
) -> dict[str, Any]:
    # models：後台各任務模型覆寫（{task: model_id}）；未列入者用 VLM 預設
    pick = (models or {}).get
    # 用量記錄：意圖 / rerank / 截圖 / 對方梗圖的呼叫也寫進 vlm_calls（後台監控）
    sink = lambda rec: repo.insert_vlm_call(conn, rec)  # noqa: E731
    timings: dict[str, int] = {}
    t_start = time.perf_counter()

    conversation = request.conversation
    screenshot_debug: dict | None = None
    opponent_debug: dict | None = None
    if request.input_type == "screenshot":
        # 截圖僅在記憶體處理、不落庫（docs/06 §1）；log 只存解析後文字
        t0 = time.perf_counter()
        parsed = parse_screenshot(vlm, image_bytes or b"", model=pick("screenshot"), log=sink)
        timings["screenshot_parse"] = int((time.perf_counter() - t0) * 1000)
        conversation = [TurnIn(speaker=t.speaker, text=t.text) for t in parsed.conversation]
        screenshot_debug = parsed.model_dump()
    elif request.input_type == "meme_battle":
        # 梗圖大戰：理解對方梗圖（僅記憶體、不落庫），合成一則 other 輪次走既有管線
        t0 = time.perf_counter()
        opponent = analyze_opponent_meme(vlm, image_bytes or b"", model=pick("opponent"), log=sink)
        timings["opponent_meme"] = int((time.perf_counter() - t0) * 1000)
        conversation = [TurnIn(speaker="other", text=build_battle_turn(opponent))]
        opponent_debug = opponent.model_dump()

    turns = [ConversationTurn(t.speaker, t.text) for t in conversation]
    t0 = time.perf_counter()
    intent = analyze_conversation(vlm, turns, model=pick("intent"), log=sink)  # 拒答由端點層轉 422
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
            vlm,
            intent,
            retrieval.candidates,
            params=RankingParams(
                top_n=request.params.top_n,
                diversity=request.params.diversity,
                hotness_weight=request.params.hotness_weight,
            ),
            vectors_by_id=vectors,
            model=pick("rerank"),
            log=sink,
        )
    except RerankRefusedError:
        rerank_fallback = True
        ranked = _vector_fallback(retrieval.candidates, request.params.top_n)
    timings["rerank"] = int((time.perf_counter() - t0) * 1000)

    extra_debug: dict[str, Any] = {}
    if screenshot_debug is not None:
        extra_debug["screenshot_parse"] = screenshot_debug
    if opponent_debug is not None:
        extra_debug["opponent_meme"] = opponent_debug

    return _assemble_and_log(
        conn,
        request,
        conversation,
        intent,
        retrieval,
        ranked,
        signature=signature,
        timings=timings,
        t_start=t_start,
        # 記錄產生此推薦實際用的模型（後台覆寫 > VLM 預設），供回饋分析模型選擇
        models_snapshot={
            "intent": pick("intent") or getattr(vlm, "model", None),
            "rerank": pick("rerank") or getattr(vlm, "model", None),
        },
        rerank_fallback=rerank_fallback,
        extra_debug=extra_debug or None,
    )


def _assemble_and_log(
    conn,
    request: RecommendRequest,
    conversation,
    intent: IntentResult,
    retrieval,
    ranked,
    *,
    signature: str,
    timings: dict[str, int],
    t_start: float,
    models_snapshot: dict,
    rerank_fallback: bool,
    extra_debug: dict | None = None,
) -> dict[str, Any]:
    """組裝 RecommendResponse + 落庫。精準／快速兩路共用，確保回應形狀一致。"""
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
                "models": models_snapshot,
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
    if extra_debug:
        debug.update(extra_debug)

    return {
        "query_id": query_id,
        "intent": intent.model_dump(mode="json"),
        "results": results,
        "debug": debug,
    }


def _safe_classify(classifier, image_bytes: bytes) -> Classification:
    """沒字圖分類（小 VLM）；未注入或呼叫失敗時退回空 Classification，不讓任務崩潰。"""
    if classifier is None:
        return Classification()
    try:
        return classifier.classify(image_bytes, top_k=5)
    except Exception:  # noqa: BLE001 VLM 瞬斷/限流 → 退回無結果
        return Classification()


def _persist_textless_sample(conn, classification: Classification, client_id: str | None) -> None:
    """把沒字圖的 (影像 embedding, 標籤) 存成飛輪訓練集；best-effort、不擋回應。"""
    if classification.embedding is None and not classification.labels:
        return
    try:
        repo.insert_textless_sample(
            conn,
            embedding=classification.embedding,
            labels=classification.labels,
            model_version=classification.model_version,
            client_id=client_id,
        )
    except Exception:  # noqa: BLE001 訓練集寫入失敗不影響推薦
        pass


def _fast_intent(query: str, source: str, labels: list[str]) -> IntentResult:
    """快速模式的極簡意圖：單一策略（query 為 OCR 文字或 CLIP 標籤）；無 query 則無策略。"""
    strategies: list[StrategyPlan] = []
    if query:
        if source == "nvclip":
            name, rationale = "快速情緒", "NV-CLIP 圖片情緒／類別：" + "、".join(labels)
        else:
            name, rationale = "快速檢索", "OCR 文字直接語意檢索"
        strategies = [StrategyPlan(name=name, rationale=rationale, query=query)]
    return IntentResult(
        summary=(query or "（未解析到內容）")[:120],
        punchline="",
        other_party_emotion=[],
        conversation_type="快速模式",
        sensitive=False,
        low_context=not query,
        language="zh-TW",
        strategies=strategies,
    )


def run_fast_recommendation(
    conn,
    ocr,  # NvidiaOcr：影像 → 文字（PaddleOCR，非 LLM）
    classifier,  # ZeroShotClassifier：沒字圖 → 情緒/類別標籤（NV-CLIP）
    embedder: Embedder,
    request: RecommendRequest,
    *,
    image_bytes: bytes | None = None,
) -> dict[str, Any]:
    """快速模式：OCR（有字）或 NV-CLIP 零樣本（沒字）→ 向量檢索，全程無 VLM/LLM。

    回應形狀與 ``run_recommendation`` 完全一致（前端結果頁不需區分），差別在
    ``debug.fast`` 標示走 ocr / nvclip / text 哪條，且無 rerank（依向量排序）。
    """
    timings: dict[str, int] = {}
    t_start = time.perf_counter()

    ocr_text = ""
    labels: list[str] = []
    source = "text"
    if image_bytes:
        t0 = time.perf_counter()
        ocr_text = (ocr.ocr(image_bytes) or "").strip()
        timings["ocr"] = int((time.perf_counter() - t0) * 1000)
        if len(ocr_text) >= _FAST_MIN_OCR_CHARS:
            source, query = "ocr", ocr_text
        else:
            # 沒字圖 → 小 VLM 取情緒/類別關鍵詞當檢索 query；同時把 (影像 embedding,
            # 標籤) 存成飛輪訓練集。VLM 瞬斷時退回無標籤（→ 空結果），不讓任務崩潰。
            t0 = time.perf_counter()
            classification = _safe_classify(classifier, image_bytes)
            timings["classify"] = int((time.perf_counter() - t0) * 1000)
            labels = classification.labels
            source, query = "vlm", " ".join(labels)
            _persist_textless_sample(conn, classification, request.client_id)
    else:
        query = " ".join(t.text for t in request.conversation if t.text.strip())
    query = query.strip()

    conversation = [TurnIn(speaker="other", text=(ocr_text or query or "（圖片）"))]
    intent = _fast_intent(query, source, labels)

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

    ranked = _vector_fallback(retrieval.candidates, request.params.top_n, reason=FAST_REASON)

    return _assemble_and_log(
        conn,
        request,
        conversation,
        intent,
        retrieval,
        ranked,
        signature=signature,
        timings=timings,
        t_start=t_start,
        models_snapshot={"intent": f"fast-{source}", "rerank": "vector"},
        rerank_fallback=False,
        extra_debug={"fast": {"source": source, "ocr_text": ocr_text, "labels": labels}},
    )
