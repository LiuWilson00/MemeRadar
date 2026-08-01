"""每日一梗的選圖：從圖庫挑出「今天該介紹哪一張」。

目標不是挑最熱門的那張——那樣專欄第一週就把好料用完，之後每天都在吃剩菜。
排序分數由三項合成：

1. **熱度**（hotness_norm，權重 ``HOTNESS_WEIGHT``）：有人按讚的圖比較可能有故事。
2. **題材新鮮度**（權重 ``NOVELTY_WEIGHT``）：最近幾篇寫過的 franchise / category 要降權，
   免得連五天都在寫海綿寶寶。franchise 相同罰得比 category 重——同作品的重複感最明顯。
3. **可考據性**（權重 ``RESEARCHABLE_WEIGHT``）：有 franchise 或 template_name 的圖，
   調研查得到東西的機率高很多。PoC 實測：查不到出處的圖，模型只能寫「怎麼用」，
   那種文章撐不起一個考據專欄。

加上一個由日期決定的擾動（``_jitter``），讓同一天永遠選到同一張（可重跑、可預覽），
不同天則洗牌，不會永遠是同一批高分圖霸榜。

已經寫過的圖（blog_posts 有紀錄，含 rejected）一律排除——呼叫端負責傳 ``exclude_ids``。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

HOTNESS_WEIGHT = 0.35
NOVELTY_WEIGHT = 0.35
RESEARCHABLE_WEIGHT = 0.20
JITTER_WEIGHT = 0.10

#: 往回看幾篇來判斷題材是否撞題
RECENT_WINDOW = 10
_SAME_FRANCHISE_PENALTY = 1.0
_SAME_CATEGORY_PENALTY = 0.5


@dataclass(frozen=True)
class MemeCandidate:
    """選圖需要的最小資訊（避免把整個 annotation 拖進來）。"""

    meme_id: str
    hotness: float
    franchise: str | None
    category: str | None
    has_template: bool


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: MemeCandidate
    score: float
    breakdown: dict[str, float]


def _jitter(meme_id: str, day: str) -> float:
    """由 (圖, 日期) 決定的穩定亂數 0~1。

    用雜湊而非 random：同一天重跑要選到同一張（預覽、重試、排程重啟都不該換人），
    但換一天就整個洗牌。random.seed 做得到，卻會污染全域亂數狀態。
    """
    digest = hashlib.sha256(f"{day}:{meme_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _novelty(candidate: MemeCandidate, recent: list[MemeCandidate]) -> float:
    """1.0 = 完全沒撞題；扣到 0 為止。"""
    penalty = 0.0
    for other in recent:
        if candidate.franchise and candidate.franchise == other.franchise:
            penalty += _SAME_FRANCHISE_PENALTY
        elif candidate.category and candidate.category == other.category:
            penalty += _SAME_CATEGORY_PENALTY
    return max(0.0, 1.0 - penalty)


def score_candidates(
    candidates: list[MemeCandidate],
    *,
    day: str,
    recent: list[MemeCandidate] | None = None,
) -> list[ScoredCandidate]:
    """依「熱度 × 題材新鮮度 × 可考據性 × 當日擾動」排序，高分在前。"""
    recent = recent or []
    if not candidates:
        return []
    max_hotness = max((c.hotness for c in candidates), default=0.0)
    scored: list[ScoredCandidate] = []
    for c in candidates:
        hotness = c.hotness / max_hotness if max_hotness > 0 else 0.0
        novelty = _novelty(c, recent)
        researchable = 1.0 if (c.franchise or c.has_template) else 0.0
        jitter = _jitter(c.meme_id, day)
        breakdown = {
            "hotness": hotness,
            "novelty": novelty,
            "researchable": researchable,
            "jitter": jitter,
        }
        score = (
            HOTNESS_WEIGHT * hotness
            + NOVELTY_WEIGHT * novelty
            + RESEARCHABLE_WEIGHT * researchable
            + JITTER_WEIGHT * jitter
        )
        scored.append(ScoredCandidate(candidate=c, score=score, breakdown=breakdown))
    # 同分時以 meme_id 決勝，確保結果完全確定（否則同分順序依輸入而定，不可重現）
    scored.sort(key=lambda s: (-s.score, s.candidate.meme_id))
    return scored


def pick_for_day(
    candidates: list[MemeCandidate],
    *,
    day: str,
    recent: list[MemeCandidate] | None = None,
) -> ScoredCandidate | None:
    """選出當日該介紹的那一張；沒有候選回 None。"""
    scored = score_candidates(candidates, day=day, recent=recent)
    return scored[0] if scored else None
