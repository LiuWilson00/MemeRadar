"""資料模型寫讀測試（驗收：docs/TASKS.md P0-4，schema 依 docs/01 §4）。

以「種子資料」情境驗證：建庫 → 寫入一張完整梗圖（含標註 / 來源 / 向量）→
讀回一致 → 推薦紀錄與回饋事件關聯正確。
"""

import sqlite3

import pytest

from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import (
    Embedding,
    FeedbackEvent,
    Meme,
    MemeAnnotation,
    MemeSource,
    RecommendationLog,
    new_id,
)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.sqlite3")
    migrate(c)
    yield c
    c.close()


def make_seed_meme() -> tuple[Meme, MemeAnnotation, MemeSource, Embedding]:
    """一筆貼近真實的種子資料（繁中內容 + JSON 欄位）。"""
    meme = Meme(
        meme_id=new_id("m"),
        image_uri="data/images/wo-jiu-lan.png",
        sha256="a" * 64,
        phash="c3d4e5f6a1b2c3d4",
        width=500,
        height=500,
    )
    annotation = MemeAnnotation(
        meme_id=meme.meme_id,
        is_meme=True,
        nsfw=False,
        ocr_text="我就爛",
        description="海綿寶寶攤手站立，表情理直氣壯，配上大字「我就爛」",
        characters=["海綿寶寶"],
        franchise="海綿寶寶",
        template_name="我就爛",
        emotions=["擺爛", "理直氣壯"],
        usage_hints=["被指責能力不足或偷懶時，理直氣壯地自嘲認了"],
        categories=["卡通動畫"],
        confidence=0.93,
        model_version="labeler-v1@claude-opus-4-8",
    )
    source = MemeSource(
        source_id=new_id("s"),
        meme_id=meme.meme_id,
        platform="manual",
        post_url="https://example.com/post/1",
        post_title="上班的我",
        top_comments=["笑死這就是我", "已存"],
        upvotes=872,
        posted_at="2026-07-01T12:00:00+08:00",
    )
    embedding = Embedding(
        meme_id=meme.meme_id,
        kind="text_retrieval",
        model="bge-m3@v1",
        vector=[0.125, -0.5, 0.75],
    )
    return meme, annotation, source, embedding


class TestMigration:
    def test_migrate_creates_all_tables(self, conn):
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert {
            "memes",
            "meme_sources",
            "meme_annotations",
            "embeddings",
            "recommendation_logs",
            "feedback_events",
            "schema_migrations",
        } <= names

    def test_migrate_is_idempotent(self, conn):
        migrate(conn)  # 重跑不應報錯、不重複套用
        applied = conn.execute("SELECT version FROM schema_migrations").fetchall()
        assert len(applied) == len({r["version"] for r in applied})


