"""P1-6 CLI 檢索驗證工具：一句話 query → Top-K（含分數與標籤）。

開發者調校用：驗證「檢索文件模板 + embedding + 過濾」的檢索品質，
P1-6 驗收（20 組 query 肉眼合理）與 embedding A/B 都用這支工具執行。

用法：
    python -m memeradar.matching.cli "被老闆罵了想擺爛" \
        [--top 10] [--franchise 海綿寶寶 ...] [--category 卡通動畫 ...] \
        [--include-nsfw] [--min-similarity 0.35] [--backend bge-m3] [--show-doc]
"""

from __future__ import annotations

import sqlite3
import sys

from memeradar.matching.search import SearchFilters, SearchHit, SqliteBruteForceSearcher
from memeradar.understanding.embedding import (
    DEFAULT_BACKEND,
    Embedder,
    embedding_signature,
    get_embedder,
)


def run_query(
    conn: sqlite3.Connection,
    embedder: Embedder,
    query: str,
    *,
    k: int,
    filters: SearchFilters,
    min_similarity: float = 0.0,
) -> list[SearchHit]:
    [query_vector] = embedder.embed([query])
    searcher = SqliteBruteForceSearcher(conn, signature=embedding_signature(embedder))
    return searcher.search(query_vector, k=k, filters=filters, min_similarity=min_similarity)


def count_indexed(conn: sqlite3.Connection, signature: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM embeddings WHERE kind = 'text_retrieval' AND model = ?",
        (signature,),
    ).fetchone()
    return row["n"]


def format_hits(hits: list[SearchHit], *, signature: str, indexed_count: int) -> str:
    if not hits:
        if indexed_count == 0:
            return (
                f"簽名 {signature} 下沒有任何向量。\n"
                "請先標註並向量化：python -m memeradar.understanding.annotator、"
                "python -m memeradar.understanding.embedding"
            )
        return (
            f"無結果：{indexed_count} 筆已索引向量中，0 筆通過過濾與相似度門檻。\n"
            "可嘗試降低 --min-similarity 或放寬 --franchise / --category 條件。"
        )

    lines: list[str] = []
    for rank, hit in enumerate(hits, 1):
        ann = hit.annotation
        source = ann.franchise or "—"
        lines.append(f"#{rank}  {hit.similarity:.3f}  [{source}] {ann.ocr_text or '(無圖中文字)'}")
        lines.append(f"     情緒：{'、'.join(ann.emotions)}；分類：{'、'.join(ann.categories)}")
        for hint in ann.usage_hints:
            lines.append(f"     用途：{hint}")
        lines.append(f"     meme_id={hit.meme_id}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    import argparse

    from memeradar.shared.db import connect, migrate
    from memeradar.understanding.retrieval_doc import build_retrieval_document

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="一句話 query 檢索梗圖庫")
    parser.add_argument("query", help="檢索語句（建議用使用情境語彙，如：被指責時想自嘲）")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--franchise", action="append", default=[], help="可重複；限定梗圖包")
    parser.add_argument("--category", action="append", default=[], help="可重複；限定分類")
    parser.add_argument("--include-nsfw", action="store_true")
    parser.add_argument("--min-similarity", type=float, default=0.0)
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument("--show-doc", action="store_true", help="附印每筆結果的檢索文件")
    args = parser.parse_args(argv)

    embedder = get_embedder(args.backend)
    filters = SearchFilters(
        franchises=tuple(args.franchise),
        categories=tuple(args.category),
        exclude_nsfw=not args.include_nsfw,
    )

    conn = connect()
    try:
        migrate(conn)
        hits = run_query(
            conn,
            embedder,
            args.query,
            k=args.top,
            filters=filters,
            min_similarity=args.min_similarity,
        )
        signature = embedding_signature(embedder)
        print(f"query=「{args.query}」  簽名={signature}\n")
        print(format_hits(hits, signature=signature, indexed_count=count_indexed(conn, signature)))
        if args.show_doc and hits:
            from memeradar.shared import repository as repo

            print("\n── 檢索文件 ──")
            for hit in hits:
                print(f"\n[{hit.meme_id}]")
                print(build_retrieval_document(repo.get_annotation(conn, hit.meme_id)))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
