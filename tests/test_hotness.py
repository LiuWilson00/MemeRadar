"""P4-3 熱度衰減（docs/06 §3.1）。

公式：hotness = engagement × e^(−λ·距最後出現天數) + 0.1 × log10(1 + engagement)
- λ = ln(2)/90（半衰期 90 天）
- 第二項是長青基礎分：歷史累積互動夠高的經典梗不會歸零消失。
- engagement / last_seen_at 是事實來源，hotness 為推導值 → 重算冪等。
"""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from memeradar.shared.db import MIGRATIONS_DIR, migrate
from memeradar.shared.hotness import (
    EVERGREEN_COEF,
    HALF_LIFE_DAYS,
    compute_hotness,
    recompute_all_hotness,
    record_engagement,
)
from memeradar.shared.models import Meme
from memeradar.shared.repository import get_meme, insert_meme

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


@pytest.fixture
def conn(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.sqlite3")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    yield conn
    conn.close()


class TestComputeHotness:
    def test_fresh_meme_keeps_full_engagement_plus_floor(self):
        got = compute_hotness(10.0, _iso(NOW), now=NOW)
        assert got == pytest.approx(10.0 + EVERGREEN_COEF * math.log10(11.0))

    def test_half_life_halves_decayed_part(self):
        last_seen = _iso(NOW - timedelta(days=HALF_LIFE_DAYS))
        got = compute_hotness(10.0, last_seen, now=NOW)
        assert got == pytest.approx(5.0 + EVERGREEN_COEF * math.log10(11.0), rel=1e-3)

    def test_evergreen_floor_survives_long_decay(self):
        last_seen = _iso(NOW - timedelta(days=3650))
        got = compute_hotness(1000.0, last_seen, now=NOW)
        floor = EVERGREEN_COEF * math.log10(1001.0)
        assert got == pytest.approx(floor, rel=1e-3)
        assert got > 0

    def test_zero_engagement_is_zero(self):
        assert compute_hotness(0.0, _iso(NOW), now=NOW) == 0.0

    def test_missing_last_seen_treated_as_now(self):
        got = compute_hotness(10.0, None, now=NOW)
        assert got == pytest.approx(10.0 + EVERGREEN_COEF * math.log10(11.0))

    def test_future_last_seen_clamped_to_zero_days(self):
        last_seen = _iso(NOW + timedelta(days=3))
        got = compute_hotness(10.0, last_seen, now=NOW)
        assert got == pytest.approx(10.0 + EVERGREEN_COEF * math.log10(11.0))


class TestRecordEngagement:
    def test_accumulates_and_refreshes_last_seen(self, conn):
        insert_meme(conn, Meme(meme_id="m1", image_uri="a.png", sha256="s1"))
        record_engagement(conn, "m1", 2.0, now=NOW)
        record_engagement(conn, "m1", 3.0, now=NOW + timedelta(days=1))

        row = conn.execute("SELECT * FROM memes WHERE meme_id='m1'").fetchone()
        assert row["engagement"] == pytest.approx(5.0)
        assert row["last_seen_at"] == _iso(NOW + timedelta(days=1))
        # hotness 立即以新事實重算（不等每日 job）
        assert row["hotness"] == pytest.approx(
            compute_hotness(5.0, row["last_seen_at"], now=NOW + timedelta(days=1))
        )

    def test_last_seen_never_moves_backwards(self, conn):
        insert_meme(conn, Meme(meme_id="m1", image_uri="a.png", sha256="s1"))
        record_engagement(conn, "m1", 1.0, now=NOW)
        record_engagement(conn, "m1", 1.0, now=NOW - timedelta(days=30))

        row = conn.execute("SELECT * FROM memes WHERE meme_id='m1'").fetchone()
        assert row["last_seen_at"] == _iso(NOW)
        assert row["engagement"] == pytest.approx(2.0)


class TestRecomputeAll:
    def test_recomputes_decay_and_is_idempotent(self, conn):
        insert_meme(conn, Meme(meme_id="old", image_uri="a.png", sha256="s1"))
        insert_meme(conn, Meme(meme_id="new", image_uri="b.png", sha256="s2"))
        record_engagement(conn, "old", 10.0, now=NOW - timedelta(days=HALF_LIFE_DAYS))
        record_engagement(conn, "new", 10.0, now=NOW)

        count = recompute_all_hotness(conn, now=NOW)
        assert count == 2

        old = get_meme(conn, "old")
        new = get_meme(conn, "new")
        assert old.hotness == pytest.approx(
            5.0 + EVERGREEN_COEF * math.log10(11.0), rel=1e-3
        )
        assert new.hotness == pytest.approx(10.0 + EVERGREEN_COEF * math.log10(11.0))

        recompute_all_hotness(conn, now=NOW)  # 再跑一次結果不變（冪等）
        assert get_meme(conn, "old").hotness == pytest.approx(old.hotness)
        assert get_meme(conn, "new").hotness == pytest.approx(new.hotness)


class TestMigrationBackfill:
    def test_legacy_hotness_backfilled_into_engagement(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "legacy.sqlite3")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # 手動套到 0004 為止，模擬 P4-3 前的既有資料庫
        for script in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if script.stem >= "0005":
                continue
            conn.executescript(script.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)", (script.stem,)
            )
        conn.execute(
            "INSERT INTO memes (meme_id, image_uri, sha256, hotness, first_seen_at) "
            "VALUES ('m1', 'a.png', 's1', 5.0, '2026-06-01T00:00:00+00:00')"
        )
        conn.commit()

        migrate(conn)  # 套用 0005

        row = conn.execute("SELECT * FROM memes WHERE meme_id='m1'").fetchone()
        assert row["engagement"] == pytest.approx(5.0)
        assert row["last_seen_at"] == "2026-06-01T00:00:00+00:00"
        conn.close()
