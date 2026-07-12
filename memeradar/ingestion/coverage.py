"""P0-3 seed 配平統計（docs/06 §3.2）。

冷啟動要求 seed 集按策略錨點配平，而不是隨機收 300 張：
每策略 ≥ 8 張、指定 Demo 主題（海綿寶寶 / 甄嬛傳）各 ≥ 30 張、總量 150–300。

一張圖「覆蓋」某策略 = 任一 usage_hint 含該策略 label 或別名（標註 prompt
要求 usage_hints 對齊錨點詞彙，子字串比對足夠；對不上任何錨點的圖列入
unmatched，通常代表 usage_hints 品質待複核）。只計 active 且 is_meme 的已標註圖。

用法：``python -m memeradar.ingestion.coverage``（蒐圖 → 匯入 → 標註後隨時重跑，
看還缺哪些錨點）。
"""

from __future__ import annotations

import sqlite3
import sys

from memeradar.shared.taxonomy import get_taxonomy

STRATEGY_TARGET = 8  # 每策略錨點最低張數
FRANCHISE_TARGETS = {"海綿寶寶": 30, "甄嬛傳": 30}  # Demo 指定主題
TOTAL_TARGET = (150, 300)


def build_coverage_report(conn: sqlite3.Connection) -> dict:
    from memeradar.shared.repository import _loads  # 共用 JSON 解析

    taxonomy = get_taxonomy()
    rows = conn.execute(
        """
        SELECT a.usage_hints, a.franchise, a.categories
        FROM memes m
        JOIN meme_annotations a ON a.meme_id = m.meme_id
        WHERE m.status = 'active' AND a.is_meme = 1
        """
    ).fetchall()

    strategy_counts = {s.label: 0 for s in taxonomy.strategies}
    franchise_counts: dict[str, int] = {}
    category_counts = {c.label: 0 for c in taxonomy.categories}
    unmatched = 0

    for row in rows:
        hints = _loads(row["usage_hints"]) or []
        covered = False
        for strategy in taxonomy.strategies:
            needles = (strategy.label, *strategy.aliases)
            if any(needle in hint for needle in needles for hint in hints):
                strategy_counts[strategy.label] += 1
                covered = True
        if not covered:
            unmatched += 1

        franchise = taxonomy.normalize_franchise(row["franchise"])
        if franchise:
            franchise_counts[franchise] = franchise_counts.get(franchise, 0) + 1

        for category in _loads(row["categories"]) or []:
            if category in category_counts:
                category_counts[category] += 1

    # 優先主題就算 0 張也要出現在報表（缺口才看得見）
    for name in FRANCHISE_TARGETS:
        franchise_counts.setdefault(name, 0)

    return {
        "total": len(rows),
        "total_target": TOTAL_TARGET,
        "strategies": [
            {
                "label": label,
                "count": count,
                "target": STRATEGY_TARGET,
                "gap": max(0, STRATEGY_TARGET - count),
            }
            for label, count in strategy_counts.items()
        ],
        "franchises": [
            {
                "name": name,
                "count": count,
                "target": FRANCHISE_TARGETS.get(name),
                "gap": max(0, FRANCHISE_TARGETS.get(name, 0) - count),
            }
            for name, count in sorted(
                franchise_counts.items(), key=lambda kv: (-kv[1], kv[0])
            )
        ],
        "categories": [
            {"label": label, "count": count} for label, count in category_counts.items()
        ],
        "unmatched": unmatched,
    }


def format_coverage(report: dict) -> str:
    lines: list[str] = []
    low, high = report["total_target"]
    lines.append(f"seed 配平報表（庫內 active 梗圖 {report['total']} 張，目標 {low}–{high}）")

    lines.append("")
    lines.append(f"策略錨點覆蓋（每策略 ≥ {STRATEGY_TARGET} 張）：")
    for row in report["strategies"]:
        mark = "✔" if row["gap"] == 0 else "✘"
        gap = "" if row["gap"] == 0 else f"　← 缺 {row['gap']} 張"
        lines.append(f"  {mark} {row['label']}: {row['count']}{gap}")

    lines.append("")
    lines.append("優先主題（Demo 指定，各 ≥ 30 張）與其他 franchise：")
    for row in report["franchises"]:
        if row["target"]:
            mark = "✔" if row["gap"] == 0 else "✘"
            gap = "" if row["gap"] == 0 else f"　← 缺 {row['gap']} 張"
            lines.append(f"  {mark} {row['name']}: {row['count']}/{row['target']}{gap}")
        else:
            lines.append(f"  ・ {row['name']}: {row['count']}")

    lines.append("")
    lines.append("分類分佈：")
    for row in report["categories"]:
        if row["count"]:
            lines.append(f"  ・ {row['label']}: {row['count']}")

    if report["unmatched"]:
        lines.append("")
        lines.append(
            f"[提醒] {report['unmatched']} 張圖的 usage_hints 對不上任何策略錨點——"
            "到 Console「複核」頁檢視是否要修標籤"
        )
    return "\n".join(lines)


def main() -> None:
    # Windows 主控台預設編碼常非 UTF-8，避免中文輸出亂碼
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    from memeradar.shared.db import connect, migrate

    conn = connect()
    try:
        migrate(conn)
        print(format_coverage(build_coverage_report(conn)))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
