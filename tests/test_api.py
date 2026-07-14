"""P2-4 契約測試：推薦 API（契約：docs/01 §5.2）。

以 stub anthropic client + fake embedder 注入，全程不需 API 金鑰。
"""

import base64

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from memeradar.api.app import Deps, create_app
from memeradar.matching.intent import IntentResult
from memeradar.matching.rerank import CandidateScore, RerankResult
from memeradar.matching.screenshot import ScreenshotParseResult
from memeradar.shared import repository as repo
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Embedding, Meme, MemeAnnotation, new_id
from memeradar.understanding.annotator import AnnotationResult
from memeradar.understanding.opponent import OpponentMeme

SIGNATURE = "fake-embed@v1|doc-v1"

INTENT_PAYLOAD = IntentResult(
    summary="同事指責使用者報告遲交",
    punchline="你到底行不行",
    other_party_emotion=["憤怒"],
    conversation_type="指責",
    sensitive=False,
    low_context=False,
    language="zh-TW",
    strategies=[{"name": "滑跪求饒", "rationale": "對方在氣頭上", "query": "犯錯道歉求饒"}],
)

# 候選編號 1..10 遞減給分；幻覺編號會被 rerank 層忽略，故對任何 pool 大小皆安全
RERANK_PAYLOAD = RerankResult(
    scores=[
        CandidateScore(candidate_id=i, score=95 - i * 10, reason=f"理由{i}") for i in range(1, 11)
    ]
)

ANNOTATION_PAYLOAD = AnnotationResult(
    is_meme=True,
    nsfw=False,
    ocr_text="上傳的梗",
    description="測試上傳",
    characters=[],
    franchise="海綿寶寶",
    template_name=None,
    emotions=["得意"],
    usage_hints=["炫耀成果時使用"],
    categories=["卡通動畫"],
    confidence=0.9,
)


class StubVlm:
    """標註 / 截圖 / 對方梗圖 stub（NVIDIA VLM 介面）：依 task 回對應 JSON。"""

    model = "qwen/test"

    def __init__(self, refuse: set[str] = frozenset()):
        self.refuse = set(refuse)

    def annotate(self, image_b64, media_type, system, user_text, *, task="annotate",
                 log=None, **kwargs):
        if log is not None:  # 比照 NvidiaVlm：log 記錄「實際使用的模型」（含 model 覆寫）
            log({"key_id": "…test", "model": kwargs.get("model") or self.model,
                 "task": task, "meme_id": kwargs.get("meme_id"), "status": "ok",
                 "latency_ms": 100, "prompt_tokens": 100, "completion_tokens": 50, "error": None})
        if task in self.refuse:
            return "抱歉，我無法處理。"  # 非 JSON → call_structured 回 None → 端點轉 422
        if task == "screenshot":
            return SCREENSHOT_PAYLOAD.model_dump_json()
        if task == "opponent":
            return OPPONENT_PAYLOAD.model_dump_json()
        return ANNOTATION_PAYLOAD.model_dump_json()

    def chat(self, system, user_text, *, task="text", log=None, **kwargs):
        if log is not None:  # 比照 NvidiaVlm：記錄實際使用的模型（含覆寫）
            log({"key_id": "…test", "model": kwargs.get("model") or self.model,
                 "task": task, "meme_id": None, "status": "ok",
                 "latency_ms": 50, "prompt_tokens": 80, "completion_tokens": 40, "error": None})
        if task in self.refuse:
            return "抱歉，我無法處理。"
        if task == "intent":
            return INTENT_PAYLOAD.model_dump_json()
        if task == "rerank":
            return RERANK_PAYLOAD.model_dump_json()
        raise AssertionError(f"StubVlm.chat 未預期的 task: {task}")

OPPONENT_PAYLOAD = OpponentMeme(
    ocr_text="我就爛",
    description="海綿寶寶攤手，一臉理直氣壯",
    emotions=["擺爛", "理直氣壯"],
    read="對方擺爛耍賴，擺明不想被說服",
)

SCREENSHOT_PAYLOAD = ScreenshotParseResult(
    app_guess="line",
    conversation=[
        {"speaker": "other", "text": "你報告又遲交了！", "confidence": 0.98},
        {"speaker": "me", "text": "抱歉抱歉", "confidence": 0.95},
    ],
    warnings=["最上方一則訊息被裁切，未納入"],
)


class StubResponse:
    def __init__(self, parsed_output, stop_reason="end_turn"):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason


