"""Taxonomy 封閉集的動態 StrEnum。

成員值即繁中標籤，會直接進 structured outputs 的 JSON schema，
由 API 端在生成階段擋掉字典外的標籤——標註端（understanding）與
意圖端（matching）共用同一份，保證兩端語彙一致（docs/03 §2.3）。
"""

from __future__ import annotations

from enum import StrEnum

from memeradar.shared.taxonomy import get_taxonomy

_TAX = get_taxonomy()

EmotionLabel = StrEnum("EmotionLabel", {label: label for label in _TAX.emotions})
CategoryLabel = StrEnum("CategoryLabel", {c.label: c.label for c in _TAX.categories})
StrategyLabel = StrEnum("StrategyLabel", {s.label: s.label for s in _TAX.strategies})
