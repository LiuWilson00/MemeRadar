"""P2-3 測試：LLM listwise rerank + 熱度微調 + MMR + 同模板限量。

純單元測試（stub client、候選直接建構，不碰 DB）。
"""

import pytest

from memeradar.matching.intent import IntentResult
from memeradar.matching.rerank import (
    DEFAULT_RERANK_MODEL,
    CandidateScore,
    RankingParams,
    RerankRefusedError,
    RerankResult,
    build_system_prompt,
    rank_candidates,
)
from memeradar.matching.retrieval import Candidate
from memeradar.shared.models import MemeAnnotation

INTENT = IntentResult(
    summary="同事第三次指責使用者報告遲交",
    punchline="每次都這樣，你到底行不行",
    other_party_emotion=["憤怒"],
    conversation_type="指責",
    sensitive=False,
    low_context=False,
    language="zh-TW",
    strategies=[
        {"name": "滑跪求饒", "rationale": "對方在氣頭上", "query": "犯錯道歉求饒"},
    ],
)


def cand(
    meme_id: str,
    sim: float,
    *,
    template: str | None = None,
    hotness: float = 0.0,
    ocr: str = "我就爛",
    hints: tuple[str, ...] = ("被指責時自嘲",),
) -> Candidate:
    return Candidate(
        meme_id=meme_id,
        similarity=sim,
        annotation=MemeAnnotation(
            meme_id=meme_id,
            model_version="labeler-v1@claude-sonnet-5",
            ocr_text=ocr,
            description="測試畫面",
            emotions=["擺爛"],
            usage_hints=list(hints),
            categories=["卡通動畫"],
            template_name=template,
        ),
        matched_strategies=("滑跪求饒",),
        per_strategy_similarity={"滑跪求饒": sim},
        hotness=hotness,
    )


class StubResponse:
    def __init__(self, parsed_output, stop_reason="end_turn"):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason


class StubClient:
    """依候選在 prompt 中的編號（1-based）回固定分數。"""

    def __init__(self, scores: list[tuple[int, int, str]] | None, stop_reason="end_turn"):
        self.calls: list[dict] = []
        parsed = (
            RerankResult(
                scores=[
                    CandidateScore(candidate_id=cid, score=s, reason=r) for cid, s, r in scores
                ]
            )
            if scores is not None
            else None
        )
        self.response = StubResponse(parsed, stop_reason)
        outer = self

        class _Messages:
            def parse(self, **kwargs):
                outer.calls.append(kwargs)
                return outer.response

        self.messages = _Messages()


class TestRankHappyPath:
    def test_orders_by_rerank_score_with_reasons(self):
        candidates = [cand("m_a", 0.9), cand("m_b", 0.8), cand("m_c", 0.7)]
        # 模型把向量第二名評為最高
        client = StubClient([(2, 95, "情境最貼"), (1, 70, "尚可"), (3, 40, "不太相關")])

        ranked = rank_candidates(
            client, INTENT, candidates, params=RankingParams(top_n=3, diversity=0.0)
        )

        assert [r.meme_id for r in ranked] == ["m_b", "m_a", "m_c"]
        assert [r.rank for r in ranked] == [1, 2, 3]
        assert ranked[0].reason == "情境最貼"
        assert ranked[0].matched_strategy == "滑跪求饒"
        assert set(ranked[0].scores) == {"vector", "rerank", "final"}
        assert ranked[0].scores["vector"] == pytest.approx(0.8)
        assert ranked[0].scores["rerank"] == pytest.approx(0.95)

        call = client.calls[0]
        assert call["model"] == DEFAULT_RERANK_MODEL
        assert call["thinking"] == {"type": "disabled"}  # 線上延遲敏感路徑
        user_text = call["messages"][0]["content"]
        assert "每次都這樣" in user_text  # punchline 進 prompt
        assert "被指責時自嘲" in user_text  # 候選以用途摘要呈現

    def test_top_n_truncation(self):
        candidates = [cand(f"m_{i}", 0.9 - i * 0.1) for i in range(5)]
        client = StubClient([(i + 1, 90 - i * 10, "r") for i in range(5)])
        ranked = rank_candidates(
            client, INTENT, candidates, params=RankingParams(top_n=3, diversity=0.0)
        )
        assert len(ranked) == 3


class TestHotnessAdjustment:
    def test_hot_meme_wins_tie_and_final_reflects_boost(self):
        candidates = [cand("m_cold", 0.9, hotness=0.0), cand("m_hot", 0.9, hotness=10.0)]
        client = StubClient([(1, 80, "r"), (2, 80, "r")])

        ranked = rank_candidates(
            client,
            INTENT,
            candidates,
            params=RankingParams(top_n=2, diversity=0.0, hotness_weight=0.1),
        )

        assert ranked[0].meme_id == "m_hot"
        assert ranked[0].scores["final"] == pytest.approx(0.8 * 1.1)
        assert ranked[1].scores["final"] == pytest.approx(0.8)