class DualStubClient:
    """依 output_format 回對應結果；可指定某類請求拒答。"""

    def __init__(self, refuse: set[str] = frozenset()):
        self.refuse = refuse
        outer = self

        class _Messages:
            def parse(self, **kwargs):
                fmt = kwargs["output_format"]
                if fmt is IntentResult:
                    if "intent" in outer.refuse:
                        return StubResponse(None, "refusal")
                    return StubResponse(INTENT_PAYLOAD)
                if fmt is RerankResult:
                    if "rerank" in outer.refuse:
                        return StubResponse(None, "refusal")
                    return StubResponse(RERANK_PAYLOAD)
                if fmt is ScreenshotParseResult:
                    if "screenshot" in outer.refuse:
                        return StubResponse(None, "refusal")
                    return StubResponse(SCREENSHOT_PAYLOAD)
                if fmt is AnnotationResult:
                    return StubResponse(ANNOTATION_PAYLOAD)
                if fmt is OpponentMeme:
                    if "opponent" in outer.refuse:
                        return StubResponse(None, "refusal")
                    return StubResponse(OPPONENT_PAYLOAD)
                raise AssertionError(f"未知 output_format: {fmt}")

        self.messages = _Messages()


class FakeEmbedder:
    model_id = "fake-embed@v1"

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def seed_meme(conn, data_dir, *, franchise="海綿寶寶", ocr="我就爛", vector=(1.0, 0.0)) -> Meme:
    meme_id = new_id("m")
    image_rel = f"images/{meme_id}.png"
    (data_dir / "images").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), (200, 30, 30)).save(data_dir / image_rel)
    meme = Meme(meme_id=meme_id, image_uri=image_rel, sha256=new_id("h").ljust(64, "0")[:64])
    repo.insert_meme(conn, meme)
    repo.upsert_annotation(
        conn,
        MemeAnnotation(
            meme_id=meme_id,
            model_version="labeler-v1@claude-sonnet-5",
            ocr_text=ocr,
            description="測試",
            franchise=franchise,
            emotions=["擺爛"],
            usage_hints=["被指責時自嘲"],
            categories=["卡通動畫"],
            confidence=0.9,
        ),
    )
    repo.add_embedding(
        conn,
        Embedding(meme_id=meme_id, kind="text_retrieval", model=SIGNATURE, vector=list(vector)),
    )
    return meme


@pytest.fixture
def env(tmp_path):
    """回傳 (client, conn, memes)。"""
    db_path = tmp_path / "db.sqlite3"
    conn = connect(db_path)
    migrate(conn)
    memes = [
        seed_meme(conn, tmp_path, franchise="海綿寶寶", ocr="我就爛"),
        seed_meme(conn, tmp_path, franchise="甄嬛傳", ocr="臣妾做不到啊"),
        seed_meme(conn, tmp_path, franchise="海綿寶寶", ocr="太神啦", vector=(0.9, 0.4358899)),
    ]
    deps = Deps(
        client=DualStubClient(),
        vlm=StubVlm(),
        embedder=FakeEmbedder(),
        db_path=db_path,
        data_dir=tmp_path,
    )
    app = create_app(deps)
    yield TestClient(app), conn, memes, deps
    conn.close()


BASE_REQUEST = {
    "input_type": "text",
    "conversation": [
        {"speaker": "other", "text": "你報告又遲交了！"},
        {"speaker": "me", "text": "抱歉抱歉"},
    ],
    "filters": {"franchises": [], "categories": [], "exclude_nsfw": True},
    "params": {"top_n": 3, "candidate_k": 50, "min_similarity": 0.1, "diversity": 0.0},
}


