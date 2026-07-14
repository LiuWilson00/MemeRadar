"""P4-1 回饋報表聚合（docs/05 §2.2、docs/06 §3.6）。

回饋量在 Demo 階段很小（數百筆級），聚合直接在 Python 端做，
換取「策略 / 參數快照藏在 JSON 欄位」的取值彈性。

👎 備註列表供人工歸因到五類錯誤（docs/06 §3.6）：
意圖錯 / query 爛 / 庫缺圖 / 排序錯 / 梗過時——分別對症，避免只看總分瞎調。
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any


def _rate(ups: int, downs: int) -> float | None:
    total = ups + downs
    return ups / total if total else None


def _group_rows(buckets: dict[Any, dict[str, int]]) -> list[dict]:
    rows = [
        {
            "key": key,
            "ups": counts["ups"],
            "downs": counts["downs"],
            "up_rate": _rate(counts["ups"], counts["downs"]),
        }
        for key, counts in buckets.items()
    ]
    rows.sort(key=lambda r: (-(r["ups"] + r["downs"]), str(r["key"])))
    return rows


def _params_key(params_snapshot: dict) -> str:
    params = (params_snapshot or {}).get("params", {})
    return (
        f"n={params.get('top_n', '%s')} "
        f"sim≥{params.get('min_similarity', '%s')} "
        f"div={params.get('diversity', '%s')} "
        f"hot={params.get('hotness_weight', '%s')}"
    )


def build_feedback_report(conn: sqlite3.Connection) -> dict:
    from memeradar.shared.repository import _loads  # 共用 JSON 解析

    rows = conn.execute(
        """
        SELECT f.rating, f.rank, f.note, f.created_at, f.meme_id, f.query_id,
               r.intent_result, r.final_results, r.params_snapshot,
               a.ocr_text, a.franchise
        FROM feedback_events f
        JOIN recommendation_logs r ON r.query_id = f.query_id
        LEFT JOIN meme_annotations a ON a.meme_id = f.meme_id
        ORDER BY f.created_at
        """
    ).fetchall()

    ups = downs = 0
    query_ids: set[str] = set()
    daily: dict[str, dict[str, int]] = defaultdict(lambda: {"ups": 0, "downs": 0})
    by_strategy: dict[str, dict[str, int]] = defaultdict(lambda: {"ups": 0, "downs": 0})
    by_franchise: dict[str, dict[str, int]] = defaultdict(lambda: {"ups": 0, "downs": 0})
    by_rank: dict[int, dict[str, int]] = defaultdict(lambda: {"ups": 0, "downs": 0})
    by_params: dict[str, dict[str, int]] = defaultdict(lambda: {"ups": 0, "downs": 0})
    down_notes: list[dict] = []

    for row in rows:
        is_up = row["rating"] == "up"
        ups += is_up
        downs += not is_up
        query_ids.add(row["query_id"])
        bucket = "ups" if is_up else "downs"

        daily[row["created_at"][:10]][bucket] += 1
        by_rank[row["rank"]][bucket] += 1
        by_franchise[row["franchise"] or "—"][bucket] += 1
        by_params[_params_key(_loads(row["params_snapshot"]))][bucket] += 1

        # 該回饋對應結果的命中策略（藏在 log 的 final_results JSON）
        final_results = _loads(row["final_results"]) or []
        strategy = next(
            (
                item.get("matched_strategy", "—")
                for item in final_results
                if item.get("meme_id") == row["meme_id"]
            ),
            "—",
        )
        by_strategy[strategy][bucket] += 1

        if not is_up and row["note"]:
            intent = _loads(row["intent_result"]) or {}
            down_notes.append(
                {
                    "created_at": row["created_at"],
                    "query_id": row["query_id"],
                    "note": row["note"],
                    "meme_id": row["meme_id"],
                    "meme_ocr": row["ocr_text"] or "",
                    "rank": row["rank"],
                    "matched_strategy": strategy,
                    "intent_summary": intent.get("summary", ""),
                }
            )

    return {
        "totals": {"ups": ups, "downs": downs, "total": ups + downs, "up_rate": _rate(ups, downs)},
        "queries_with_feedback": len(query_ids),
        "daily": [
            {"date": date, **counts} for date, counts in sorted(daily.items())
        ],
        "by_strategy": _group_rows(by_strategy),
        "by_franchise": _group_rows(by_franchise),
        "by_rank": _group_rows(by_rank),
        "by_params": _group_rows(by_params),
        "down_notes": list(reversed(down_notes)),  # 新到舊
    }


def build_dashboard(conn: sqlite3.Connection) -> dict:
    """全站監控儀表板：使用量 / 推薦延遲 / NVIDIA 用量 / 標註速度 / 回饋 / 圖庫。"""
    from datetime import UTC, datetime, timedelta

    from memeradar.shared import repository as repo

    now = datetime.now(UTC)
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    cutoff_14d = (now - timedelta(days=14)).date().isoformat()

    def scalar(sql: str, params: tuple = ()) -> int:
        row = conn.execute(sql, params).fetchone()
        return row["n"] if row else 0

    def _int(x) -> int | None:
        return int(x) if x is not None else None

    fb = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN rating='up' THEN 1 ELSE 0 END), 0) AS ups, "
        "COALESCE(SUM(CASE WHEN rating='down' THEN 1 ELSE 0 END), 0) AS downs FROM feedback_events"
    ).fetchone()
    ups, downs = fb["ups"], fb["downs"]
    memes_active = scalar("SELECT COUNT(*) AS n FROM memes WHERE status = 'active'")
    embeddings = scalar("SELECT COUNT(*) AS n FROM embeddings WHERE kind = 'text_retrieval'")

    overview = {
        "recommendations_total": scalar("SELECT COUNT(*) AS n FROM recommendation_logs"),
        "recommendations_7d": scalar(
            "SELECT COUNT(*) AS n FROM recommendation_logs WHERE created_at >= %s", (cutoff_7d,)
        ),
        "unique_clients": scalar(
            "SELECT COUNT(DISTINCT client_id) AS n FROM recommendation_logs "
            "WHERE client_id IS NOT NULL"
        ),
        "tasks_total": scalar("SELECT COUNT(*) AS n FROM tasks"),
        "memes_active": memes_active,
        "memes_total": scalar("SELECT COUNT(*) AS n FROM memes"),
        "embeddings": embeddings,
        "annotations": scalar("SELECT COUNT(*) AS n FROM meme_annotations"),
        "vlm_calls_total": scalar("SELECT COUNT(*) AS n FROM vlm_calls"),
        "feedback_ups": ups,
        "feedback_downs": downs,
        "feedback_up_rate": _rate(ups, downs),
        "embedding_coverage": (min(1.0, embeddings / memes_active) if memes_active else None),
    }

    tasks_by_status = {
        r["status"]: r["n"]
        for r in conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()
    }

    daily = [
        {"date": r["day"], "count": r["n"]}
        for r in conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n FROM recommendation_logs "
            "WHERE created_at >= %s GROUP BY day ORDER BY day",
            (cutoff_14d,),
        ).fetchall()
    ]

    # 各階段延遲 p50 / p95（timings 為 TEXT JSON，查詢時 ::jsonb 取值）
    stages = ("intent", "retrieval", "rerank", "total")
    select = ", ".join(
        f"percentile_cont({q}) WITHIN GROUP "
        f"(ORDER BY (timings::jsonb->>'{s}')::numeric) AS {s}_{name}"
        for s in stages
        for q, name in ((0.5, "p50"), (0.95, "p95"))
    )
    lat_row = conn.execute(
        f"SELECT {select} FROM recommendation_logs WHERE timings IS NOT NULL"
    ).fetchone()
    latency = {k: _int(v) for k, v in dict(lat_row).items()}

    vlm_calls = [
        {"task": r["task"], "status": r["status"], "count": r["n"], "avg_ms": _int(r["avg_ms"])}
        for r in conn.execute(
            "SELECT task, status, COUNT(*) AS n, AVG(latency_ms) AS avg_ms "
            "FROM vlm_calls GROUP BY task, status ORDER BY task, status"
        ).fetchall()
    ]

    franchises = list(repo.franchise_counts(conn).items())[:8]
    categories = list(repo.category_counts(conn).items())[:8]

    return {
        "overview": overview,
        "tasks_by_status": tasks_by_status,
        "daily_recommendations": daily,
        "latency_ms": latency,
        "vlm_calls": vlm_calls,
        "library": {
            "by_franchise": [{"name": k, "count": v} for k, v in franchises],
            "by_category": [{"name": k, "count": v} for k, v in categories],
        },
    }
