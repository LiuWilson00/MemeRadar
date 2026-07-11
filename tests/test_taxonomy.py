"""Taxonomy v1 載入與驗證測試（驗收：docs/TASKS.md P0-2）。"""

from pathlib import Path

import pytest

from memeradar.shared.taxonomy import TaxonomyError, get_taxonomy, load_taxonomy

TAX = load_taxonomy()


class TestBuiltinTaxonomy:
    def test_closed_set_sizes_match_spec(self):
        # docs/03 §2.3：情緒 20、策略錨點 14、分類 10
        assert len(TAX.emotions) == 20
        assert len(TAX.strategies) == 14
        assert len(TAX.categories) == 10

    def test_required_labels_present(self):
        assert "擺爛" in TAX.emotions
        assert "理直氣壯" in TAX.emotions
        assert {c.label for c in TAX.categories} >= {"卡通動畫", "戲劇影視", "名人政治", "其他"}

    def test_only_comfort_is_sensitive_safe(self):
        # docs/04 §4：敏感情境僅保留「安撫」
        safe = TAX.sensitive_safe_strategies
        assert [s.label for s in safe] == ["安撫"]

    def test_politics_excluded_by_default(self):
        # docs/04 §4：「名人政治」分類預設排除
        assert TAX.default_excluded_categories == ("名人政治",)

    def test_strategy_lookup_by_label_and_alias(self):
        assert TAX.strategy_by_label("滑跪求饒").id == "grovel"
        assert TAX.strategy_by_label("滑跪").id == "grovel"
        assert TAX.strategy_by_label("不存在的策略") is None

    def test_franchise_normalization(self):
        assert TAX.normalize_franchise("SpongeBob") == "海綿寶寶"
        assert TAX.normalize_franchise("  spongebob ") == "海綿寶寶"  # 大小寫與空白不敏感
        assert TAX.normalize_franchise("甄嬛传") == "甄嬛傳"
        assert TAX.normalize_franchise("海綿寶寶") == "海綿寶寶"  # 正規名自身可解析

    def test_franchise_unknown_passthrough(self):
        assert TAX.normalize_franchise("獵人") == "獵人"
        assert TAX.normalize_franchise(None) is None
        assert TAX.normalize_franchise("   ") is None

    def test_singleton_cached(self):
        assert get_taxonomy() is get_taxonomy()


class TestValidation:
    def _load(self, tmp_path: Path, content: str):
        f = tmp_path / "taxonomy.yaml"
        f.write_text(content, encoding="utf-8")
        return load_taxonomy(f)

    def test_duplicate_emotion_rejected(self, tmp_path):
        with pytest.raises(TaxonomyError, match="情緒標籤重複"):
            self._load(
                tmp_path,
                """
version: 1
emotions: [無奈, 無奈]
strategies:
  - {id: comfort, label: 安撫, sensitive_safe: true}
categories:
  - {label: 其他}
""",
            )

    def test_missing_sensitive_safe_rejected(self, tmp_path):
        with pytest.raises(TaxonomyError, match="sensitive_safe"):
            self._load(
                tmp_path,
                """
version: 1
emotions: [無奈]
strategies:
  - {id: clap_back, label: 嗆聲反擊, sensitive_safe: false}
categories:
  - {label: 其他}
""",
            )

    def test_conflicting_franchise_alias_rejected(self, tmp_path):
        with pytest.raises(TaxonomyError, match="別名衝突"):
            self._load(
                tmp_path,
                """
version: 1
emotions: [無奈]
strategies:
  - {id: comfort, label: 安撫, sensitive_safe: true}
categories:
  - {label: 其他}
franchises:
  海綿寶寶: [SpongeBob]
  派大星宇宙: [spongebob]
""",
            )

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(TaxonomyError, match="找不到"):
            load_taxonomy(tmp_path / "nope.yaml")
