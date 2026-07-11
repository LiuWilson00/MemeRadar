"""P2-2 多路檢索 + 合併（docs/04 §2.3）。

對意圖分析展開的每個回應策略各自檢索，再合併去重：

1. 所有策略的 query **單批** embed（一次呼叫，是延遲與成本的主要節省點；
   檢索本身為行程內運算，逐策略執行即已在延遲預算內——「平行」的收益
   在批次 embed，不在執行緒）。
2. 每策略：向量 Top-K（``candidate_k``）+ metadata 過濾 + 相似度門檻。
3. 合併：同一張圖被多個策略命中時保留**最高分**，並記錄所有命中策略與
   各策略分數（Console debug 面板與 rerank 的素材）。
4. 依最高分排序後回傳完整候選池——截斷到 top_n 是 P2-3 rerank 之後的事。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from memeradar.matching.intent import StrategyPlan
from memeradar.matching.search import SearchFilters, VectorSearcher
from memeradar.shared.models import MemeAnnotation
from memeradar.understanding.embedding import Embedder

DEFAULT_CANDIDATE_K = 50
DEFAULT_MIN_SIMILARITY = 0.35


@dataclass(frozen=True)
class RetrievalParams:
    """檢索參數（docs/04 §3，Console 參數面板對應）。"""

    candidate_k: int = DEFAULT_CANDIDATE_K
    min_similarity: float = DEFAULT_MIN_SIMILARITY


@dataclass(frozen=True)
class Candidate:
    meme_id: str
    similarity: float  # 跨策略最高分
    annotation: MemeAnnotation
    matched_strategies: tuple[str, ...]  # 命中策略名，依各自分數排序
    per_strategy_similarity: dict[str, float]
    hotness: float = 0.0


@dataclass(frozen=True)
class RetrievalResult:
    candidates: list[Candidate]
    per_strategy_hits: dict[str, int] = field(default_factory=dict)  # 合併前各策略回收數


def retrieve_candidates(
    searcher: VectorSearcher,
    embedder: Embedder,
    strategies: list[StrategyPlan],
    *,
    filters: SearchFilters,
    params: RetrievalParams | None = None,
) -> RetrievalResult:
    params = params if params is not None else RetrievalParams()
    if not strategies:
        return RetrievalResult(candidates=[])

    query_vectors = embedder.embed([s.query for s in strategies])

    per_strategy_hits: dict[str, int] = {}
    # meme_id → (annotation, hotness, {策略名: 分數})
    pool: dict[str, tuple[MemeAnnotation, float, dict[str, float]]] = {}

    for plan, vector in zip(strategies, query_vectors, strict=True):
        name = plan.name.value
        hits = searcher.search(
            vector, k=params.candidate_k, filters=filters, min_similarity=params.min_similarity
        )
        per_strategy_hits[name] = len(hits)
        for hit in hits:
            _, _, scores = pool.setdefault(hit.meme_id, (hit.annotation, hit.hotness, {}))
            # 同策略理論上不會重複命中同圖；保險起見取最高
            scores[name] = max(scores.get(name, float("-inf")), hit.similarity)

    candidates = [
        Candidate(
            meme_id=meme_id,
            similarity=max(scores.values()),
            annotation=annotation,
            matched_strategies=tuple(
                sorted(scores, key=lambda strategy_name: -scores[strategy_name])
            ),
            per_strategy_similarity=dict(scores),
            hotness=hotness,
        )
        for meme_id, (annotation, hotness, scores) in pool.items()
    ]
    candidates.sort(key=lambda c: (-c.similarity, c.meme_id))
    return RetrievalResult(candidates=candidates, per_strategy_hits=per_strategy_hits)
