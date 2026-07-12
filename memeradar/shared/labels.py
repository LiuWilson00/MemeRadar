"""Taxonomy 封閉集的動態 StrEnum。

成員值即繁中標籤，會直接進 structured outputs 的 JSON schema，
由 API 端在生成階段擋掉字典外的標籤——標註端（understanding）與
意圖端（matching）共用同一份，保證兩端語彙一致（docs/03 §2.3）。

情緒與策略為封閉集（enum 強制）。分類改為開放集（franchise 式）：
不進 enum，改由 taxonomy.normalize_category 正規化，故此處無 CategoryLabel。
"""

from __future__ import annotations

from enum import StrEnum

from memeradar.shared.taxonomy import get_taxonomy

_TAX = get_taxonomy()

EmotionLabel = StrEnum("EmotionLabel", {label: label for label in _TAX.emotions})
StrategyLabel = StrEnum("StrategyLabel", {s.label: s.label for s in _TAX.strategies})
