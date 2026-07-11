"""P2-2 測試：多路檢索 + 合併（規格：docs/04 §2.3、§3）。"""

import pytest

from memeradar.matching.intent import StrategyPlan
from memeradar.matching.retrieval import RetrievalParams, retrieve_candidates
from memeradar.matching.search import SearchFilters, SqliteBruteForceSearcher
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Embedding, Meme, MemeAnnotation, new_id

SIGNATURE = "fake-embed@v1|doc-v1"


class RoutedFakeEmbedder:
    """依 query 文字回固定向量，模擬「不同策略 query 指向語意空間不同方向」。"""

    model_id = "fake-embed@v1"

    def __init__(self, routes: dict[str, list[float]]):
        self.routes = routes
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.routes[t] for t in texts]


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


def seed(conn, vector, *, franchise="海綿寶寶") -> Meme:
    meme = Meme(meme_id=new_id("m"), image_uri="x.png", sha256=new_id("h").ljust(64, "0")[:64])
    repo.insert_meme(conn, meme)
    repo.upsert_annotation(
        conn,
        MemeAnnotation(
            meme_id=meme.meme_id,
            model_version="labeler-v1@claude-sonnet-5",
            ocr_text="x",
            description="測試",
            franchise=franchise,
            emotions=["無奈"],
            usage_hints=["測試"],
            categories=["卡通動畫"],
            confidence=0.9,
        ),
    )
    repo.add_embedding(
        conn,
        Embedding(meme_id=meme.meme_id, kind="text_retrieval", model=SIGNATURE, vector=vector),
    )
    return meme


def strategy(name: str, query: str) -> StrategyPlan:
    return StrategyPlan(name=name, rationale="測試", query=query)


TWO_STRATEGIES = [
    strategy("滑跪求饒", "犯錯道歉求饒"),
    strategy("自嘲", "承認自己爛 自嘲"),
]
ROUTES = {"犯錯道歉求饒": [1.0, 0.0], "承認自己爛 自嘲": [0.0, 1.0]}


class TestMultiPathRetrieval:
    def test_merges_across_strategies_with_max_score(self, conn):
        only_a = seed(conn, [1.0, 0.0])          # 只被策略 A 命中（sim 1.0）
        only_b = seed(conn, [0.0, 1.0])          # 只被策略 B 命中（sim 1.0）
        both = seed(conn, [0.8, 0.6])            # A: 0.8、B: 0.6 → 取 0.8

        embedder = RoutedFakeEmbedder(ROUTES)
        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        # 門檻 0.1：排除正交（相似度 0）的組合，讓「只被單一策略命中」成立
        result = retrieve_candidates(
            searcher, embedder, TWO_STRATEGIES,
            filters=SearchFilters(), params=RetrievalParams(min_similarity=0.1),
        )

        by_id = {c.meme_id: c for c in result.candidates}
        assert set(by_id) == {only_a.meme_id, only_b.meme_id, both.meme_id}

        merged = by_id[both.meme_id]
        assert merged.similarity == pytest.approx(0.8)  # 跨策略取最高
        assert merged.matched_strategies == ("滑跪求饒", "自嘲")  # 依各自分數排序
        assert merged.per_strategy_similarity["滑跪求饒"] == pytest.approx(0.8)
        assert merged.per_strategy_similarity["自嘲"] == pytest.approx(0.6)

        assert by_id[only_a.meme_id].matched_strategies == ("滑跪求饒",)

    def test_candidates_sorted_by_similarity_desc(self, conn):
        seed(conn, [0.6, 0.8])
        seed(conn, [1.0, 0.0])
        seed(conn, [0.9, 0.4358899])

        embedder = RoutedFakeEmbedder(ROUTES)
        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        result = retrieve_candidates(
            searcher, embedder, TWO_STRATEGIES,
            filters=SearchFilters(), params=RetrievalParams(min_similarity=0.0),
        )
        sims = [c.similarity for c in result.candidates]
        assert sims == sorted(sims, reverse=True)

    def test_queries_embedded_in_single_batch(self, conn):
        seed(conn, [1.0, 0.0])
        embedder = RoutedFakeEmbedder(ROUTES)
        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)

        retrieve_candidates(
            searcher, embedder, TWO_STRATEGIES,
            filters=SearchFilters(), params=RetrievalParams(),
        )
        assert len(embedder.calls) == 1  # 所有策略 query 一次批次 embed
        assert embedder.calls[0] == ["犯錯道歉求饒", "承認自己爛 自嘲"]

    def test_min_similarity_and_candidate_k(self, conn):
        seed(conn, [0.0, -1.0])  # 對兩策略皆為負相似度 → 被門檻擋
        for _ in range(3):
            seed(conn, [1.0, 0.0])

        embedder = RoutedFakeEmbedder(ROUTES)
        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        result = retrieve_candidates(
            searcher, embedder, TWO_STRATEGIES,
            filters=SearchFilters(),
            params=RetrievalParams(candidate_k=2, min_similarity=0.35),
        )
        # 策略 A 只取 Top-2（candidate_k），負相似度者不入池
        assert len(result.candidates) == 2

    def test_filters_plumbed_through(self, conn):
        sponge = seed(conn, [1.0, 0.0], franchise="海綿寶寶")
        seed(conn, [1.0, 0.0], franchise="甄嬛傳")

        embedder = RoutedFakeEmbedder(ROUTES)
        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        result = retrieve_candidates(
            searcher, embedder, TWO_STRATEGIES,
            filters=SearchFilters(franchises=("海綿寶寶",)),
            params=RetrievalParams(min_similarity=0.0),
        )
        assert {c.meme_id for c in result.candidates} == {sponge.meme_id}

    def test_per_strategy_hits_reported(self, conn):
        seed(conn, [1.0, 0.0])   # 只 A
        seed(conn, [0.8, 0.6])   # A、B 都命中

        embedder = RoutedFakeEmbedder(ROUTES)
        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        result = retrieve_candidates(
            searcher, embedder, TWO_STRATEGIES,
            filters=SearchFilters(), params=RetrievalParams(min_similarity=0.35),
        )
        assert result.per_strategy_hits == {"滑跪求饒": 2, "自嘲": 1}

    def test_empty_strategies_returns_empty(self, conn):
        seed(conn, [1.0, 0.0])
        embedder = RoutedFakeEmbedder({})
        searcher = SqliteBruteForceSearcher(conn, signature=SIGNATURE)
        result = retrieve_candidates(
            searcher, embedder, [], filters=SearchFilters(), params=RetrievalParams()
        )
        assert result.candidates == []
        assert result.per_strategy_hits == {}
        assert embedder.calls == []  # 不浪費 embed 呼叫
