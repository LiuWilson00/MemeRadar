"""P1-4 測試：檢索文件組裝 + embedding 介面（規格：docs/03 §3）。

驗收對應：
- `embed()` 可切換後端 → 後端註冊表 + 不同 model_id 的向量可並存
- 模板與模型版本入庫 → embeddings.model 存「模型|模板版本」簽名
"""

import importlib.util
import time

import pytest

from memeradar.shared import repository as repo
from memeradar.shared.config import Settings, get_settings
from memeradar.shared.db import connect, migrate
from memeradar.shared.models import Meme, MemeAnnotation, new_id
from memeradar.understanding.embedding import (
    HOSTED_PROVIDERS,
    FallbackEmbedder,
    HostedBgeM3Embedder,
    build_hosted_embedder,
    embed_pending_memes,
    embedding_signature,
    get_embedder,
)
from memeradar.understanding.retrieval_doc import (
    RETRIEVAL_DOC_VERSION,
    build_retrieval_document,
)


def make_annotation(meme_id: str, **overrides) -> MemeAnnotation:
    fields = {
        "meme_id": meme_id,
        "model_version": "labeler-v1@claude-sonnet-5",
        "is_meme": True,
        "ocr_text": "我就爛",
        "description": "海綿寶寶攤手站立，表情理直氣壯",
        "characters": ["海綿寶寶"],
        "franchise": "海綿寶寶",
        "emotions": ["擺爛", "理直氣壯"],
        "usage_hints": ["被指責時理直氣壯自嘲", "表達躺平態度"],
        "categories": ["卡通動畫"],
        "confidence": 0.93,
    }
    fields.update(overrides)
    return MemeAnnotation(**fields)


class TestRetrievalDocument:
    def test_template_format_usage_hints_first(self):
        doc = build_retrieval_document(make_annotation("m_x"))
        assert doc == (
            "用途：被指責時理直氣壯自嘲\n"
            "用途：表達躺平態度\n"
            "情緒：擺爛、理直氣壯\n"
            "圖中文字：我就爛\n"
            "畫面：海綿寶寶攤手站立，表情理直氣壯\n"
            "角色：海綿寶寶；出處：海綿寶寶"
        )

    def test_empty_ocr_line_omitted(self):
        doc = build_retrieval_document(make_annotation("m_x", ocr_text=""))
        assert "圖中文字" not in doc

    def test_no_characters_and_no_franchise(self):
        doc = build_retrieval_document(
            make_annotation("m_x", characters=[], franchise=None)
        )
        assert doc.endswith("角色：無")
        assert "出處" not in doc

    def test_deterministic(self):
        ann = make_annotation("m_x")
        assert build_retrieval_document(ann) == build_retrieval_document(ann)


class FakeEmbedder:
    model_id = "fake-embed@v1"

    def __init__(self):
        self.seen: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[float(len(t)), 1.0, -0.5] for t in texts]


class AnotherFakeEmbedder(FakeEmbedder):
    model_id = "fake-embed@v2"


class TestEmbedderInterface:
    def test_signature_couples_model_and_doc_version(self):
        assert embedding_signature(FakeEmbedder()) == f"fake-embed@v1|{RETRIEVAL_DOC_VERSION}"

    def test_unknown_backend_raises_with_available_list(self):
        with pytest.raises(ValueError, match="bge-m3"):
            get_embedder("nope")

    def test_bge_m3_constructs_lazily(self):
        # 建構不載入模型（重依賴 lazy import），model_id 正確
        embedder = get_embedder("bge-m3")
        assert embedder.model_id == "bge-m3"

    @pytest.mark.skipif(
        importlib.util.find_spec("sentence_transformers") is not None,
        reason="已安裝 sentence-transformers，錯誤提示路徑不適用",
    )
    def test_bge_m3_embed_without_package_gives_install_hint(self):
        embedder = get_embedder("bge-m3")
        with pytest.raises(RuntimeError, match="local-embedding"):
            embedder.embed(["測試"])


class _FakeClient:
    """形狀比照 openai.OpenAI：``embeddings.create(model=, input=, **kw)``。

    真 client 的漂移只有真 key 打得出來（見 smoke script）；這裡只驗證我方送出的
    參數與供應商切換邏輯。
    """

    def __init__(self, *, base_url: str, api_key: str, fail: Exception | None = None):
        self.base_url = base_url
        self.api_key = api_key
        self.fail = fail
        self.calls: list[dict] = []
        outer = self

        class _Embeddings:
            def create(self, *, model, input, **kw):
                outer.calls.append({"model": model, "input": list(input), **kw})
                if outer.fail is not None:
                    raise outer.fail
                data = [
                    type("E", (), {"embedding": [float(len(s)), 1.0], "index": i})()
                    for i, s in enumerate(input)
                ]
                return type("R", (), {"data": data})()

        self.embeddings = _Embeddings()


