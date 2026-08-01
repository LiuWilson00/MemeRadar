"""blog_posts 的存取層。與 shared/repository.py 同風格：每寫即 commit、回 dict。

放在 blog/ 而非塞進已經 1400 行的 shared/repository.py——那支檔案的長度已經是這次
架構檢視點名的問題之一，不該再往上疊。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

_SLUG_STRIP = re.compile(r"[^\w一-鿿-]+")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _dumps(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _loads(value):
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def make_slug(title: str, meme_id: str) -> str:
    """網址用 slug：標題去標點 + meme_id 尾碼保證唯一。

    保留中日韓字元——這是繁中站，`/blog/九品芝麻官-a1b2c3` 比一串拼音好認得多，
    現代瀏覽器與搜尋引擎都吃得下 UTF-8 網址。
    """
    base = _SLUG_STRIP.sub("-", (title or "").strip()).strip("-")[:40] or "meme"
    return f"{base}-{meme_id[-6:]}"


def create_post(
    conn,
    *,
    post_id: str,
    meme_id: str,
    title: str,
    article_html: str,
    status: str,
    verdict: str | None = None,
    confidence: float | None = None,
    origin: dict | None = None,
    caption_is_original: bool | None = None,
    caption_note: str | None = None,
    sources: list | None = None,
    unverified_claims: list | None = None,
    model_version: str | None = None,
    cost_usd: float | None = None,
    featured_on: str | None = None,
) -> dict:
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO blog_posts (post_id, slug, meme_id, title, article_html, status,
            verdict, confidence, origin, caption_is_original, caption_note, sources,
            unverified_claims, model_version, cost_usd, featured_on, created_at, published_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            post_id, make_slug(title, meme_id), meme_id, title, article_html, status,
            verdict, confidence, _dumps(origin),
            None if caption_is_original is None else int(caption_is_original),
            caption_note, _dumps(sources), _dumps(unverified_claims),
            model_version, cost_usd, featured_on, now,
            now if status == "published" else None,
        ),
    )
    conn.commit()
    return get_post(conn, post_id)


def _row_to_post(row) -> dict:
    post = dict(row)
    for field in ("origin", "sources", "unverified_claims"):
        post[field] = _loads(post.get(field))
    if post.get("caption_is_original") is not None:
        post["caption_is_original"] = bool(post["caption_is_original"])
    return post


def get_post(conn, post_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM blog_posts WHERE post_id = %s", (post_id,)).fetchone()
    return None if row is None else _row_to_post(row)


def get_post_by_slug(conn, slug: str) -> dict | None:
    row = conn.execute("SELECT * FROM blog_posts WHERE slug = %s", (slug,)).fetchone()
    return None if row is None else _row_to_post(row)


def list_posts(conn, *, status: str | None = "published", limit: int = 30,
               offset: int = 0) -> list[dict]:
    """依 featured_on 新到舊。status=None 取全部（後台用）。"""
    if status is None:
        rows = conn.execute(
            "SELECT * FROM blog_posts ORDER BY featured_on DESC, created_at DESC "
            "LIMIT %s OFFSET %s", (limit, offset)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM blog_posts WHERE status = %s "
            "ORDER BY featured_on DESC, created_at DESC LIMIT %s OFFSET %s",
            (status, limit, offset)).fetchall()
    return [_row_to_post(r) for r in rows]


def get_post_for_day(conn, day: str) -> dict | None:
    """某天的當日文章（不論狀態）——排程器靠這個判斷「今天寫過了沒」。"""
    row = conn.execute(
        "SELECT * FROM blog_posts WHERE featured_on = %s ORDER BY created_at LIMIT 1",
        (day,)).fetchone()
    return None if row is None else _row_to_post(row)


def featured_meme_ids(conn) -> set[str]:
    """已經寫過的梗圖（含草稿與退稿）——選圖時要排除，避免重寫與重複付費。"""
    return {r["meme_id"] for r in conn.execute("SELECT meme_id FROM blog_posts").fetchall()}


def set_status(conn, post_id: str, status: str) -> dict | None:
    published_at = _now_iso() if status == "published" else None
    conn.execute(
        "UPDATE blog_posts SET status = %s, published_at = COALESCE(%s, published_at) "
        "WHERE post_id = %s", (status, published_at, post_id))
    conn.commit()
    return get_post(conn, post_id)


def update_content(conn, post_id: str, *, title: str | None = None,
                   article_html: str | None = None) -> dict | None:
    """人工修訂：審核者改標題／內文後存回（調研欄位不動，那是模型的原始輸出）。"""
    current = get_post(conn, post_id)
    if current is None:
        return None
    conn.execute(
        "UPDATE blog_posts SET title = %s, article_html = %s WHERE post_id = %s",
        (title if title is not None else current["title"],
         article_html if article_html is not None else current["article_html"],
         post_id))
    conn.commit()
    return get_post(conn, post_id)