class TestRecommendContract:
    def test_response_shape_per_contract(self, env):
        client, *_ = env
        resp = client.post("/recommend", json=BASE_REQUEST)
        assert resp.status_code == 200
        body = resp.json()

        assert body["query_id"].startswith("q_")
        assert body["intent"]["summary"]
        assert body["intent"]["strategies"][0]["name"] == "滑跪求饒"

        assert len(body["results"]) == 3
        first = body["results"][0]
        assert set(first) >= {
            "meme_id", "image_url", "rank", "scores",
            "matched_strategy", "matched_tags", "reason",
        }
        assert first["rank"] == 1
        assert set(first["scores"]) == {"vector", "rerank", "final"}
        assert first["image_url"] == f"/memes/{first['meme_id']}/image"
        assert first["reason"] == "理由1"

        debug = body["debug"]
        assert debug["queries"] == ["犯錯道歉求饒"]
        assert isinstance(debug["candidates"], list)
        assert "timings_ms" in debug and "intent" in debug["timings_ms"]

    def test_recommendation_logged(self, env):
        client, conn, *_ = env
        query_id = client.post(
            "/recommend", json={**BASE_REQUEST, "client_id": "c_test123"}
        ).json()["query_id"]

        log = repo.get_recommendation_log(conn, query_id)
        assert log is not None
        assert log.params_snapshot["params"]["top_n"] == 3
        assert log.intent_result["punchline"] == "你到底行不行"
        assert len(log.final_results) == 3
        assert isinstance(log.latency_ms, int)
        # 分階段耗時持續落庫（延遲監控用）
        assert log.timings is not None
        assert {"intent", "retrieval", "rerank", "total"} <= set(log.timings)
        # 供未來優化的上下文：輸入類型、匿名 client id、產生推薦的 LLM 模型
        assert log.input_type == "text"
        assert log.client_id == "c_test123"
        assert set(log.params_snapshot["models"]) == {"intent", "rerank"}

    def test_franchise_filter_applied(self, env):
        client, conn, memes, _ = env
        request = {**BASE_REQUEST, "filters": {**BASE_REQUEST["filters"], "franchises": ["甄嬛傳"]}}
        body = client.post("/recommend", json=request).json()
        assert len(body["results"]) == 1
        assert body["results"][0]["meme_id"] == memes[1].meme_id

    def test_empty_results_still_200(self, env):
        client, *_ = env
        request = {**BASE_REQUEST, "filters": {**BASE_REQUEST["filters"], "franchises": ["獵人"]}}
        body = client.post("/recommend", json=request).json()
        assert body["results"] == []
        assert body["debug"]["per_strategy_hits"] == {"滑跪求饒": 0}

    def test_screenshot_input_parses_then_recommends(self, env):
        client, conn, *_ = env
        png_b64 = base64.standard_b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()
        resp = client.post(
            "/recommend",
            json={**BASE_REQUEST, "input_type": "screenshot", "conversation": [], "image": png_b64},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) > 0
        assert body["debug"]["screenshot_parse"]["app_guess"] == "line"
        # 截圖不落庫：log 只存解析後的文字對話
        log = repo.get_recommendation_log(conn, body["query_id"])
        assert log.conversation == [
            {"speaker": "other", "text": "你報告又遲交了！"},
            {"speaker": "me", "text": "抱歉抱歉"},
        ]

    def test_meme_battle_understands_opponent_then_recommends(self, env):
        client, conn, *_ = env
        png_b64 = base64.standard_b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()
        resp = client.post(
            "/recommend",
            json={**BASE_REQUEST, "input_type": "meme_battle",
                  "conversation": [], "image": png_b64},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) > 0
        # 對方梗圖理解入 debug；合成的對話輪次寫進 log（不落庫存原圖）
        assert body["debug"]["opponent_meme"]["ocr_text"] == "我就爛"
        log = repo.get_recommendation_log(conn, body["query_id"])
        assert len(log.conversation) == 1
        assert log.conversation[0]["speaker"] == "other"
        assert "梗圖" in log.conversation[0]["text"]

    def test_meme_battle_missing_image_422(self, env):
        client, *_ = env
        resp = client.post(
            "/recommend", json={**BASE_REQUEST, "input_type": "meme_battle", "conversation": []}
        )
        assert resp.status_code == 422

    def test_meme_battle_refusal_422(self, env):
        client, _conn, _memes, deps = env
        deps.vlm.refuse = {"opponent"}
        png_b64 = base64.standard_b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()
        resp = client.post(
            "/recommend",
            json={**BASE_REQUEST, "input_type": "meme_battle",
                  "conversation": [], "image": png_b64},
        )
        assert resp.status_code == 422

    def test_screenshot_missing_image_422(self, env):
        client, *_ = env
        resp = client.post(
            "/recommend", json={**BASE_REQUEST, "input_type": "screenshot", "conversation": []}
        )
        assert resp.status_code == 422

    def test_screenshot_invalid_base64_422(self, env):
        client, *_ = env
        resp = client.post(
            "/recommend",
            json={**BASE_REQUEST, "input_type": "screenshot", "conversation": [], "image": "@@@"},
        )
        assert resp.status_code == 422

    def test_empty_conversation_422(self, env):
        client, *_ = env
        resp = client.post("/recommend", json={**BASE_REQUEST, "conversation": []})
        assert resp.status_code == 422

    def test_intent_refusal_422(self, env):
        client, conn, memes, deps = env
        deps.vlm.refuse = {"intent"}
        resp = client.post("/recommend", json=BASE_REQUEST)
        assert resp.status_code == 422
        assert "安全" in resp.json()["detail"]

    def test_rerank_refusal_falls_back_to_vector_order(self, env):
        client, conn, memes, deps = env
        deps.vlm.refuse = {"rerank"}
        resp = client.post("/recommend", json=BASE_REQUEST)
        assert resp.status_code == 200
        body = resp.json()
        assert body["debug"]["rerank_fallback"] is True
        sims = [r["scores"]["vector"] for r in body["results"]]
        assert sims == sorted(sims, reverse=True)  # 退回純向量排序