def _factory(made: list, *, fail: Exception | None = None):
    def make(*, base_url, api_key, **_kw):
        client = _FakeClient(base_url=base_url, api_key=api_key, fail=fail)
        made.append(client)
        return client

    return make


class TestHostedProviders:
    def test_nvidia_sends_nim_only_params(self):
        made: list[_FakeClient] = []
        emb = HostedBgeM3Embedder(
            HOSTED_PROVIDERS["nvidia"], ["k1"], client_factory=_factory(made)
        )
        emb.embed(["hi"])

        call = made[0].calls[0]
        assert made[0].base_url == "https://integrate.api.nvidia.com/v1"
        assert call["model"] == "baai/bge-m3"
        assert call["extra_body"] == {"input_type": "passage", "truncate": "END"}

    def test_third_party_omits_nvidia_only_params(self):
        """input_type / truncate 是 NVIDIA NIM 擴充；別家會當成未知參數。"""
        made: list[_FakeClient] = []
        emb = HostedBgeM3Embedder(
            HOSTED_PROVIDERS["deepinfra"], ["k1"], client_factory=_factory(made)
        )
        emb.embed(["hi"])

        call = made[0].calls[0]
        assert made[0].base_url == "https://api.deepinfra.com/v1/openai"
        assert call["model"] == "BAAI/bge-m3"  # 各家 model id 大小寫不同
        assert "extra_body" not in call

    def test_gives_up_on_time_budget_instead_of_retrying_forever(self):
        """光數重試次數封不住上限：每次都可能耗到逾時。4 把 key 就能拖到 40s+，
        2026-07-29 事故就是這樣把啟動探針拖爆的。故以總時間預算硬性封頂。
        """
        calls = []

        class _SlowEmbeddings:
            def create(self, **_kw):
                calls.append(1)
                time.sleep(0.2)  # 模擬慢到逾時
                raise RuntimeError("timed out")

        def slow_factory(*, base_url, api_key, **_kw):
            return type("C", (), {"embeddings": _SlowEmbeddings()})()

        emb = HostedBgeM3Embedder(
            HOSTED_PROVIDERS["nvidia"], ["k1", "k2", "k3", "k4"],
            budget=0.3, client_factory=slow_factory,
        )
        t0 = time.monotonic()
        with pytest.raises(RuntimeError, match="nvidia"):
            emb.embed(["hi"])
        elapsed = time.monotonic() - t0

        assert len(calls) < 4, f"預算沒生效，試了 {len(calls)} 次"
        assert elapsed < 2, f"耗了 {elapsed:.1f}s，超過預算太多"

    def test_openrouter_is_available_as_bge_m3_provider(self):
        """2026-07-30：selfhost 之外唯一活著的 bge-m3 備援——NVIDIA 的自 7/27 起一直回 500。"""
        p = HOSTED_PROVIDERS["openrouter"]
        assert p.base_url == "https://openrouter.ai/api/v1"
        assert p.model == "baai/bge-m3"  # 1024 維，與既有索引相容
        assert p.nim_extras is False  # 不是 NIM，別送 NVIDIA 專屬參數
        assert p.keys_env == "openrouter_api_key"  # 與 VLM 共用同一把 key

    def test_selfhost_can_chain_with_openrouter(self):
        settings = Settings(
            _env_file=None,
            embedding_providers="selfhost,openrouter",
            embedding_selfhost_url="https://embed.example.com/v1",
            openrouter_api_key="or1",
        )
        embedder = build_hosted_embedder(settings)
        assert [e.provider.name for e in embedder.embedders] == ["selfhost", "openrouter"]
        assert embedder.model_id == "bge-m3"  # 簽名不變 → 既有 2000+ 向量照用

    def test_every_provider_keeps_the_same_signature(self):
        """換供應商不得改變入庫簽名，否則整庫向量要重建。"""
        for provider in HOSTED_PROVIDERS.values():
            emb = HostedBgeM3Embedder(provider, ["k"], client_factory=_factory([]))
            assert emb.model_id == "bge-m3"
            assert embedding_signature(emb) == f"bge-m3|{RETRIEVAL_DOC_VERSION}"