class TestDiversity:
    def test_same_template_capped_at_one(self):
        candidates = [
            cand("m_a", 0.9, template="派大星攤手"),
            cand("m_b", 0.85, template="派大星攤手"),
            cand("m_c", 0.6, template="臣妾做不到"),
        ]
        client = StubClient([(1, 90, "r"), (2, 85, "r"), (3, 60, "r")])

        ranked = rank_candidates(
            client, INTENT, candidates, params=RankingParams(top_n=2, diversity=0.0)
        )

        assert [r.meme_id for r in ranked] == ["m_a", "m_c"]  # 同模板第二張被跳過

    def test_null_templates_do_not_collide(self):
        candidates = [cand("m_a", 0.9), cand("m_b", 0.85)]  # template 皆為 None
        client = StubClient([(1, 90, "r"), (2, 85, "r")])
        ranked = rank_candidates(
            client, INTENT, candidates, params=RankingParams(top_n=2, diversity=0.0)
        )
        assert len(ranked) == 2

    def test_mmr_defers_near_duplicates_when_diversity_high(self):
        candidates = [cand("m_a1", 0.9), cand("m_a2", 0.85), cand("m_b", 0.6)]
        vectors = {"m_a1": [1.0, 0.0], "m_a2": [1.0, 0.0], "m_b": [0.0, 1.0]}
        scores = [(1, 90, "r"), (2, 85, "r"), (3, 60, "r")]

        low_d = rank_candidates(
            StubClient(scores), INTENT, candidates,
            params=RankingParams(top_n=3, diversity=0.0), vectors_by_id=vectors,
        )
        assert [r.meme_id for r in low_d] == ["m_a1", "m_a2", "m_b"]

        high_d = rank_candidates(
            StubClient(scores), INTENT, candidates,
            params=RankingParams(top_n=3, diversity=0.8), vectors_by_id=vectors,
        )
        assert [r.meme_id for r in high_d] == ["m_a1", "m_b", "m_a2"]


class TestRobustness:
    def test_rerank_pool_caps_candidates_sent_to_model(self):
        candidates = [cand(f"m_{i}", 0.9 - i * 0.1, ocr=f"獨特文字{i}") for i in range(3)]
        client = StubClient([(1, 90, "r"), (2, 80, "r")])

        ranked = rank_candidates(
            client, INTENT, candidates,
            params=RankingParams(top_n=5, diversity=0.0, rerank_pool=2),
        )

        user_text = client.calls[0]["messages"][0]["content"]
        assert "獨特文字0" in user_text and "獨特文字1" in user_text
        assert "獨特文字2" not in user_text  # 池外候選不進 prompt
        assert {r.meme_id for r in ranked} <= {"m_0", "m_1"}

    def test_candidate_omitted_by_model_is_excluded(self):
        candidates = [cand("m_a", 0.9), cand("m_b", 0.8)]
        client = StubClient([(1, 90, "r")])  # 模型漏掉候選 2
        ranked = rank_candidates(
            client, INTENT, candidates, params=RankingParams(top_n=5, diversity=0.0)
        )
        assert [r.meme_id for r in ranked] == ["m_a"]

    def test_duplicate_candidate_id_first_wins(self):
        candidates = [cand("m_a", 0.9)]
        client = StubClient([(1, 90, "第一筆"), (1, 10, "重複")])
        ranked = rank_candidates(
            client, INTENT, candidates, params=RankingParams(top_n=5, diversity=0.0)
        )
        assert ranked[0].scores["rerank"] == pytest.approx(0.9)
        assert ranked[0].reason == "第一筆"

    def test_empty_reason_gets_strategy_fallback(self):
        # 低分候選允許省略理由（省輸出 token）；若仍進 Top-N 則以策略名合成
        candidates = [cand("m_a", 0.9)]
        client = StubClient([(1, 30, "")])
        ranked = rank_candidates(
            client, INTENT, candidates, params=RankingParams(top_n=5, diversity=0.0)
        )
        assert ranked[0].reason == "符合「滑跪求饒」策略"

    def test_unknown_candidate_id_ignored(self):
        candidates = [cand("m_a", 0.9)]
        client = StubClient([(1, 90, "r"), (99, 80, "幻覺編號")])
        ranked = rank_candidates(
            client, INTENT, candidates, params=RankingParams(top_n=5, diversity=0.0)
        )
        assert len(ranked) == 1

    def test_refusal_raises(self):
        client = StubClient(None, stop_reason="refusal")
        with pytest.raises(RerankRefusedError):
            rank_candidates(
                client, INTENT, [cand("m_a", 0.9)], params=RankingParams(diversity=0.0)
            )

    def test_empty_candidates_short_circuit(self):
        client = StubClient([(1, 90, "r")])
        assert rank_candidates(client, INTENT, [], params=RankingParams()) == []
        assert client.calls == []  # 不浪費 LLM 呼叫


class TestPrompt:
    def test_system_prompt_deterministic(self):
        assert build_system_prompt() == build_system_prompt()
