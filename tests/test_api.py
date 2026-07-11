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
        query_id = client.post("/recommend", json=BASE_REQUEST).json()["query_id"]

        log = repo.get_recommendation_log(conn, query_id)
        assert log is not None
        assert log.params_snapshot["params"]["top_n"] == 3
        assert log.intent_result["punchline"] == "你到底行不行"
        assert len(log.final_results) == 3
        assert isinstance(log.latency_ms, int)

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
        deps.client.refuse = {"intent"}
        resp = client.post("/recommend", json=BASE_REQUEST)
        assert resp.status_code == 422
        assert "安全" in resp.json()["detail"]

    def test_rerank_refusal_falls_back_to_vector_order(self, env):
        client, conn, memes, deps = env
        deps.client.refuse = {"rerank"}
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
        deps.client.refuse = {"screenshot"}
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
        assert "卡通動畫" in body["categories"]