class TestFallbackEmbedder:
    def test_falls_over_to_next_provider(self):
        dead, alive = [], []
        primary = HostedBgeM3Embedder(
            HOSTED_PROVIDERS["nvidia"], ["k"],
            client_factory=_factory(dead, fail=RuntimeError("500 boom")),
        )
        backup = HostedBgeM3Embedder(
            HOSTED_PROVIDERS["deepinfra"], ["k"], client_factory=_factory(alive)
        )

        vectors = FallbackEmbedder([primary, backup]).embed(["hi"])

        assert vectors == [[2.0, 1.0]]  # 備援真的有回值
        assert alive[0].calls, "備援供應商應被呼叫"

    def test_raises_naming_every_provider_when_all_fail(self):
        chain = [
            HostedBgeM3Embedder(
                HOSTED_PROVIDERS[name], ["k"],
                client_factory=_factory([], fail=RuntimeError("boom")),
            )
            for name in ("nvidia", "deepinfra")
        ]
        with pytest.raises(RuntimeError) as exc:
            FallbackEmbedder(chain).embed(["hi"])

        assert "nvidia" in str(exc.value) and "deepinfra" in str(exc.value)

    def test_logs_warning_on_failover(self, caplog):
        """降級必須留痕——這次事故就是因為全程無聲才查不出來。"""
        primary = HostedBgeM3Embedder(
            HOSTED_PROVIDERS["nvidia"], ["k"],
            client_factory=_factory([], fail=RuntimeError("500 boom")),
        )
        backup = HostedBgeM3Embedder(
            HOSTED_PROVIDERS["deepinfra"], ["k"], client_factory=_factory([])
        )
        with caplog.at_level("WARNING"):
            FallbackEmbedder([primary, backup]).embed(["hi"])

        assert any("nvidia" in r.getMessage() for r in caplog.records)

    def test_refuses_to_mix_vector_spaces(self):
        """不同 model_id = 不同向量空間，混用會讓檢索結果無意義。"""
        with pytest.raises(ValueError, match="向量空間"):
            FallbackEmbedder([FakeEmbedder(), AnotherFakeEmbedder()])


