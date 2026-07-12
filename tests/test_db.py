"""資料模型寫讀測試（驗收：docs/TASKS.md P0-4，schema 依 docs/01 §4）。

以「種子資料」情境驗證：建庫 → 寫入一張完整梗圖（含標註 / 來源 / 向量）→
讀回一致 → 推薦紀錄與回饋事件關聯正確。
"""

import sqlite3
from dataclasses import replace

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


class TestThreadSafety:
    def test_connection_can_close_from_another_thread(self, tmp_path):
        """FastAPI 的 sync yield 依賴會在不同 threadpool 執行緒 setup/teardown：
        連線在 A 執行緒建立、B 執行緒關閉；預設 check_same_thread=True 會丟
        ProgrammingError → 端點在並發下 500（圖片牆一次載多張即引爆）。"""
        import threading

        c = connect(tmp_path / "cross.sqlite3")
        migrate(c)
        errors: list[Exception] = []

        def close_elsewhere():
            try:
                c.execute("SELECT 1").fetchone()  # 跨執行緒查詢
                c.close()  # 跨執行緒關閉（原 bug 觸發點）
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=close_elsewhere)
        t.start()
        t.join()
        assert not errors, errors


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
            "crawl_state",
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

    def test_status_update_and_engagement_accumulation(self, conn):
        from memeradar.shared.hotness import compute_hotness, record_engagement

        meme, *_ = make_seed_meme()
        repo.insert_meme(conn, meme)

        record_engagement(conn, meme.meme_id, 1.5)
        record_engagement(conn, meme.meme_id, 0.5)
        got = repo.get_meme(conn, meme.meme_id)
        assert got.engagement == 2.0
        # hotness 為推導值：f(engagement, last_seen_at)（docs/06 §3.1）
        assert got.hotness == pytest.approx(compute_hotness(2.0, got.last_seen_at))

        repo.set_status(conn, meme.meme_id, "removed")
        assert repo.get_meme(conn, meme.meme_id).status == "removed"
        assert repo.count_memes(conn, status="removed") == 1
        assert repo.count_memes(conn, status="active") == 0

    def test_invalid_status_rejected(self, conn):
        meme, *_ = make_seed_meme()
        repo.insert_meme(conn, meme)
        with pytest.raises(sqlite3.IntegrityError):
            repo.set_status(conn, meme.meme_id, "not-a-status")


class TestGetVectors:
    def test_batch_load_by_ids_and_signature(self, conn):
        meme_a, *_ = make_seed_meme()
        repo.insert_meme(conn, meme_a)
        repo.add_embedding(
            conn,
            Embedding(meme_id=meme_a.meme_id, kind="text_retrieval", model="sig|v1",
                      vector=[0.1, 0.2]),
        )
        repo.add_embedding(
            conn,
            Embedding(meme_id=meme_a.meme_id, kind="text_retrieval", model="other|v9",
                      vector=[9.9]),
        )

        vectors = repo.get_vectors(
            conn, kind="text_retrieval", model="sig|v1",
            meme_ids=[meme_a.meme_id, "m_missing"],
        )
        assert vectors == {meme_a.meme_id: [0.1, 0.2]}  # 只回簽名相符者；缺席者不含

    def test_empty_ids_returns_empty(self, conn):
        assert repo.get_vectors(conn, kind="text_retrieval", model="sig|v1", meme_ids=[]) == {}


class TestMissingAnnotationQuery:
    def test_lists_only_unannotated_non_removed(self, conn):
        meme_a, ann_a, *_ = make_seed_meme()
        repo.insert_meme(conn, meme_a)
        repo.upsert_annotation(conn, ann_a)  # 已標註 → 不應列出

        meme_b = Meme(meme_id=new_id("m"), image_uri="b.png", sha256="b" * 64)
        repo.insert_meme(conn, meme_b)  # 未標註 → 應列出

        meme_c = Meme(meme_id=new_id("m"), image_uri="c.png", sha256="c" * 64)
        repo.insert_meme(conn, meme_c)
        repo.set_status(conn, meme_c.meme_id, "removed")  # 已下架 → 不應列出

        pending = repo.list_memes_missing_annotation(conn)
        assert [m.meme_id for m in pending] == [meme_b.meme_id]

    def test_respects_limit(self, conn):
        for i in range(3):
            repo.insert_meme(
                conn, Meme(meme_id=new_id("m"), image_uri=f"{i}.png", sha256=str(i) * 64)
            )
        assert len(repo.list_memes_missing_annotation(conn, limit=2)) == 2


