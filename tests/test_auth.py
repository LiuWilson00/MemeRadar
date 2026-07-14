"""D1 登入基礎：我方 session JWT + users 表寫讀。"""

from __future__ import annotations

import pytest

from memeradar.shared import auth
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate


class TestSessionToken:
    def test_roundtrip_returns_user_id(self):
        token = auth.issue_session("u_123", "topsecret")
        assert auth.verify_session(token, "topsecret") == "u_123"

    def test_wrong_secret_rejected(self):
        token = auth.issue_session("u_123", "topsecret")
        assert auth.verify_session(token, "othersecret") is None

    def test_tampered_token_rejected(self):
        token = auth.issue_session("u_123", "topsecret")
        assert auth.verify_session(token + "x", "topsecret") is None

    def test_garbage_rejected(self):
        assert auth.verify_session("not-a-jwt", "topsecret") is None

    def test_expired_token_rejected(self):
        token = auth.issue_session("u_123", "topsecret", ttl_seconds=-10)
        assert auth.verify_session(token, "topsecret") is None


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


class TestUsersRepo:
    def test_upsert_creates_then_returns_row(self, conn):
        user = repo.upsert_user(
            conn, google_sub="g-sub-1", email="a@x.com", name="Amy", picture="http://p/a.png")
        assert user["user_id"].startswith("u_")
        assert user["google_sub"] == "g-sub-1"
        assert user["email"] == "a@x.com"
        assert user["name"] == "Amy"
        assert user["role"] == "user"

    def test_upsert_is_idempotent_by_google_sub(self, conn):
        first = repo.upsert_user(
            conn, google_sub="g-sub-1", email="a@x.com", name="Amy", picture="")
        second = repo.upsert_user(
            conn, google_sub="g-sub-1", email="a@new.com", name="Amy Chen", picture="")
        # 同一 Google 帳號 → 同一 user_id，資料更新
        assert second["user_id"] == first["user_id"]
        assert second["email"] == "a@new.com"
        assert second["name"] == "Amy Chen"
        n = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        assert n == 1

    def test_get_user_roundtrip_and_missing(self, conn):
        user = repo.upsert_user(
            conn, google_sub="g-sub-1", email="a@x.com", name="Amy", picture="")
        assert repo.get_user(conn, user["user_id"])["email"] == "a@x.com"
        assert repo.get_user(conn, "u_nope") is None
