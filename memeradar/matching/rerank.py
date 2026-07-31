"""P2-3 重排序與多樣化（docs/04 §2.4）。

管線：候選池（P2-2，已依向量分排序）
  → 截前 ``rerank_pool``（20–30）張
  → **LLM listwise rerank**：候選以標註摘要編號呈現，模型對每張打 0–100 分
    並產出一句推薦理由（Q3 決策：LLM rerank 一次呼叫同時得到分數與理由）
  → 熱度微調：``final = rerank_norm × (1 + α × hotness_norm)``（α 預設 0.1，
    熱門梗略加分但不壓過相關性）
  → MMR 貪婪選取：``mmr = (1−d)·final − d·max_sim(候選, 已選)``（d = diversity，
    0=純相關性、1=最大多樣性；pairwise 相似度用檢索向量餘弦）
  → 同模板（template_name）硬規則限 1 張，與 d 無關
  → 截 Top-N（3–5）輸出 RankedMeme（分數拆解 vector / rerank / final）。

穩健性：模型漏評的候選視為淘汰；重複編號取第一筆；幻覺編號忽略；
模型拒答拋 ``RerankRefusedError``（上層可退回純向量排序）。
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from memeradar.matching.intent import IntentResult
from memeradar.matching.retrieval import Candidate
from memeradar.matching.search import _cosine
from memeradar.shared.models import MemeAnnotation

# rerank 用哪個模型不在這裡決定：DB settings 表的分任務覆寫（repo.get_task_models）
# 優先，否則用 config.vlm_model。這裡曾有 RERANK_PROMPT_VERSION 與
# DEFAULT_RERANK_MODEL = "claude-haiku-4-5"，但兩者從來沒被任何地方引用過，只是讓人
# 誤以為 rerank 跑在 haiku 上（實際是 qwen flash）。2026-08-01 移除。
MAX_OUTPUT_TOKENS = 2000
_DESCRIPTION_SNIPPET = 60  # 候選摘要中畫面描述的截斷長度


class RerankRefusedError(RuntimeError):
    """模型拒絕重排序時拋出；上層可退回純向量排序。"""


@dataclass(frozen=True)
class RankingParams:
    """排序參數（docs/04 §3，Console 參數面板對應）。"""

    top_n: int = 5
    diversity: float = 0.5  # MMR d：0=純相關性、1=最大多樣性
    hotness_weight: float = 0.1  # α
    rerank_pool: int = 25  # 送進 LLM 的候選上限（延遲與成本旋鈕）


class CandidateScore(BaseModel):
    candidate_id: int = Field(description="候選編號（與輸入清單一致）")
    score: int = Field(description="0–100：作為回覆這段對話的適配度")
    reason: str = Field(description="一句推薦理由（25 字內）；分數 <60 者給空字串")


class RerankResult(BaseModel):
    scores: list[CandidateScore] = Field(description="每個候選編號各一筆，不可遺漏")


@dataclass(frozen=True)
class RankedMeme:
    meme_id: str
    rank: int
    annotation: MemeAnnotation
    matched_strategy: str  # 最高分命中策略
    matched_tags: tuple[str, ...]  # 命中標籤（v1 = 情緒標籤）
    reason: str
    scores: dict[str, float]  # {"vector", "rerank", "final"}


def build_system_prompt() -> str:
    return """你是梗圖回覆的品味裁判。給你一段對話的情境分析與一批候選梗圖（以標註摘要呈現），為每張圖打 0–100 分：這張圖作為「我」回覆這段對話的下一則訊息，效果有多好。

score 的評分級距（這是打分尺規，**不是** reason 的範本）：
- 70+：圖的使用情境與對話的情境／所選回應策略高度吻合，丟出來會準確又好笑。
- 40–69：方向對但不夠貼，或姿態與情境略有落差。
- <40：無關、勉強、或在此情境丟出來會失禮尷尬。無關的圖務必給低分，不要客氣。

reason 怎麼寫：一句話（25 字內）、繁體中文、口語但精準，**會直接顯示給使用者看**。
必須講到**這張圖本身**的內容（圖中文字、角色、動作、梗），讓人一看就知道你在說哪張。
⚠️ 嚴禁複述上面的評分級距文字（例如「使用情境高度吻合」「方向對但不夠貼」這類），
那是尺規不是理由，抄過來對使用者毫無意義。
例：好的 reason →「他攤手說『我就爛』，正好接你這句自嘲」；壞的 reason →「與情境高度吻合」。
**分數低於 60 的候選 reason 一律給空字串**（反正不會被推薦，省輸出）。

必須為每個候選編號各給一筆評分，不可遺漏、不可虛構編號。