class TestHistoryQuery:
    def test_list_logs_newest_first_with_feedback_counts(self, conn):
        # 三張不同梗圖各一票（回饋對每組 query+meme 冪等，不同 meme 各自計數）
        memes = []
        for i in range(3):
            m, *_ = make_seed_meme()
            m = replace(m, meme_id=new_id("m"), sha256=f"{i}".ljust(64, "0")[:64])
            repo.insert_meme(conn, m)
            memes.append(m)
        older = RecommendationLog(
            query_id=new_id("q"), conversation=[{"speaker": "other", "text": "早的"}],
            params_snapshot={"params": {"top_n": 5}}, created_at="2026-07-10T10:00:00+00:00",
        )
        newer = RecommendationLog(
            query_id=new_id("q"), conversation=[{"speaker": "other", "text": "晚的"}],
            params_snapshot={"params": {"top_n": 3}}, latency_ms=1234,
            final_results=[{"meme_id": memes[0].meme_id, "rank": 1}],
            created_at="2026-07-11T10:00:00+00:00",
        )
        repo.insert_recommendation_log(conn, older)
        repo.insert_recommendation_log(conn, newer)
        for m, rating in zip(memes, ("up", "up", "down"), strict=True):
            repo.insert_feedback(conn, FeedbackEvent(
                feedback_id=new_id("f"), query_id=newer.query_id,
                meme_id=m.meme_id, rank=1, rating=rating,
            ))

        logs = repo.list_recommendation_logs(conn)

        assert [entry["query_id"] for entry in logs] == [newer.query_id, older.query_id]
        assert logs[0]["ups"] == 2 and logs[0]["downs"] == 1
        assert logs[0]["result_count"] == 1
        assert logs[0]["conversation"][0]["text"] == "晚的"
        assert logs[1]["ups"] == 0 and logs[1]["downs"] == 0

    def test_list_logs_limit(self, conn):
        for i in range(3):
            repo.insert_recommendation_log(conn, RecommendationLog(
                query_id=new_id("q"), conversation=[], params_snapshot={},
                created_at=f"2026-07-0{i + 1}T00:00:00+00:00",
            ))
        assert len(repo.list_recommendation_logs(conn, limit=2)) == 2


class TestLibraryQuery:
    def _seed(self, conn, *, franchise, emotions, status="active"):
        meme = Meme(meme_id=new_id("m"), image_uri="x.png", sha256=new_id("h").ljust(64, "0")[:64])
        repo.insert_meme(conn, meme)
        repo.upsert_annotation(conn, MemeAnnotation(
            meme_id=meme.meme_id, model_version="v", ocr_text="字",
            franchise=franchise, emotions=emotions, categories=["卡通動畫"],
        ))
        if status != "active":
            repo.set_status(conn, meme.meme_id, status)
        return meme

    def test_filters_by_franchise_emotion_status(self, conn):
        sponge = self._seed(conn, franchise="海綿寶寶", emotions=["擺爛"])
        zhen = self._seed(conn, franchise="甄嬛傳", emotions=["崩潰"])
        pending = self._seed(conn, franchise="海綿寶寶", emotions=["擺爛"], status="pending_review")

        rows = repo.list_memes_with_annotations(conn, franchise="海綿寶寶")
        assert {r["meme_id"] for r in rows} == {sponge.meme_id, pending.meme_id}

        rows = repo.list_memes_with_annotations(conn, emotion="崩潰")
        assert [r["meme_id"] for r in rows] == [zhen.meme_id]
        assert rows[0]["annotation"]["franchise"] == "甄嬛傳"

        rows = repo.list_memes_with_annotations(conn, status="pending_review")
        assert [r["meme_id"] for r in rows] == [pending.meme_id]

    def test_unannotated_meme_listed_with_null_annotation(self, conn):
        bare = Meme(meme_id=new_id("m"), image_uri="u.png", sha256="9" * 64)
        repo.insert_meme(conn, bare)
        rows = repo.list_memes_with_annotations(conn)
        target = next(r for r in rows if r["meme_id"] == bare.meme_id)
        assert target["annotation"] is None


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

    def test_feedback_is_idempotent_per_query_meme(self, conn):
        """同一查詢的同一張圖只保留一筆回饋，改投以最新為準（避免報表重複計數）。"""
        meme, *_ = make_seed_meme()
        repo.insert_meme(conn, meme)
        log = self._seed_log(conn, meme.meme_id)

        repo.insert_feedback(conn, FeedbackEvent(
            feedback_id=new_id("f"), query_id=log.query_id, meme_id=meme.meme_id,
            rank=1, rating="up",
        ))
        repo.insert_feedback(conn, FeedbackEvent(
            feedback_id=new_id("f"), query_id=log.query_id, meme_id=meme.meme_id,
            rank=1, rating="down", note="改主意了",
        ))

        events = repo.list_feedback(conn, query_id=log.query_id)
        assert len(events) == 1  # 不重複計數
        assert events[0].rating == "down"  # 最新一次為準
        assert events[0].note == "改主意了"

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
