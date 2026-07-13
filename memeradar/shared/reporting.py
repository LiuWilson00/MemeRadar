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
