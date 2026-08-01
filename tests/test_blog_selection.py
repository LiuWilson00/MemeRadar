"""每日一梗選圖：熱度 × 題材新鮮度 × 可考據性 × 當日擾動。"""

from __future__ import annotations

from memeradar.blog.selection import MemeCandidate, pick_for_day, score_candidates


def _c(mid, hotness=0.0, franchise=None, category=None, template=False):
    return MemeCandidate(meme_id=mid, hotness=hotness, franchise=franchise,
                         category=category, has_template=template)


class TestDeterminism:
    """同一天要選到同一張——預覽、重試、排程重啟都不該換人。"""

    def test_same_day_same_pick(self):
        cands = [_c(f"m{i}", hotness=i % 3) for i in range(30)]
        first = pick_for_day(cands, day="2026-08-02")
        again = pick_for_day(list(reversed(cands)), day="2026-08-02")
        assert first.candidate.meme_id == again.candidate.meme_id

    def test_different_days_shuffle(self):
        """不換日期就永遠同一張＝專欄會卡住；擾動要真的讓不同天選到不同圖。"""
        cands = [_c(f"m{i}") for i in range(60)]  # 其餘因子全相同 → 只剩擾動決定
        picks = {pick_for_day(cands, day=f"2026-08-{d:02d}").candidate.meme_id
                 for d in range(1, 15)}
        assert len(picks) > 1

    def test_ties_broken_by_id_not_input_order(self):
        cands = [_c("mb"), _c("ma")]
        assert [s.candidate.meme_id for s in score_candidates(cands, day="2026-08-02")] \
            == sorted(s.candidate.meme_id for s in score_candidates(cands, day="2026-08-02"))


class TestPriorities:
    def test_hotter_meme_wins_all_else_equal(self):
        cands = [_c("m_cold", hotness=0.0), _c("m_hot", hotness=100.0)]
        # 擾動最多 0.10，熱度差距 0.35 → 熱度必定壓過
        assert pick_for_day(cands, day="2026-08-02").candidate.meme_id == "m_hot"

    def test_researchable_meme_preferred(self):
        """有出處/模板的圖，調研查得到東西的機率高很多。"""
        cands = [_c("m_plain"), _c("m_sourced", franchise="海綿寶寶")]
        assert pick_for_day(cands, day="2026-08-02").candidate.meme_id == "m_sourced"

    def test_recent_franchise_is_penalised(self):
        """連五天寫海綿寶寶是這個專欄最容易犯的錯。"""
        recent = [_c("old", franchise="海綿寶寶")]
        cands = [_c("m_same", franchise="海綿寶寶"), _c("m_other", franchise="哈利波特")]
        assert pick_for_day(cands, day="2026-08-02", recent=recent) \
            .candidate.meme_id == "m_other"

    def test_category_clash_penalised_less_than_franchise(self):
        recent = [_c("old", franchise="海綿寶寶", category="卡通動畫")]
        same_franchise = score_candidates(
            [_c("x", franchise="海綿寶寶", category="卡通動畫")],
            day="2026-08-02", recent=recent)[0]
        same_category = score_candidates(
            [_c("x", franchise="哈利波特", category="卡通動畫")],
            day="2026-08-02", recent=recent)[0]
        assert same_category.breakdown["novelty"] > same_franchise.breakdown["novelty"]

    def test_novelty_never_goes_negative(self):
        recent = [_c(f"old{i}", franchise="海綿寶寶") for i in range(9)]
        s = score_candidates([_c("x", franchise="海綿寶寶")], day="2026-08-02", recent=recent)[0]
        assert s.breakdown["novelty"] == 0.0


class TestEmpty:
    def test_no_candidates_returns_none(self):
        assert pick_for_day([], day="2026-08-02") is None