class TestParseScreenshotEndpoint:
    def test_returns_parse_result_for_console_editing(self, env):
        client, *_ = env
        png_b64 = base64.standard_b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()
        resp = client.post("/parse-screenshot", json={"image": png_b64})
        assert resp.status_code == 200
        body = resp.json()
        assert body["app_guess"] == "line"
        assert body["conversation"][0] == {
            "speaker": "other",
            "text": "你報告又遲交了！",
            "confidence": 0.98,
        }
        assert body["warnings"] == ["最上方一則訊息被裁切，未納入"]

    def test_invalid_base64_422(self, env):
        client, *_ = env
        assert client.post("/parse-screenshot", json={"image": "@@@"}).status_code == 422

    def test_parse_refusal_422(self, env):
        client, _, _, deps = env
        deps.vlm.refuse = {"screenshot"}
        png_b64 = base64.standard_b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()
        resp = client.post("/parse-screenshot", json={"image": png_b64})
        assert resp.status_code == 422


class TestFeedback:
    def test_roundtrip(self, env):
        client, conn, *_ = env
        body = client.post("/recommend", json=BASE_REQUEST).json()
        target = body["results"][0]

        resp = client.post(
            "/feedback",
            json={
                "query_id": body["query_id"],
                "meme_id": target["meme_id"],
                "rank": target["rank"],
                "rating": "up",
                "note": "圖對理由也對",
            },
        )
        assert resp.status_code == 200

        events = repo.list_feedback(conn, query_id=body["query_id"])
        assert len(events) == 1
        assert events[0].rating == "up"

    def test_unknown_query_404(self, env):
        client, _, memes, _ = env
        resp = client.post(
            "/feedback",
            json={
                "query_id": "q_nope",
                "meme_id": memes[0].meme_id,
                "rank": 1,
                "rating": "down",
            },
        )
        assert resp.status_code == 404

    def test_invalid_rating_422(self, env):
        client, *_ = env
        resp = client.post(
            "/feedback",
            json={"query_id": "q_x", "meme_id": "m_x", "rank": 1, "rating": "meh"},
        )
        assert resp.status_code == 422


class TestEventsAndLeaderboard:
    def test_log_download_event(self, env):
        client, conn, memes, _ = env
        resp = client.post("/events", json={
            "event_type": "download", "client_id": "c1",
            "meme_id": memes[0].meme_id, "meta": {"src": "mobile"},
        })
        assert resp.status_code == 202
        n = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
        assert n == 1

    def test_unknown_event_type_422(self, env):
        client, *_ = env
        assert client.post("/events", json={"event_type": "hack"}).status_code == 422

    def test_bad_meme_id_is_swallowed(self, env):
        # best-effort：meme_id 不存在也不該讓前台收到錯誤
        client, *_ = env
        resp = client.post("/events", json={"event_type": "download", "meme_id": "m_nope"})
        assert resp.status_code == 202

    def test_leaderboard_ranks_by_likes_and_downloads(self, env):
        client, _, memes, _ = env
        body = client.post("/recommend", json=BASE_REQUEST).json()
        top = body["results"][0]
        client.post("/feedback", json={
            "query_id": body["query_id"], "meme_id": top["meme_id"],
            "rank": top["rank"], "rating": "up",
        })
        client.post("/events", json={"event_type": "download", "meme_id": top["meme_id"]})

        board = client.get("/leaderboard").json()
        assert board[0]["meme_id"] == top["meme_id"]
        assert board[0]["likes"] == 1 and board[0]["downloads"] == 1
        assert board[0]["score"] == 4
        assert board[0]["image_url"] == f"/memes/{top['meme_id']}/image"

    def test_leaderboard_empty_with_no_engagement(self, env):
        client, *_ = env
        assert client.get("/leaderboard").json() == []


class TestHistory:
    def test_list_with_feedback_counts_and_detail_for_replay(self, env):
        client, *_ = env
        body = client.post("/recommend", json=BASE_REQUEST).json()
        client.post("/feedback", json={
            "query_id": body["query_id"], "meme_id": body["results"][0]["meme_id"],
            "rank": 1, "rating": "up",
        })

        history = client.get("/history").json()
        assert history[0]["query_id"] == body["query_id"]
        assert history[0]["ups"] == 1 and history[0]["downs"] == 0
        assert history[0]["result_count"] == 3

        detail = client.get(f"/history/{body['query_id']}").json()
        assert detail["conversation"] == BASE_REQUEST["conversation"]  # 重放輸入
        assert detail["params_snapshot"]["params"]["top_n"] == 3
        assert detail["intent_result"]["punchline"] == "你到底行不行"

    def test_unknown_query_404(self, env):
        client, *_ = env
        assert client.get("/history/q_nope").status_code == 404