只輸出一個 JSON 物件，不要多餘文字或圍欄。格式：{"scores": [{"candidate_id": 整數編號, "score": 0~100 整數, "reason": 字串}, ...]}。"""


def _candidate_digest(index: int, candidate: Candidate) -> str:
    ann = candidate.annotation
    description = ann.description[:_DESCRIPTION_SNIPPET]
    lines = [
        f"候選 {index}：",
        f"  用途：{' / '.join(ann.usage_hints)}",
        f"  情緒：{'、'.join(ann.emotions)}",
        f"  圖中文字：{ann.ocr_text or '（無）'}",
        f"  畫面：{description}",
        f"  出處：{ann.franchise or '—'}；命中策略：{'、'.join(candidate.matched_strategies)}",
    ]
    return "\n".join(lines)


def build_user_content(intent: IntentResult, candidates: list[Candidate]) -> str:
    strategy_lines = "\n".join(
        f"- {s.name}：{s.rationale}" for s in intent.strategies
    )
    digests = "\n".join(
        _candidate_digest(i, c) for i, c in enumerate(candidates, start=1)
    )
    return (
        f"對話情境：{intent.summary}\n"
        f"關鍵爆點句：「{intent.punchline}」\n"
        f"可行回應策略：\n{strategy_lines}\n\n"
        f"候選梗圖（共 {len(candidates)} 張）：\n{digests}"
    )


def _mmr_select(
    scored: list[tuple[Candidate, float, float]],  # (候選, rerank_norm, final)
    *,
    top_n: int,
    diversity: float,
    vectors_by_id: dict[str, list[float]] | None,
) -> list[tuple[Candidate, float, float]]:
    """貪婪 MMR 選取 + 同模板限 1 張硬規則。"""
    remaining = list(scored)
    selected: list[tuple[Candidate, float, float]] = []
    used_templates: set[str] = set()

    def pairwise(a: Candidate, b: Candidate) -> float:
        if not vectors_by_id:
            return 0.0
        va, vb = vectors_by_id.get(a.meme_id), vectors_by_id.get(b.meme_id)
        if va is None or vb is None:
            return 0.0
        return _cosine(va, vb)

    while remaining and len(selected) < top_n:
        best = None
        best_score = float("-inf")
        for item in remaining:
            candidate, _, final = item
            template = candidate.annotation.template_name
            if template is not None and template in used_templates:
                continue  # 同模板硬規則
            max_sim = max((pairwise(candidate, s[0]) for s in selected), default=0.0)
            mmr = (1.0 - diversity) * final - diversity * max_sim
            if mmr > best_score:
                best_score = mmr
                best = item
        if best is None:
            break  # 剩餘候選全被模板規則擋下
        selected.append(best)
        remaining.remove(best)
        template = best[0].annotation.template_name
        if template is not None:
            used_templates.add(template)
    return selected


def rank_candidates(
    vlm,
    intent: IntentResult,
    candidates: list[Candidate],
    *,
    params: RankingParams | None = None,
    vectors_by_id: dict[str, list[float]] | None = None,
    model: str | None = None,
    log=None,
) -> list[RankedMeme]:
    from memeradar.understanding.nvidia_vlm import call_structured

    params = params if params is not None else RankingParams()
    if not candidates:
        return []

    pool = candidates[: params.rerank_pool]

    result = call_structured(
        vlm, RerankResult, build_system_prompt(), build_user_content(intent, pool),
        task="rerank", model=model, log=log, max_tokens=MAX_OUTPUT_TOKENS,
    )
    if result is None:
        raise RerankRefusedError("模型無法重排序")

    # 編號 → 分數與理由（重複取第一筆；幻覺編號忽略；漏評者淘汰）
    by_id: dict[int, CandidateScore] = {}
    for item in result.scores:
        if 1 <= item.candidate_id <= len(pool):
            by_id.setdefault(item.candidate_id, item)

    max_hotness = max((c.hotness for c in pool), default=0.0)
    scored: list[tuple[Candidate, float, float, str]] = []
    for index, candidate in enumerate(pool, start=1):
        judged = by_id.get(index)
        if judged is None:
            continue
        rerank_norm = judged.score / 100.0
        hotness_norm = candidate.hotness / max_hotness if max_hotness > 0 else 0.0
        final = rerank_norm * (1.0 + params.hotness_weight * hotness_norm)
        scored.append((candidate, rerank_norm, final, judged.reason))

    scored.sort(key=lambda item: (-item[2], item[0].meme_id))
    selected = _mmr_select(
        [(c, r, f) for c, r, f, _ in scored],
        top_n=params.top_n,
        diversity=params.diversity,
        vectors_by_id=vectors_by_id,
    )
    reason_by_id = {c.meme_id: reason for c, _, _, reason in scored}

    return [
        RankedMeme(
            meme_id=candidate.meme_id,
            rank=rank,
            annotation=candidate.annotation,
            matched_strategy=candidate.matched_strategies[0],
            matched_tags=tuple(candidate.annotation.emotions),
            reason=reason_by_id[candidate.meme_id]
            or f"符合「{candidate.matched_strategies[0]}」策略",
            scores={
                "vector": candidate.similarity,
                "rerank": rerank_norm,
                "final": final,
            },
        )
        for rank, (candidate, rerank_norm, final) in enumerate(selected, start=1)
    ]
