"""Taxonomy v1 載入與驗證。

標註端（understanding）與意圖端（matching）都必須透過本模組讀取
``memeradar/shared/data/taxonomy.yaml``，確保兩端語彙一致（docs/03 §2.3）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_TAXONOMY_PATH = Path(__file__).parent / "data" / "taxonomy.yaml"


class TaxonomyError(ValueError):
    """taxonomy 資料檔內容不合法時拋出。"""


@dataclass(frozen=True)
class Strategy:
    id: str
    label: str
    description: str
    sensitive_safe: bool
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Category:
    label: str
    default_excluded: bool = False


@dataclass(frozen=True)
class Taxonomy:
    version: int
    emotions: tuple[str, ...]
    strategies: tuple[Strategy, ...]
    categories: tuple[Category, ...]
    # 正規名稱 -> 別名列表（原始資料，供展示 / 維護）
    franchises: dict[str, tuple[str, ...]]
    # 比對鍵（casefold + strip 後的別名或正規名）-> 正規名稱
    _franchise_lookup: dict[str, str] = field(repr=False, default_factory=dict)

    # -- 查詢介面 ---------------------------------------------------------

    def normalize_franchise(self, name: str | None) -> str | None:
        """把 franchise 名稱正規化為庫內唯一寫法；查無對應時原樣返回。"""
        if name is None:
            return None
        cleaned = name.strip()
        if not cleaned:
            return None
        return self._franchise_lookup.get(cleaned.casefold(), cleaned)

    def strategy_by_label(self, label: str) -> Strategy | None:
        needle = label.strip()
        for s in self.strategies:
            if needle == s.label or needle in s.aliases:
                return s
        return None

    @property
    def sensitive_safe_strategies(self) -> tuple[Strategy, ...]:
        return tuple(s for s in self.strategies if s.sensitive_safe)

    @property
    def default_excluded_categories(self) -> tuple[str, ...]:
        return tuple(c.label for c in self.categories if c.default_excluded)


def _require_unique(items: list[str], what: str) -> None:
    seen: set[str] = set()
    for item in items:
        if item in seen:
            raise TaxonomyError(f"{what}重複：{item!r}")
        seen.add(item)


def _parse(raw: dict) -> Taxonomy:
    version = raw.get("version")
    if not isinstance(version, int):
        raise TaxonomyError("version 必須是整數")

    emotions = raw.get("emotions") or []
    if not emotions:
        raise TaxonomyError("emotions 不可為空")
    _require_unique(emotions, "情緒標籤")

    strategies_raw = raw.get("strategies") or []
    if not strategies_raw:
        raise TaxonomyError("strategies 不可為空")
    strategies = tuple(
        Strategy(
            id=s["id"],
            label=s["label"],
            description=s.get("description", ""),
            sensitive_safe=bool(s.get("sensitive_safe", False)),
            aliases=tuple(s.get("aliases") or ()),
        )
        for s in strategies_raw
    )
    _require_unique([s.id for s in strategies], "策略 id")
    _require_unique(
        [s.label for s in strategies] + [a for s in strategies for a in s.aliases],
        "策略名稱（含別名）",
    )
    if not any(s.sensitive_safe for s in strategies):
        raise TaxonomyError("至少需有一個 sensitive_safe 策略（敏感情境的降級目標）")

    categories_raw = raw.get("categories") or []
    if not categories_raw:
        raise TaxonomyError("categories 不可為空")
    categories = tuple(
        Category(label=c["label"], default_excluded=bool(c.get("default_excluded", False)))
        for c in categories_raw
    )
    _require_unique([c.label for c in categories], "分類標籤")

    franchises_raw: dict[str, list[str]] = raw.get("franchises") or {}
    franchises = {name: tuple(aliases or ()) for name, aliases in franchises_raw.items()}
    lookup: dict[str, str] = {}
    for canonical, aliases in franchises.items():
        for key_source in (canonical, *aliases):
            key = key_source.strip().casefold()
            existing = lookup.get(key)
            if existing is not None and existing != canonical:
                raise TaxonomyError(
                    f"franchise 別名衝突：{key_source!r} 同時指向 {existing!r} 與 {canonical!r}"
                )
            lookup[key] = canonical

    return Taxonomy(
        version=version,
        emotions=tuple(emotions),
        strategies=strategies,
        categories=categories,
        franchises=franchises,
        _franchise_lookup=lookup,
    )


def load_taxonomy(path: Path | None = None) -> Taxonomy:
    """讀取並驗證 taxonomy 資料檔（預設為 repo 內建 v1）。"""
    target = path or DEFAULT_TAXONOMY_PATH
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TaxonomyError(f"找不到 taxonomy 資料檔：{target}") from exc
    if not isinstance(raw, dict):
        raise TaxonomyError("taxonomy 資料檔頂層必須是 mapping")
    return _parse(raw)


@lru_cache(maxsize=1)
def get_taxonomy() -> Taxonomy:
    """取得內建 taxonomy 單例（全程式共用；測試可 cache_clear()）。"""
    return load_taxonomy()
