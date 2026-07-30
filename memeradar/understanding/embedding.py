"""Embedding 介面封裝與向量化管線（docs/03 §3.2）。

- ``Embedder`` 為薄介面：``embed(texts) -> vectors`` + ``model_id``。
- 後端註冊表 ``get_embedder(name)``：已定案 **bge-m3**（本地自架，2026-07-11 決策）；
  介面保留，之後要加 Voyage 等後端只需註冊新類別。
- 入庫簽名 ``{model_id}|{RETRIEVAL_DOC_VERSION}`` 同時綁定 embedding 模型與
  檢索文件模板版本——兩者任一改版，既有向量即視為過期需重建。
- 重依賴（torch / sentence-transformers）lazy 載入，需安裝 extras：
  ``pip install -e ".[local-embedding]"``。
- CLI：``python -m memeradar.understanding.embedding [--limit N] [--backend bge-m3]``。
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from memeradar.shared import repository as repo
from memeradar.shared.models import Embedding
from memeradar.understanding.retrieval_doc import (
    RETRIEVAL_DOC_VERSION,
    build_retrieval_document,
)

# 預設走 NVIDIA hosted bge-m3（與本地 sentence-transformers bge-m3 向量完全相同，
# cosine=1.0），省掉容器內的 torch + 2.3GB 模型與其記憶體。離線開發可設
# EMBEDDING_BACKEND=bge-m3 走本地。
DEFAULT_BACKEND = "nvidia-bge-m3"
BATCH_SIZE = 32
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
# 單次呼叫逾時 / SDK 層重試次數。務必留小：見 HostedBgeM3Embedder 內的相乘說明。
REQUEST_TIMEOUT = 10.0
SDK_RETRIES = 0
# 單一供應商的總時間預算：超過就放棄，讓備援鏈換下一家（見 _embed_batch）
PROVIDER_BUDGET = 20.0

logger = logging.getLogger("memeradar.embedding")


class EmbeddingUnavailableError(RuntimeError):
    """embedding 供應商不可用（單一供應商失敗，或備援鏈全數失敗）。

    有專用型別才能在 API 層對應到使用者文案——靠比對錯誤訊息字串的話，
    訊息一改對應就會悄悄失效（見 memeradar/api/error_copy.py）。
    """


class Embedder(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class BgeM3Embedder:
    """BGE-M3 本地推論（首次使用會自動下載模型權重，約 2.3GB）。"""

    model_id = "bge-m3"

    def __init__(self, device: str | None = None):
        self._device = device
        self._model = None

    def _ensure_loaded(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "本地 embedding 需要 sentence-transformers："
                    '請執行 pip install -e ".[local-embedding]"'
                ) from exc
            self._model = SentenceTransformer("BAAI/bge-m3", device=self._device)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_loaded()
        vectors = model.encode(texts, normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]


@dataclass(frozen=True)
class HostedProvider:
    """OpenAI 相容的 hosted bge-m3 供應商。

    各家服務的都是同一份 ``BAAI/bge-m3`` 權重，故向量可互換、``model_id`` 一律 "bge-m3"
    （換家不必重建整庫索引）；檢索走 pgvector ``<=>`` 餘弦距離，尺度差異也不影響排序。
    """

    name: str
    base_url: str
    model: str  # 各家對同一個模型的 id 大小寫不同
    keys_env: str  # Settings 欄位名（大寫即環境變數名）
    nim_extras: bool = False  # input_type / truncate 是 NVIDIA NIM 專屬擴充，別家會擋


HOSTED_PROVIDERS: dict[str, HostedProvider] = {
    "nvidia": HostedProvider(
        "nvidia", NVIDIA_BASE_URL, "baai/bge-m3", "nvidia_api_keys", nim_extras=True
    ),
    "deepinfra": HostedProvider(
        "deepinfra", "https://api.deepinfra.com/v1/openai", "BAAI/bge-m3", "deepinfra_api_keys"
    ),
    "siliconflow": HostedProvider(
        "siliconflow", "https://api.siliconflow.cn/v1", "BAAI/bge-m3", "siliconflow_api_keys"
    ),
}


class HostedBgeM3Embedder:
    """Hosted ``bge-m3``（OpenAI 相容 embeddings 介面）。

    與本地 sentence-transformers bge-m3 的向量**完全相同**（實測 cosine=1.0），故
    ``model_id`` 沿用 "bge-m3"、簽名相同、既有向量相容。不需 torch/本地模型，記憶體極省。
    多把 key 輪流以分攤免費層速率限制；失敗換 key 重試。
    """

    model_id = "bge-m3"  # 與本地相同 → 簽名相同 → 既有向量相容

    def __init__(
        self,
        provider: HostedProvider,
        keys: list[str],
        *,
        batch_size: int = BATCH_SIZE,
        budget: float = PROVIDER_BUDGET,
        client_factory: Callable[..., Any] | None = None,
    ):
        if not keys:
            raise RuntimeError(
                f"{provider.name} embedding 需要 {provider.keys_env.upper()}"
                "（或設 EMBEDDING_BACKEND=bge-m3 走本地）"
            )
        self.provider = provider
        if client_factory is None:
            from openai import OpenAI

            def client_factory(*, base_url, api_key, **kw):  # noqa: ANN001
                return OpenAI(base_url=base_url, api_key=api_key, **kw)

        # 逾時與重試次數會相乘：SDK 的 max_retries × 下面 _embed_batch 的外圈重試。
        # 2026-07-29 事故：timeout=30 × 3 次嘗試 × 外圈 2 輪 ≈ 181 秒，久到啟動探針
        # 直接把容器殺掉。有備援鏈時「快速失敗換下一家」遠比原地重試划算。
        self._clients = [
            client_factory(
                base_url=provider.base_url, api_key=k,
                timeout=REQUEST_TIMEOUT, max_retries=SDK_RETRIES,
            )
            for k in keys
        ]
        self._batch = batch_size
        self._budget = budget
        self._rr = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            out.extend(self._embed_batch(texts[i : i + self._batch]))
        return out

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        extra = (
            {"extra_body": {"input_type": "passage", "truncate": "END"}}
            if self.provider.nim_extras
            else {}
        )
        # 時間預算硬性封頂：光靠「重試幾次」無法封頂，因為每次都可能耗到逾時上限
        # （4 把 key × 逾時 10s 就是 40s+）。超過預算就放棄、讓備援鏈換下一家。
        deadline = time.monotonic() + self._budget
        last_exc: Exception | None = None
        for attempt in range(max(2, len(self._clients))):
            client = self._clients[self._rr % len(self._clients)]
            self._rr += 1
            try:
                resp = client.embeddings.create(
                    model=self.provider.model, input=batch, **extra
                )
                return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
            except Exception as exc:  # noqa: BLE001 速率限制/瞬斷 → 換 key 重試
                last_exc = exc
                if time.monotonic() >= deadline:
                    logger.warning(
                        "[embedding] %s 用完 %.0fs 預算（試了 %d 次），放棄換備援",
                        self.provider.name, self._budget, attempt + 1,
                    )
                    break
                time.sleep(0.5)
        raise EmbeddingUnavailableError(f"{self.provider.name} embedding 失敗：{last_exc}")


class FallbackEmbedder:
    """依序試多個供應商，前一家整個掛掉就換下一家。

    2026-07-27 事故：NVIDIA hosted bge-m3 全面回 500，而 embedding 是每一筆搜尋的
    必經之路 → 單一供應商等於全站搜尋的單點故障。降級一律留 WARNING，不再無聲。
    """

    def __init__(self, embedders: list[Embedder]):
        if not embedders:
            raise ValueError("備援鏈不可為空")
        spaces = {e.model_id for e in embedders}
        if len(spaces) > 1:
            raise ValueError(
                f"備援鏈的向量空間不一致：{sorted(spaces)}——混用會讓檢索結果失去意義"
            )
        self.embedders = list(embedders)
        self.model_id = embedders[0].model_id

    def embed(self, texts: list[str]) -> list[list[float]]:
        failures: list[str] = []
        for index, embedder in enumerate(self.embedders):
            name = getattr(getattr(embedder, "provider", None), "name", type(embedder).__name__)
            try:
                return embedder.embed(texts)
            except Exception as exc:  # noqa: BLE001 換下一家；全掛才往上拋
                failures.append(f"{name}：{exc}")
                if index + 1 < len(self.embedders):
                    logger.warning("[embedding] 供應商 %s 失敗，改用備援：%s", name, exc)
        raise EmbeddingUnavailableError("embedding 供應商全數失敗——" + "；".join(failures))


SELFHOST = "selfhost"


def _selfhost_provider(settings) -> HostedProvider:
    """自架 bge-m3（HuggingFace TEI 等）：走它的 OpenAI 相容 /v1/embeddings。

    同一份 BAAI/bge-m3 權重 → 向量與既有索引相容（1024 維），不必重建。
    """
    url = settings.embedding_selfhost_url.strip().rstrip("/")
    if not url:
        raise RuntimeError(
            "selfhost embedding 需要 EMBEDDING_SELFHOST_URL"
            "（例：https://embed.example.com/v1）"
        )
    return HostedProvider(
        SELFHOST, url, settings.embedding_selfhost_model, "embedding_selfhost_keys"
    )


def build_hosted_embedder(settings=None) -> Embedder:
    """依 ``EMBEDDING_PROVIDERS`` 順序組供應商鏈；沒設 key 的自動略過。"""
    if settings is None:
        from memeradar.shared.config import get_settings

        settings = get_settings()
    chain: list[Embedder] = []
    keyless: list[HostedProvider] = []
    for name in settings.embedding_provider_list():
        if name == SELFHOST:
            provider = _selfhost_provider(settings)
            # 自架服務多半不開認證（TEI 未加 --api-key 時不驗）；有設就照送
            keys = settings.csv_list(provider.keys_env) or ["not-needed"]
            chain.append(HostedBgeM3Embedder(provider, keys))
            continue
        provider = HOSTED_PROVIDERS.get(name)
        if provider is None:
            available = "、".join([*sorted(HOSTED_PROVIDERS), SELFHOST])
            raise ValueError(f"未知的 embedding 供應商：{name!r}（可用：{available}）")
        keys = settings.csv_list(provider.keys_env)
        if not keys:
            keyless.append(provider)
            continue
        chain.append(HostedBgeM3Embedder(provider, keys))
    if not chain:
        wanted = "、".join(p.keys_env.upper() for p in keyless) or "EMBEDDING_PROVIDERS"
        raise RuntimeError(
            f"沒有可用的 embedding 供應商：請設定 {wanted}（或設 EMBEDDING_BACKEND=bge-m3 走本地）"
        )
    return chain[0] if len(chain) == 1 else FallbackEmbedder(chain)


_LOCAL_BACKENDS: dict[str, type] = {
    "bge-m3": BgeM3Embedder,
}
# "nvidia-bge-m3" 是舊名（正式環境 .env 仍在用）：現在一律解析成可換供應商的 hosted 鏈
_HOSTED_BACKENDS = frozenset({"hosted-bge-m3", "nvidia-bge-m3"})
_BACKENDS = frozenset({*_HOSTED_BACKENDS, *_LOCAL_BACKENDS})


def get_embedder(name: str) -> Embedder:
    if name in _HOSTED_BACKENDS:
        return build_hosted_embedder()
    if name in _LOCAL_BACKENDS:
        return _LOCAL_BACKENDS[name]()
    available = "、".join(sorted(_BACKENDS))
    raise ValueError(f"未知的 embedding 後端：{name!r}（可用：{available}）")


def embedding_signature(embedder: Embedder) -> str:
    """入庫用簽名：embedding 模型 × 檢索文件模板版本。"""
    return f"{embedder.model_id}|{RETRIEVAL_DOC_VERSION}"


def embed_pending_memes(
    conn: sqlite3.Connection,
    embedder: Embedder,
    *,
    limit: int | None = None,
    batch_size: int = BATCH_SIZE,
) -> int:
    """把缺少當前簽名向量的梗圖批次向量化，回傳處理張數（冪等）。"""
    signature = embedding_signature(embedder)
    pending = repo.list_memes_missing_embedding(
        conn, kind="text_retrieval", model=signature, limit=limit
    )
    processed = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        docs = [
            build_retrieval_document(repo.get_annotation(conn, meme.meme_id)) for meme in batch
        ]
        vectors = embedder.embed(docs)
        for meme, vector in zip(batch, vectors, strict=True):
            repo.add_embedding(
                conn,
                Embedding(
                    meme_id=meme.meme_id,
                    kind="text_retrieval",
                    model=signature,
                    vector=vector,
                ),
            )
        processed += len(batch)
    return processed


def main(argv: list[str] | None = None) -> None:
    import argparse

    from memeradar.shared.db import connect, migrate

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="批次向量化已標註的梗圖")
    parser.add_argument("--limit", type=int, default=None, help="最多處理張數（預設全部）")
    parser.add_argument("--backend", default=DEFAULT_BACKEND, choices=sorted(_BACKENDS))
    args = parser.parse_args(argv)

    embedder = get_embedder(args.backend)
    conn = connect()
    try:
        migrate(conn)
        count = embed_pending_memes(conn, embedder)
    finally:
        conn.close()
    print(f"向量化完成：{count} 張（簽名 {embedding_signature(embedder)}）")


if __name__ == "__main__":
    main()
