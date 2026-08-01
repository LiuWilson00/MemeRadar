"""調研 prompt 與 schema 的一致性，以及自動發布閘門。"""

from __future__ import annotations

from memeradar.blog.research import (
    MAX_OUTPUT_TOKENS,
    ResearchOrigin,
    ResearchResult,
    ResearchSource,
    build_system_prompt,
    should_auto_publish,
)


class TestPromptDescribesTheSchema:
    """prompt 必須逐一列出欄位名——call_structured 只做「解析+驗證」，不會把 pydantic
    schema 送給模型。漏講欄位，模型就會自己發明名字（2026-08-02 首次實跑：它回了
    meme_name / episode / scene_description，且沒有 title 與 article_html，驗證全滅）。
    """

    def test_every_top_level_field_is_named_in_the_prompt(self):
        prompt = build_system_prompt()
        missing = [f for f in ResearchResult.model_fields if f not in prompt]
        assert not missing, f"prompt 沒交代這些欄位：{missing}"

    def test_every_origin_subfield_is_named_in_the_prompt(self):
        prompt = build_system_prompt()
        missing = [f for f in ResearchOrigin.model_fields if f not in prompt]
        assert not missing, f"prompt 沒交代 origin 的子欄位：{missing}"

    def test_source_subfields_are_named(self):
        prompt = build_system_prompt()
        assert all(f in prompt for f in ResearchSource.model_fields)

    def test_output_budget_leaves_room_for_reasoning(self):
        """sonnet 開著 thinking，推理會吃掉輸出預算：實測 out=3572 之中正文只有 ~1200 字元。
        4000 太緊（首跑就被截斷），這裡釘一個下限免得日後有人調小。"""
        assert MAX_OUTPUT_TOKENS >= 8000


def _result(**kw):
    base = dict(
        verdict="identified", confidence=0.9,
        origin=ResearchOrigin(work="九品芝麻官", year="1994", scene="公堂",
                              characters="方唐鏡", region="香港"),
        caption_is_original=True, caption_note="原台詞",
        sources=[ResearchSource(title="t", url="https://e.com", supports="出處")],
        unverified_claims=[], title="標題", article_html="<p>內文</p>")
    base.update(kw)
    return ResearchResult(**base)


class TestAutoPublishGate:
    def test_identified_with_sources_publishes(self):
        assert should_auto_publish(_result()) is True

    def test_low_confidence_blocked(self):
        assert should_auto_publish(_result(confidence=0.5)) is False

    def test_partial_blocked(self):
        assert should_auto_publish(_result(verdict="partial")) is False

    def test_no_sources_blocked(self):
        """說查到了卻給不出來源＝沒有憑據。"""
        assert should_auto_publish(_result(sources=[])) is False
