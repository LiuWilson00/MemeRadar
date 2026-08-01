"""每日一梗：選圖 → 調研 → 依信心決定上線或進草稿。

排程走 API 內建的常駐執行緒（比照背景標註 worker），因為 Zeabur 沒有內建 cron。
執行緒每小時醒來問一次「今天有文章了嗎」，沒有就產一篇——重啟、當機、部署中斷都會
自動補上，不需要外部服務。

冪等靠 ``blog_posts.featured_on``：同一天已經有紀錄（含草稿與退稿）就不再產。
這很重要——每篇要 $0.08~0.23，重複觸發等於重複付錢。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from memeradar.blog import repository as blog_repo
from memeradar.blog.research import research_meme, should_auto_publish
from memeradar.blog.selection import MemeCandidate, pick_for_day
from memeradar.shared.models import new_id

logger = logging.getLogger("memeradar.blog")


def today_str(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m-%d")


def load_candidates(conn, *, exclude: set[str], limit: int = 400) -> list[MemeCandidate]:
    """可入選的梗圖：上架、已標註、是梗圖、非 NSFW。

    只取熱門前 ``limit`` 張進來評分——全庫掃描沒必要，且熱度為 0 的圖佔絕大多數
    （memes.tw 各深度按讚中位數都是 0），選圖時本來就排在後面。
    """
    rows = conn.execute(
        """
        SELECT m.meme_id, m.hotness, a.franchise, a.categories, a.template_name
        FROM memes m JOIN meme_annotations a USING (meme_id)
        WHERE m.status = 'active' AND a.is_meme = 1 AND a.nsfw = 0
        ORDER BY m.hotness DESC, m.first_seen_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    out: list[MemeCandidate] = []
    for r in rows:
        if r["meme_id"] in exclude:
            continue
        cats = r["categories"]
        if isinstance(cats, str):
            import json

            try:
                cats = json.loads(cats)
            except (TypeError, ValueError):
                cats = []
        out.append(MemeCandidate(
            meme_id=r["meme_id"],
            hotness=float(r["hotness"] or 0.0),
            franchise=r["franchise"] or None,
            category=(cats or [None])[0],
            has_template=bool(r["template_name"]),
        ))
    return out


def _recent_context(conn, limit: int = 10) -> list[MemeCandidate]:
    """最近幾篇寫過的題材，供選圖避免撞題。"""
    rows = conn.execute(
        """
        SELECT a.franchise, a.categories FROM blog_posts b
        JOIN meme_annotations a ON a.meme_id = b.meme_id
        ORDER BY b.created_at DESC LIMIT %s
        """,
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        cats = r["categories"]
        if isinstance(cats, str):
            import json

            try:
                cats = json.loads(cats)
            except (TypeError, ValueError):
                cats = []
        out.append(MemeCandidate(meme_id="", hotness=0.0, franchise=r["franchise"] or None,
                                 category=(cats or [None])[0], has_template=False))
    return out


def generate_for_day(deps, conn, *, day: str | None = None, force: bool = False) -> dict | None:
    """產出當日文章。已存在且非 force 時直接回既有那篇（冪等，不重複付費）。"""
    from memeradar.shared import repository as repo
    from memeradar.shared.config import get_settings
    from memeradar.understanding.annotator import load_meme_image_bytes

    day = day or today_str()
    existing = blog_repo.get_post_for_day(conn, day)
    if existing is not None and not force:
        return existing

    candidates = load_candidates(conn, exclude=blog_repo.featured_meme_ids(conn))
    picked = pick_for_day(candidates, day=day, recent=_recent_context(conn))
    if picked is None:
        logger.warning("[blog] %s 沒有可用候選梗圖，今天不產文", day)
        return None

    meme_id = picked.candidate.meme_id
    meme = repo.get_meme(conn, meme_id)
    annotation = repo.get_annotation(conn, meme_id)
    if meme is None or annotation is None:
        logger.warning("[blog] %s 候選 %s 缺梗圖或標註，跳過", day, meme_id)
        return None

    image = load_meme_image_bytes(conn, meme, data_dir=deps.data_dir)
    settings = get_settings()
    logger.info("[blog] %s 選中 %s（score=%.3f %s）開始調研",
                day, meme_id, picked.score, picked.breakdown)
    result = research_meme(
        deps.blog_vlm, image,
        ocr_text=annotation.ocr_text, description=annotation.description,
        franchise=annotation.franchise, meme_id=meme_id,
        log=lambda rec: repo.insert_vlm_call(conn, rec),
    )
    if result is None:
        logger.warning("[blog] %s 調研失敗（模型未回可解析的 JSON），今天不產文", day)
        return None

    auto = should_auto_publish(
        result, min_confidence=settings.blog_auto_publish_min_confidence)
    post = blog_repo.create_post(
        conn, post_id=new_id("post"), meme_id=meme_id,
        title=result.title, article_html=result.article_html,
        status="published" if auto else "draft",
        verdict=result.verdict, confidence=result.confidence,
        origin=result.origin.model_dump(),
        caption_is_original=result.caption_is_original,
        caption_note=result.caption_note,
        sources=[s.model_dump() for s in result.sources],
        unverified_claims=result.unverified_claims,
        model_version=f"research-v1@{getattr(deps.blog_vlm, 'model', '?')}",
        featured_on=day,
    )
    logger.info("[blog] %s 產出 %s（%s，verdict=%s conf=%.2f）",
                day, post["post_id"], post["status"], result.verdict, result.confidence)
    return post