class TestLibrary:
    def test_list_and_filter(self, env):
        client, _, memes, _ = env
        rows = client.get("/memes").json()
        assert {r["meme_id"] for r in rows} >= {m.meme_id for m in memes}
        assert all("image_url" in r for r in rows)

        rows = client.get("/memes", params={"franchise": "甄嬛傳"}).json()
        assert [r["meme_id"] for r in rows] == [memes[1].meme_id]
        assert rows[0]["annotation"]["ocr_text"] == "臣妾做不到啊"

    def test_upload_imports_annotates_and_embeds(self, env):
        client, conn, *_ = env
        buffer = __import__("io").BytesIO()
        Image.new("RGB", (300, 300), (10, 200, 90)).save(buffer, format="PNG")
        png_b64 = base64.standard_b64encode(buffer.getvalue()).decode()

        resp = client.post("/memes", json={"image": png_b64, "title_hint": "手動上傳測試"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "imported"
        assert body["annotation"]["ocr_text"] == "上傳的梗"
        # 已標註 + 已向量化 → 立即可檢索
        embeddings = repo.get_embeddings(conn, body["meme_id"], kind="text_retrieval")
        assert len(embeddings) == 1
        assert embeddings[0].model == SIGNATURE

        dup = client.post("/memes", json={"image": png_b64})
        assert dup.status_code == 409

    def test_upload_invalid_base64_422(self, env):
        client, *_ = env
        assert client.post("/memes", json={"image": "@@@"}).status_code == 422

    def test_public_routes_open_even_when_admin_auth_enabled(self, env):
        client, _c, memes, deps = env
        deps.admin_username, deps.admin_password = "boss", "pw"
        assert client.get("/health").status_code == 200
        assert client.get("/meta").status_code == 200
        assert client.post("/recommend", json=BASE_REQUEST).status_code == 200
        assert client.get(f"/memes/{memes[0].meme_id}/image").status_code == 200  # 圖片公開

    def test_admin_route_requires_credentials(self, env):
        client, _c, _m, deps = env
        deps.admin_username, deps.admin_password = "boss", "pw"
        assert client.get("/memes").status_code == 401  # 列表=後台
        assert client.get("/memes", auth=("boss", "wrong")).status_code == 401
        assert client.get("/memes", auth=("boss", "pw")).status_code == 200

    def test_no_gate_when_creds_unset(self, env):
        client, *_ = env  # 帳密空 → 不設防（dev/測試）
        assert client.get("/memes").status_code == 200

    def test_vlm_models_lists_candidates_and_default(self, env):
        client, *_ = env
        body = client.get("/vlm/models").json()
        assert "qwen/qwen3.5-122b-a10b" in body["models"]
        assert body["default"] == "qwen/test"  # StubVlm.model

    def test_upload_passes_chosen_model(self, env):
        client, conn, *_ = env
        buffer = __import__("io").BytesIO()
        Image.new("RGB", (300, 300), (10, 200, 90)).save(buffer, format="PNG")
        png_b64 = base64.standard_b64encode(buffer.getvalue()).decode()
        resp = client.post("/memes", json={"image": png_b64, "model": "meta/llama-4-maverick"})
        assert resp.status_code == 200
        # 用量紀錄該筆的 model 應為選定模型
        row = conn.execute(
            "SELECT model FROM vlm_calls ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert row["model"] == "meta/llama-4-maverick"

    def test_upload_corrupt_image_422(self, env):
        client, *_ = env
        bad = base64.standard_b64encode(b"not an image").decode()
        assert client.post("/memes", json={"image": bad}).status_code == 422


class TestAnnotationReviewQueue:
    def _seed_pending(self, env):
        client, conn, memes, deps = env
        meme = seed_meme(conn, deps.data_dir, franchise="海綿寶寶", ocr="待審文字")
        repo.set_status(conn, meme.meme_id, "pending_review")
        return client, conn, meme

    def test_approve_with_patch_updates_and_reembeds(self, env):
        client, conn, meme = self._seed_pending(env)
        resp = client.post(
            f"/review/annotations/{meme.meme_id}",
            json={
                "action": "approve",
                "patch": {
                    "ocr_text": "人工修正後的文字",
                    "emotions": ["嘲諷"],
                    "usage_hints": ["修正後的用途"],
                },
            },
        )
        assert resp.status_code == 200
        assert repo.get_meme(conn, meme.meme_id).status == "active"
        ann = repo.get_annotation(conn, meme.meme_id)
        assert ann.ocr_text == "人工修正後的文字"
        assert ann.emotions == ["嘲諷"]
        assert ann.franchise == "海綿寶寶"  # 未修補欄位保留
        assert ann.model_version.endswith("+human")  # 人工審核溯源
        assert repo.get_embeddings(conn, meme.meme_id, kind="text_retrieval")  # 已重建向量

    def test_approve_without_patch(self, env):
        client, conn, meme = self._seed_pending(env)
        resp = client.post(f"/review/annotations/{meme.meme_id}", json={"action": "approve"})
        assert resp.status_code == 200
        assert repo.get_meme(conn, meme.meme_id).status == "active"

    def test_remove_action(self, env):
        client, conn, meme = self._seed_pending(env)
        resp = client.post(f"/review/annotations/{meme.meme_id}", json={"action": "remove"})
        assert resp.status_code == 200
        assert repo.get_meme(conn, meme.meme_id).status == "removed"

    def test_unknown_meme_404_and_invalid_action_422(self, env):
        client, *_ = env
        assert client.post(
            "/review/annotations/m_nope", json={"action": "approve"}
        ).status_code == 404
        assert client.post(
            "/review/annotations/m_nope", json={"action": "yolo"}
        ).status_code == 422

    def test_patch_rejects_off_taxonomy_emotion(self, env):
        client, conn, meme = self._seed_pending(env)
        resp = client.post(
            f"/review/annotations/{meme.meme_id}",
            json={"action": "approve", "patch": {"emotions": ["超展開"]}},
        )
        assert resp.status_code == 422


class TestDedupReviewQueue:
    def _seed_pair(self, env):
        client, conn, memes, deps = env
        dup = seed_meme(conn, deps.data_dir, franchise="海綿寶寶", ocr="重複疑似")
        repo.add_dedup_review(
            conn, meme_id=dup.meme_id, matched_meme_id=memes[0].meme_id,
            layer="clip", score=0.95,
        )
        return client, conn, dup, memes[0]

    def test_list_pairs_with_images(self, env):
        client, conn, dup, kept = self._seed_pair(env)
        rows = client.get("/review/dedup").json()
        assert len(rows) == 1
        row = rows[0]
        assert row["meme"]["meme_id"] == dup.meme_id
        assert row["meme"]["image_url"] == f"/memes/{dup.meme_id}/image"
        assert row["matched"]["meme_id"] == kept.meme_id
        assert row["matched"]["ocr_text"] == "我就爛"
        assert row["score"] == pytest.approx(0.95)

    def test_resolve_merged(self, env):
        client, conn, dup, kept = self._seed_pair(env)
        review_id = client.get("/review/dedup").json()[0]["review_id"]

        resp = client.post(f"/review/dedup/{review_id}", json={"resolution": "merged"})

        assert resp.status_code == 200
        assert repo.get_meme(conn, dup.meme_id).status == "removed"
        assert repo.get_meme(conn, kept.meme_id).hotness > 0  # 熱度轉移
        assert repo.list_dedup_reviews(conn, resolution="merged")
        assert client.get("/review/dedup").json() == []  # 佇列清空

    def test_resolve_distinct(self, env):
        client, conn, dup, kept = self._seed_pair(env)
        review_id = client.get("/review/dedup").json()[0]["review_id"]

        resp = client.post(f"/review/dedup/{review_id}", json={"resolution": "distinct"})

        assert resp.status_code == 200
        assert repo.get_meme(conn, dup.meme_id).status == "active"  # 保留
        assert repo.list_dedup_reviews(conn, resolution="distinct")

    def test_unknown_review_404(self, env):
        client, *_ = env
        assert client.post(
            "/review/dedup/dr_nope", json={"resolution": "merged"}
        ).status_code == 404


class TestFeedbackReportEndpoint:
    def test_report_shape(self, env):
        client, *_ = env
        body = client.post("/recommend", json=BASE_REQUEST).json()
        client.post("/feedback", json={
            "query_id": body["query_id"], "meme_id": body["results"][0]["meme_id"],
            "rank": 1, "rating": "down", "note": "梗太老了",
        })

        report = client.get("/report/feedback").json()

        assert report["totals"]["total"] == 1
        assert report["totals"]["up_rate"] == 0.0
        assert report["by_strategy"][0]["key"] == "滑跪求饒"
        assert report["down_notes"][0]["note"] == "梗太老了"
        assert report["daily"][0]["downs"] == 1


class TestImagesAndMeta:
    def test_image_served(self, env):
        client, _, memes, _ = env
        resp = client.get(f"/memes/{memes[0].meme_id}/image")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_unknown_image_404(self, env):
        client, *_ = env
        assert client.get("/memes/m_nope/image").status_code == 404

    def test_meta_lists_franchises_with_counts(self, env):
        client, *_ = env
        body = client.get("/meta").json()
        franchises = {f["name"]: f["count"] for f in body["franchises"]}
        assert franchises == {"海綿寶寶": 2, "甄嬛傳": 1}
        # 分類為開放集：來自庫內實際出現的值（非整份 taxonomy），新分類自動現身
        assert "卡通動畫" in body["categories"]  # 種子圖都標這類
        assert "綜藝" not in body["categories"]  # taxonomy 有、但庫內無 → 不列
        assert "擺爛" in body["emotions"]  # 複核頁標籤編修的字典來源（情緒仍為封閉集）


class TestAsyncTasks:
    """非同步任務：POST /tasks 送出 → 背景執行 → GET /tasks/{id} 查進度/結果。"""

    def _sync(self, deps):
        # 測試用：背景執行改為同步（送出即跑完），免除輪詢時序
        deps.run_async = lambda fn: fn()

    def test_submit_returns_task_id_and_pending_shape(self, env):
        client, _, _, deps = env
        deps.run_async = lambda fn: None  # 不執行，觀察剛送出的 pending 狀態
        resp = client.post("/tasks", json={**BASE_REQUEST, "client_id": "c_abc"})
        assert resp.status_code == 202
        body = resp.json()
        assert body["task_id"].startswith("task_")
        assert body["status"] == "pending"

    def test_task_executes_and_stores_result(self, env):
        client, _, _, deps = env
        self._sync(deps)
        task_id = client.post("/tasks", json={**BASE_REQUEST, "client_id": "c_abc"}).json()[
            "task_id"
        ]
        got = client.get(f"/tasks/{task_id}")
        assert got.status_code == 200
        body = got.json()
        assert body["status"] == "done"
        assert body["result"]["query_id"].startswith("q_")
        assert len(body["result"]["results"]) == 3

    def test_history_lists_client_tasks_without_full_result(self, env):
        client, _, _, deps = env
        self._sync(deps)
        for _ in range(2):
            client.post("/tasks", json={**BASE_REQUEST, "client_id": "c_me"})
        client.post("/tasks", json={**BASE_REQUEST, "client_id": "c_other"})

        history = client.get("/tasks", params={"client_id": "c_me"}).json()
        assert len(history) == 2
        assert all(t["status"] == "done" for t in history)
        assert all(t["has_result"] for t in history)
        assert "result" not in history[0]  # 列表精簡，不夾帶完整結果

    def test_missing_task_returns_404(self, env):
        client, *_ = env
        assert client.get("/tasks/task_nope").status_code == 404

    def test_refused_intent_marks_task_error(self, env):
        client, _, _, deps = env
        deps.vlm = StubVlm(refuse={"intent"})
        self._sync(deps)
        task_id = client.post("/tasks", json={**BASE_REQUEST, "client_id": "c_me"}).json()[
            "task_id"
        ]
        body = client.get(f"/tasks/{task_id}").json()
        assert body["status"] == "error"
        assert body["error"]


class TestModelSettingsEndpoints:
    """後台設定頁：各任務模型可調 + 用量檢視。"""

    def test_get_lists_five_tasks_with_available_and_default(self, env):
        client, *_ = env
        body = client.get("/settings/models").json()
        keys = {t["key"] for t in body["tasks"]}
        assert keys == {"annotation", "intent", "rerank", "screenshot", "opponent"}
        assert all(t["current"] is None for t in body["tasks"])  # 預設未覆寫
        assert "qwen/qwen3.5-122b-a10b" in body["available"]
        assert body["default"] == "qwen/test"  # StubVlm.model

    def test_put_persists_and_reflects_in_get(self, env):
        client, *_ = env
        client.put("/settings/models", json={"models": {"intent": "qwen/qwen3.5-397b-a17b"}})
        body = client.get("/settings/models").json()
        by_key = {t["key"]: t["current"] for t in body["tasks"]}
        assert by_key["intent"] == "qwen/qwen3.5-397b-a17b"
        assert by_key["rerank"] is None

    def test_put_ignores_unknown_task_keys(self, env):
        client, *_ = env
        client.put(
            "/settings/models",
            json={"models": {"bogus": "x", "rerank": "qwen/qwen3.5-397b-a17b"}},
        )
        by_key = {t["key"]: t["current"] for t in client.get("/settings/models").json()["tasks"]}
        assert by_key["rerank"] == "qwen/qwen3.5-397b-a17b"
        assert "bogus" not in by_key

    def test_configured_model_flows_into_task_execution(self, env):
        client, conn, _, deps = env
        # 設定覆寫的模型應真的傳進意圖呼叫，並落進 vlm_calls（用量表）
        client.put("/settings/models", json={"models": {"intent": "qwen/qwen3.5-397b-a17b"}})
        deps.run_async = lambda fn: fn()
        client.post("/tasks", json={**BASE_REQUEST, "client_id": "c_me"})
        rows = conn.execute("SELECT model FROM vlm_calls WHERE task='intent'").fetchall()
        assert rows and rows[0]["model"] == "qwen/qwen3.5-397b-a17b"

    def test_usage_endpoint_reports_calls_after_a_task(self, env):
        client, _, _, deps = env
        deps.run_async = lambda fn: fn()
        client.post("/tasks", json={**BASE_REQUEST, "client_id": "c_me"})
        usage = client.get("/vlm/usage").json()
        assert any(row["n"] >= 1 for row in usage)  # 推薦路徑呼叫已被記錄

    def test_settings_endpoints_are_admin_gated(self, tmp_path):
        db_path = tmp_path / "db.sqlite3"
        connect(db_path).close()
        deps = Deps(
            client=DualStubClient(), vlm=StubVlm(), embedder=FakeEmbedder(),
            db_path=db_path, data_dir=tmp_path,
            admin_username="boss", admin_password="secret",
        )
        client = TestClient(create_app(deps))
        assert client.get("/settings/models").status_code == 401
        assert client.get("/vlm/usage").status_code == 401


class TestProdHardening:
    def test_orphan_tasks_aborted_on_startup(self, tmp_path):
        # 先塞一個 running 任務，建 app 時的啟動清理應把它標成 error（模擬重啟）
        conn = connect(tmp_path / "x")  # 走 DATABASE_URL（測試容器）
        repo.create_task(conn, "orphan", client_id="c", input_type="text", label="x")
        repo.set_task_status(conn, "orphan", "running")
        deps = Deps(client=DualStubClient(), vlm=StubVlm(), embedder=FakeEmbedder(),
                    db_path=tmp_path, data_dir=tmp_path)
        create_app(deps)
        assert repo.get_task(conn, "orphan")["status"] == "error"
        conn.close()

    def test_cors_headers_when_configured(self, tmp_path):
        deps = Deps(client=DualStubClient(), vlm=StubVlm(), embedder=FakeEmbedder(),
                    db_path=tmp_path, data_dir=tmp_path,
                    cors_origins=("https://app.example.com",))
        client = TestClient(create_app(deps))
        r = client.get("/health", headers={"Origin": "https://app.example.com"})
        assert r.headers.get("access-control-allow-origin") == "https://app.example.com"

    def test_no_cors_headers_when_unconfigured(self, env):
        client, *_ = env  # cors_origins 預設空
        r = client.get("/health", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in r.headers

    def test_rate_limit_returns_429_after_max(self, tmp_path):
        from memeradar.api.ratelimit import RateLimiter

        deps = Deps(client=DualStubClient(), vlm=StubVlm(), embedder=FakeEmbedder(),
                    db_path=tmp_path, data_dir=tmp_path,
                    rate_limiter=RateLimiter(2, 60), run_async=lambda fn: None)
        client = TestClient(create_app(deps))
        body = {**BASE_REQUEST, "client_id": "c"}
        assert client.post("/tasks", json=body).status_code == 202
        assert client.post("/tasks", json=body).status_code == 202
        assert client.post("/tasks", json=body).status_code == 429  # 第 3 次超限


class TestAsyncTasksRealExecutor:
    """不注入 run_async：驗證真正的 ThreadPoolExecutor 背景執行 + 自開連線可行。"""

    def test_background_thread_completes_task(self, env):
        import time

        client, *_ = env  # deps.run_async 維持 None → 走內建 thread pool
        task_id = client.post("/tasks", json={**BASE_REQUEST, "client_id": "c_me"}).json()[
            "task_id"
        ]
        deadline = time.time() + 5
        status = None
        while time.time() < deadline:
            body = client.get(f"/tasks/{task_id}").json()
            status = body["status"]
            if status in ("done", "error"):
                break
            time.sleep(0.05)
        assert status == "done"
        assert body["result"]["query_id"].startswith("q_")


class TestAsyncTasksArePublic:
    """前台手機 client 會用 /tasks，故須繞過後台登入。"""

    def test_tasks_endpoints_bypass_admin_gate(self, tmp_path):
        db_path = tmp_path / "db.sqlite3"
        conn = connect(db_path)
        migrate(conn)
        conn.close()
        deps = Deps(
            client=DualStubClient(), vlm=StubVlm(), embedder=FakeEmbedder(),
            db_path=db_path, data_dir=tmp_path,
            admin_username="boss", admin_password="secret",
        )
        deps.run_async = lambda fn: fn()
        client = TestClient(create_app(deps))

        # 無帳密也能送出 / 查詢（前台公開）
        submit = client.post("/tasks", json={**BASE_REQUEST, "client_id": "c_me"})
        assert submit.status_code == 202
        task_id = submit.json()["task_id"]
        assert client.get(f"/tasks/{task_id}").status_code == 200
        assert client.get("/tasks", params={"client_id": "c_me"}).status_code == 200
        # 後台路徑仍需登入
        assert client.get("/memes").status_code == 401