class TestSeedRoundtrip:
    def test_full_seed_write_and_read_back(self, conn):
        meme, ann, src, emb = make_seed_meme()
        repo.insert_meme(conn, meme)
        repo.upsert_annotation(conn, ann)
        repo.add_source(conn, src)
        repo.add_embedding(conn, emb)

        got = repo.get_meme(conn, meme.meme_id)
        assert got is not None
        assert got.sha256 == meme.sha256
        assert got.status == "active"
        assert got.hotness == 0.0

        got_ann = repo.get_annotation(conn, meme.meme_id)
        assert got_ann.ocr_text == "我就爛"
        assert got_ann.emotions == ["擺爛", "理直氣壯"]
        assert got_ann.usage_hints == ["被指責能力不足或偷懶時，理直氣壯地自嘲認了"]
        assert got_ann.model_version == "labeler-v1@claude-opus-4-8"

        sources = repo.list_sources(conn, meme.meme_id)
        assert len(sources) == 1
        assert sources[0].top_comments == ["笑死這就是我", "已存"]

        embs = repo.get_embeddings(conn, meme.meme_id)
        assert len(embs) == 1
        assert embs[0].vector == [0.125, -0.5, 0.75]
        assert embs[0].model == "bge-m3@v1"

    def test_find_by_sha256_and_unique_constraint(self, conn):
        meme, *_ = make_seed_meme()
        repo.insert_meme(conn, meme)
        assert repo.find_meme_by_sha256(conn, meme.sha256).meme_id == meme.meme_id
        assert repo.find_meme_by_sha256(conn, "f" * 64) is None

        dup = Meme(meme_id=new_id("m"), image_uri="x.png", sha256=meme.sha256)
        with pytest.raises(sqlite3.IntegrityError):
            repo.insert_meme(conn, dup)

    def test_annotation_upsert_overwrites(self, conn):
        meme, ann, *_ = make_seed_meme()
        repo.insert_meme(conn, meme)
        repo.upsert_annotation(conn, ann)
        revised = MemeAnnotation(
            meme_id=meme.meme_id,
            ocr_text="我就爛",
            emotions=["擺爛"],
            model_version="labeler-v2@claude-opus-4-8",
        )
        repo.upsert_annotation(conn, revised)  # 重標覆蓋
        got = repo.get_annotation(conn, meme.meme_id)
        assert got.model_version == "labeler-v2@claude-opus-4-8"
        assert got.emotions == ["擺爛"]

    def test_status_update_and_hotness_accumulation(self, conn):
        meme, *_ = make_seed_meme()
        repo.insert_meme(conn, meme)

        repo.add_hotness(conn, meme.meme_id, 1.5)
        repo.add_hotness(conn, meme.meme_id, 0.5)
        assert repo.get_meme(conn, meme.meme_id).hotness == 2.0

        repo.set_status(conn, meme.meme_id, "removed")
        assert repo.get_meme(conn, meme.meme_id).status == "removed"
        assert repo.count_memes(conn, status="removed") == 1
        assert repo.count_memes(conn, status="active") == 0

    def test_invalid_status_rejected(self, conn):
        meme, *_ = make_seed_meme()
        repo.insert_meme(conn, meme)
        with pytest.raises(sqlite3.IntegrityError):
            repo.set_status(conn, meme.meme_id, "not-a-status")


class TestRecommendationAndFeedback:
    def _seed_log(self, conn, meme_id: str) -> RecommendationLog:
        log = RecommendationLog(
            query_id=new_id("q"),
            conversation=[{"speaker": "other", "text": "你報告又遲交了！"}],
            intent_result={"punchline": "你到底行不行"},
            params_snapshot={"top_n": 5, "min_similarity": 0.35},
            candidates=[{"meme_id": meme_id, "vector": 0.82}],
            final_results=[{"meme_id": meme_id, "rank": 1}],
            latency_ms=4200,
        )
        repo.insert_recommendation_log(conn, log)
        return log

    def test_log_roundtrip(self, conn):
        meme, *_ = make_seed_meme()
        repo.insert_meme(conn, meme)
        log = self._seed_log(conn, meme.meme_id)
        got = repo.get_recommendation_log(conn, log.query_id)
        assert got.conversation[0]["text"] == "你報告又遲交了！"
        assert got.params_snapshot["top_n"] == 5
        assert got.latency_ms == 4200

    def test_feedback_links_query_and_meme(self, conn):
        meme, *_ = make_seed_meme()
        repo.insert_meme(conn, meme)
        log = self._seed_log(conn, meme.meme_id)

        fb = FeedbackEvent(
            feedback_id=new_id("f"),
            query_id=log.query_id,
            meme_id=meme.meme_id,
            rank=1,
            rating="up",
            note="圖對理由也對",
        )
        repo.insert_feedback(conn, fb)
        events = repo.list_feedback(conn, query_id=log.query_id)
        assert len(events) == 1
        assert events[0].rating == "up"
        assert events[0].meme_id == meme.meme_id

    def test_feedback_requires_existing_query(self, conn):
        meme, *_ = make_seed_meme()
        repo.insert_meme(conn, meme)
        fb = FeedbackEvent(
            feedback_id=new_id("f"),
            query_id="q_does_not_exist",
            meme_id=meme.meme_id,
            rank=1,
            rating="down",
        )
        with pytest.raises(sqlite3.IntegrityError):
            repo.insert_feedback(conn, fb)

    def test_invalid_rating_rejected(self, conn):
        meme, *_ = make_seed_meme()
        repo.insert_meme(conn, meme)
        log = self._seed_log(conn, meme.meme_id)
        fb = FeedbackEvent(
            feedback_id=new_id("f"),
            query_id=log.query_id,
            meme_id=meme.meme_id,
            rank=1,
            rating="meh",
        )
        with pytest.raises(sqlite3.IntegrityError):
            repo.insert_feedback(conn, fb)
