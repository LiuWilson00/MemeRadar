"""每日一梗 API：公開只看得到已發布、後台審核流程、產文冪等。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from memeradar.api.app import Deps, create_app
from memeradar.blog import repository as blog_repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import new_id


@pytest.fixture
def env(tmp_path):
    conn = connect(tmp_path / "db.sqlite3")
    migrate(conn)
    deps = Deps(client=None, vlm=None, embedder=None,
                db_path=tmp_path / "db.sqlite3", data_dir=tmp_path)
    return TestClient(create_app(deps)), conn, deps


def _post(conn, *, status, title="標題", day="2026-08-02"):
    return blog_repo.create_post(
        conn, post_id=new_id("post"), meme_id=new_id("m"), title=title,
        article_html="<p>內文</p>", status=status, verdict="identified",
        confidence=0.9, sources=[{"title": "s", "url": "https://e.com", "supports": "x"}],
        unverified_claims=["這句查不到"], featured_on=day)


class TestPublicVisibility:
    def test_only_published_listed(self, env):
        client, conn, _ = env
        _post(conn, status="draft", title="草稿")
        _post(conn, status="published", title="上線")
        titles = [p["title"] for p in client.get("/blog").json()]
        assert titles == ["上線"]

    def test_draft_not_reachable_by_slug(self, env):
        client, conn, _ = env
        draft = _post(conn, status="draft")
        assert client.get(f"/blog/{draft['slug']}").status_code == 404

    def test_published_post_readable(self, env):
        client, conn, _ = env
        pub = _post(conn, status="published")
        body = client.get(f"/blog/{pub['slug']}").json()
        assert body["title"] == "標題" and "<p>" in body["article_html"]

    def test_public_view_hides_reviewer_only_fields(self, env):
        """unverified_claims 是給審核者判斷用的，讀者看到會誤以為是內容的一部分。"""
        client, conn, _ = env
        pub = _post(conn, status="published")
        body = client.get(f"/blog/{pub['slug']}").json()
        assert "unverified_claims" not in body
        assert "sources" in body  # 來源要給讀者，那是考據的憑據


class TestAdminReview:
    def test_admin_sees_drafts(self, env):
        client, conn, _ = env
        _post(conn, status="draft")
        assert len(client.get("/admin/blog").json()) == 1

    def test_publish_a_draft(self, env):
        client, conn, _ = env
        draft = _post(conn, status="draft")
        r = client.post(f"/admin/blog/{draft['post_id']}/status", json={"status": "published"})
        assert r.status_code == 200 and r.json()["status"] == "published"
        assert client.get(f"/blog/{draft['slug']}").status_code == 200

    def test_invalid_status_rejected(self, env):
        client, conn, _ = env
        draft = _post(conn, status="draft")
        assert client.post(f"/admin/blog/{draft['post_id']}/status",
                           json={"status": "上線啦"}).status_code == 422

    def test_edit_keeps_research_fields(self, env):
        """人工改文案不該動到模型的原始調研輸出——那是事後對照品質的依據。"""
        client, conn, _ = env
        draft = _post(conn, status="draft")
        r = client.put(f"/admin/blog/{draft['post_id']}",
                       json={"title": "改過的標題"})
        assert r.status_code == 200
        assert r.json()["title"] == "改過的標題"
        assert r.json()["confidence"] == 0.9
        assert r.json()["unverified_claims"] == ["這句查不到"]


class TestGenerateGuard:
    def test_generate_without_model_returns_503(self, env):
        client, _conn, _ = env
        assert client.post("/admin/blog/generate", json={}).status_code == 503
