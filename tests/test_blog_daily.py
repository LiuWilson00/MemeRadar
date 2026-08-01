"""每日一梗產文：冪等（不重複付費）、自動發布閘門、選圖排除已寫過的。"""

from __future__ import annotations

import io as _io

import pytest
from PIL import Image

from memeradar.api.app import Deps
from memeradar.blog import repository as blog_repo
from memeradar.blog.daily import generate_for_day
from memeradar.blog.research import ResearchOrigin, ResearchResult, ResearchSource
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Meme, MemeAnnotation, new_id


def _png() -> bytes:
    buf = _io.BytesIO()
    Image.new("RGB", (32, 32), (200, 100, 50)).save(buf, "PNG")
    return buf.getvalue()


class StubResearchVlm:
    """假的調研 client：記錄被呼叫幾次，回固定結果。"""

    model = "stub-sonnet"

    def __init__(self, result: ResearchResult):
        self.result = result
        self.calls = 0


def _result(verdict="identified", confidence=0.9, sources=True) -> ResearchResult:
    return ResearchResult(
        verdict=verdict, confidence=confidence,
        origin=ResearchOrigin(work="九品芝麻官", year="1994", scene="公堂",
                              characters="方唐鏡", region="香港"),
        caption_is_original=True, caption_note="原台詞",
        sources=([ResearchSource(title="來源", url="https://example.com", supports="出處")]
                 if sources else []),
        unverified_claims=[], title="打我呀笨蛋的出處",
        article_html="<p>內文</p>")


@pytest.fixture
def env(tmp_path, monkeypatch):
    conn = connect(tmp_path / "db.sqlite3")
    migrate(conn)
    meme_id = new_id("m")
    repo.insert_meme(conn, Meme(meme_id=meme_id, image_uri=f"images/{meme_id}.png",
                                sha256="a" * 64, hotness=10.0))
    repo.upsert_annotation(conn, MemeAnnotation(
        meme_id=meme_id, model_version="v", is_meme=True, ocr_text="打我呀笨蛋",
        description="公堂場景", characters=[], franchise="九品芝麻官",
        emotions=["嘲諷"], usage_hints=["挑釁"], categories=["電影"], confidence=0.9))
    images = tmp_path / "images"
    images.mkdir(exist_ok=True)
    (images / f"{meme_id}.png").write_bytes(_png())
    deps = Deps(client=None, vlm=None, embedder=None,
                db_path=tmp_path / "db.sqlite3", data_dir=tmp_path)
    return conn, deps, meme_id


def _patch_research(monkeypatch, result, counter):
    def fake(vlm, image, **kw):
        counter.append(1)
        return result
    monkeypatch.setattr("memeradar.blog.daily.research_meme", fake)


class TestAutoPublishGate:
    def test_high_confidence_publishes(self, env, monkeypatch):
        conn, deps, _ = env
        deps.blog_vlm = object()
        _patch_research(monkeypatch, _result(), [])
        post = generate_for_day(deps, conn, day="2026-08-02")
        assert post["status"] == "published"

    def test_unknown_verdict_stays_draft(self, env, monkeypatch):
        """查不到出處的文章不能自動上線——那正是 PoC 裡便宜模型會編故事的情境。"""
        conn, deps, _ = env
        deps.blog_vlm = object()
        _patch_research(monkeypatch, _result(verdict="unknown", confidence=0.15), [])
        assert generate_for_day(deps, conn, day="2026-08-02")["status"] == "draft"

    def test_identified_but_no_sources_stays_draft(self, env, monkeypatch):
        """說查到了卻給不出來源＝沒有憑據，一樣要人看過。"""
        conn, deps, _ = env
        deps.blog_vlm = object()
        _patch_research(monkeypatch, _result(sources=False), [])
        assert generate_for_day(deps, conn, day="2026-08-02")["status"] == "draft"


class TestIdempotency:
    def test_second_run_same_day_does_not_call_the_model(self, env, monkeypatch):
        """每篇要 $0.08~0.23，同一天重跑絕不能再付一次錢。"""
        conn, deps, _ = env
        deps.blog_vlm = object()
        calls: list[int] = []
        _patch_research(monkeypatch, _result(), calls)
        first = generate_for_day(deps, conn, day="2026-08-02")
        again = generate_for_day(deps, conn, day="2026-08-02")
        assert again["post_id"] == first["post_id"]
        assert len(calls) == 1, "第二次不該再呼叫調研模型"

    def test_already_featured_meme_is_not_picked_again(self, env, monkeypatch):
        conn, deps, meme_id = env
        deps.blog_vlm = object()
        _patch_research(monkeypatch, _result(), [])
        generate_for_day(deps, conn, day="2026-08-02")
        # 圖庫只有這一張，且已寫過 → 隔天沒有候選
        assert generate_for_day(deps, conn, day="2026-08-03") is None
        assert blog_repo.featured_meme_ids(conn) == {meme_id}


class TestResearchFailure:
    def test_unparseable_research_produces_no_post(self, env, monkeypatch):
        conn, deps, _ = env
        deps.blog_vlm = object()
        monkeypatch.setattr("memeradar.blog.daily.research_meme", lambda *a, **k: None)
        assert generate_for_day(deps, conn, day="2026-08-02") is None
        assert blog_repo.get_post_for_day(conn, "2026-08-02") is None
