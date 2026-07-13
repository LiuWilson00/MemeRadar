"""熱度衰減（docs/06 §3.1）。

    hotness = engagement × e^(−λ · 距最後出現天數) + 0.1 × log10(1 + engagement)

- λ = ln(2)/90：半衰期 90 天，之後靠「梗過時」類 👎 回饋校正。
- 第二項是長青基礎分：歷史累積互動夠高的經典梗（海綿寶寶類模板）
  衰減後仍保有與「經典程度」成正比的基礎分，不會歸零消失。
- ``engagement`` / ``last_seen_at`` 是事實來源，``hotness`` 為推導值，
  重算冪等——每日 job 跑幾次都得到同一結果。
- 排序端已以 α=0.1 低權重消費 memes.hotness（matching/rerank.py），
  本模組讓該欄位隨時間自然降溫，無須改排序程式。

每日 job：``python -m memeradar.shared.hotness``（排程掛 Windows 工作排程器 / cron）。
"""

from __future__ import annotations

import math
import sqlite3
import sys
from datetime import UTC, datetime

HALF_LIFE_DAYS = 90.0
DECAY_LAMBDA = math.log(2) / HALF_LIFE_DAYS
EVERGREEN_COEF = 0.1  # 長青基礎分係數：floor = 0.1 × log10(1 + engagement)


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def compute_hotness(
    engagement: float, last_seen_at: str | None, *, now: datetime | None = None
) -> float:
    if engagement <= 0:
        return 0.0
    now = now or datetime.now(UTC)
    days = 0.0
    if last_seen_at:
        days = max(0.0, (now - _parse_ts(last_seen_at)).total_seconds() / 86400.0)
    decayed = engagement * math.exp(-DECAY_LAMBDA * days)
    floor = EVERGREEN_COEF * math.log10(1.0 + engagement)
    return decayed + floor


def record_engagement(
    conn: sqlite3.Connection, meme_id: str, gain: float, *, now: datetime | None = None
) -> None:
    """同圖再現：累加互動分、刷新最後出現時間、立即重算 hotness。

    last_seen_at 只向前不回退（補爬舊貼文不會讓活梗顯得過時）。
    """
    now = now or datetime.now(UTC)
    now_iso = now.isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE memes
        SET engagement = engagement + %s,
            last_seen_at = GREATEST(COALESCE(last_seen_at, ''), %s)
        WHERE meme_id = %s
        """,
        (gain, now_iso, meme_id),
    )
    row = conn.execute(
        "SELECT engagement, last_seen_at FROM memes WHERE meme_id = %s", (meme_id,)
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE memes SET hotness = %s WHERE meme_id = %s",
            (compute_hotness(row["engagement"], row["last_seen_at"], now=now), meme_id),
        )
    conn.commit()


def recompute_all_hotness(conn: sqlite3.Connection, *, now: datetime | None = None) -> int:
    """每日重算：全量 hotness ← f(engagement, last_seen_at)。冪等。"""
    now = now or datetime.now(UTC)
    rows = conn.execute("SELECT meme_id, engagement, last_seen_at FROM memes").fetchall()
    for row in rows:
        conn.execute(
            "UPDATE memes SET hotness = %s WHERE meme_id = %s",
            (
                compute_hotness(row["engagement"], row["last_seen_at"], now=now),
                row["meme_id"],
            ),
        )
    conn.commit()
    return len(rows)


def main() -> None:
    # Windows 主控台預設編碼常非 UTF-8，避免中文輸出亂碼
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    from memeradar.shared.db import connect, migrate

    conn = connect()
    try:
        migrate(conn)
        count = recompute_all_hotness(conn)
    finally:
        conn.close()
    print(f"已重算 {count} 張梗圖的熱度（半衰期 {HALF_LIFE_DAYS:.0f} 天）")


if __name__ == "__main__":
    main()