class TestBuildHostedEmbedder:
    def test_chain_follows_configured_order(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_PROVIDERS", "deepinfra,nvidia")
        monkeypatch.setenv("DEEPINFRA_API_KEYS", "d1")
        monkeypatch.setenv("NVIDIA_API_KEYS", "n1")
        get_settings.cache_clear()

        embedder = build_hosted_embedder()

        assert [e.provider.name for e in embedder.embedders] == ["deepinfra", "nvidia"]

    def test_skips_providers_without_keys(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_PROVIDERS", "nvidia,deepinfra")
        monkeypatch.setenv("NVIDIA_API_KEYS", "n1")
        monkeypatch.delenv("DEEPINFRA_API_KEYS", raising=False)
        get_settings.cache_clear()

        embedder = build_hosted_embedder()

        assert embedder.provider.name == "nvidia"  # 單一可用供應商 → 不包 fallback

    def test_no_usable_provider_names_the_env_var(self):
        # _env_file=None：不讀本機 .env，才測得到「什麼 key 都沒設」的情境
        settings = Settings(_env_file=None, embedding_providers="nvidia")

        with pytest.raises(RuntimeError, match="NVIDIA_API_KEYS"):
            build_hosted_embedder(settings)

    def test_unknown_provider_lists_available(self):
        settings = Settings(_env_file=None, embedding_providers="openai")

        with pytest.raises(ValueError, match="deepinfra"):
            build_hosted_embedder(settings)

    def test_selfhost_uses_configured_url_and_needs_no_key(self):
        """自架 TEI：URL 由設定給，且未開 --api-key 時不該逼使用者填金鑰。"""
        settings = Settings(
            _env_file=None,
            embedding_providers="selfhost",
            embedding_selfhost_url="https://embed.example.com/v1/",
        )
        embedder = build_hosted_embedder(settings)

        assert embedder.provider.base_url == "https://embed.example.com/v1"  # 尾斜線去掉
        assert embedder.provider.model == "BAAI/bge-m3"
        assert embedder.provider.nim_extras is False  # 不是 NIM，別送 NVIDIA 專屬參數
        assert embedder.model_id == "bge-m3"  # 同一份權重 → 既有向量相容

    def test_selfhost_can_be_chained_with_hosted_providers(self):
        settings = Settings(
            _env_file=None,
            embedding_providers="selfhost,nvidia",
            embedding_selfhost_url="https://embed.example.com/v1",
            nvidia_api_keys="n1",
        )
        embedder = build_hosted_embedder(settings)

        assert [e.provider.name for e in embedder.embedders] == ["selfhost", "nvidia"]

    def test_selfhost_without_url_names_the_env_var(self):
        settings = Settings(_env_file=None, embedding_providers="selfhost")

        with pytest.raises(RuntimeError, match="EMBEDDING_SELFHOST_URL"):
            build_hosted_embedder(settings)

    def test_legacy_backend_name_still_resolves(self, monkeypatch):
        """正式環境 .env 目前寫 nvidia-bge-m3，不能因改名而開不起來。"""
        monkeypatch.setenv("NVIDIA_API_KEYS", "n1")
        monkeypatch.setenv("EMBEDDING_PROVIDERS", "nvidia")
        get_settings.cache_clear()

        assert get_embedder("nvidia-bge-m3").model_id == "bge-m3"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "db.sqlite3")
    migrate(c)
    yield c
    c.close()


def seed_annotated_meme(conn, *, is_meme=True, status="active") -> Meme:
    meme = Meme(meme_id=new_id("m"), image_uri="x.png", sha256=new_id("h")[:64].ljust(64, "0"))
    repo.insert_meme(conn, meme)
    repo.upsert_annotation(conn, make_annotation(meme.meme_id, is_meme=is_meme))
    if status != "active":
        repo.set_status(conn, meme.meme_id, status)
    return meme


class TestEmbedPendingMemes:
    def test_embeds_and_stores_versioned_signature(self, conn):
        meme = seed_annotated_meme(conn)
        embedder = FakeEmbedder()

        count = embed_pending_memes(conn, embedder)

        assert count == 1
        embs = repo.get_embeddings(conn, meme.meme_id, kind="text_retrieval")
        assert len(embs) == 1
        assert embs[0].model == f"fake-embed@v1|{RETRIEVAL_DOC_VERSION}"  # 模板與模型版本入庫
        # 向量確實來自檢索文件
        expected_doc = build_retrieval_document(repo.get_annotation(conn, meme.meme_id))
        assert embedder.seen == [expected_doc]
        assert embs[0].vector == pytest.approx([float(len(expected_doc)), 1.0, -0.5])

    def test_rerun_is_idempotent(self, conn):
        seed_annotated_meme(conn)
        embedder = FakeEmbedder()
        assert embed_pending_memes(conn, embedder) == 1
        assert embed_pending_memes(conn, embedder) == 0

    def test_backend_switch_creates_parallel_vectors(self, conn):
        meme = seed_annotated_meme(conn)
        embed_pending_memes(conn, FakeEmbedder())
        count_v2 = embed_pending_memes(conn, AnotherFakeEmbedder())

        assert count_v2 == 1  # 換後端 → 簽名不同 → 需重新向量化
        models = {e.model for e in repo.get_embeddings(conn, meme.meme_id)}
        assert models == {
            f"fake-embed@v1|{RETRIEVAL_DOC_VERSION}",
            f"fake-embed@v2|{RETRIEVAL_DOC_VERSION}",
        }

    def test_excludes_non_meme_pending_and_unannotated(self, conn):
        seed_annotated_meme(conn, is_meme=False)  # 非梗圖
        seed_annotated_meme(conn, status="pending_review")  # 待審
        unannotated = Meme(meme_id=new_id("m"), image_uri="u.png", sha256="e" * 64)
        repo.insert_meme(conn, unannotated)  # 未標註

        assert embed_pending_memes(conn, FakeEmbedder()) == 0

    def test_limit_and_batching(self, conn):
        for _ in range(3):
            seed_annotated_meme(conn)
        embedder = FakeEmbedder()
        assert embed_pending_memes(conn, embedder, limit=2) == 2
        assert embed_pending_memes(conn, embedder) == 1  # 補完剩下一張


class TestProviderDefaults:
    """預設值不可以指向已知掛掉的服務。

    2026-07-27 NVIDIA 的 baai/bge-m3 全面 500 至今未修，但預設值一直留著 nvidia——
    等於「沒設 EMBEDDING_PROVIDERS 的部署一定壞」，而且壞在啟動之後才看得出來。
    """

    def test_default_chain_avoids_the_dead_nvidia_bge_m3(self):
        settings = Settings(_env_file=None)

        assert "nvidia" not in settings.embedding_provider_list()

    def test_default_chain_is_selfhost_then_openrouter(self):
        settings = Settings(_env_file=None)

        assert settings.embedding_provider_list() == ["selfhost", "openrouter"]

    def test_selfhost_without_url_is_skipped_so_the_rest_of_the_chain_still_builds(self):
        """預設鏈含 selfhost，但沒自架的人（本機開發）不該因此整條鏈都組不起來。

        比照「沒設 key 的供應商自動略過」，缺 URL 的 selfhost 也略過往後退。
        """
        settings = Settings(
            _env_file=None,
            embedding_providers="selfhost,openrouter",
            openrouter_api_key="or1",
        )
        embedder = build_hosted_embedder(settings)

        assert embedder.provider.name == "openrouter"
