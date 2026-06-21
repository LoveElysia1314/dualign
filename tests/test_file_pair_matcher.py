"""
Dualign — 文件对匹配引擎测试
"""

import pytest
import tempfile
from pathlib import Path
from dualign.core.file_pair_matcher import (
    FilePairMatcher,
    MatchRule,
    MatchedPair,
)


class TestMatchRule:
    def test_glob_rule(self):
        rule = MatchRule(
            type="glob", src_pattern="*.source.md", tgt_pattern="*.target.md"
        )
        assert rule.type == "glob"

    def test_prefix_rule(self):
        rule = MatchRule(type="prefix", suffix_pair=(".src.md", ".tgt.md"))
        assert rule.type == "prefix"


class TestNaturalSort:
    def test_numeric_order(self):
        paths = [Path("ch10.md"), Path("ch2.md"), Path("ch1.md")]
        sorted_p = FilePairMatcher._natural_sort(paths)
        assert [p.stem for p in sorted_p] == ["ch1", "ch2", "ch10"]

    def test_alphabetic(self):
        paths = [Path("banana.md"), Path("apple.md"), Path("cherry.md")]
        sorted_p = FilePairMatcher._natural_sort(paths)
        assert [p.stem for p in sorted_p] == ["apple", "banana", "cherry"]

    def test_mixed(self):
        paths = [Path("a2.md"), Path("a10.md"), Path("a1.md")]
        sorted_p = FilePairMatcher._natural_sort(paths)
        assert [p.stem for p in sorted_p] == ["a1", "a2", "a10"]


class TestFileDiscovery:
    @pytest.fixture
    def temp_source_dir(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            for name in ["ch1.source.md", "ch2.source.md", "ch10.source.md"]:
                (base / name).write_text("src", encoding="utf-8")
            for name in ["ch1.target.md", "ch2.target.md", "ch10.target.md"]:
                (base / name).write_text("tgt", encoding="utf-8")
            (base / "README.md").write_text("readme", encoding="utf-8")
            yield base

    def test_glob_discovery(self, temp_source_dir):
        matcher = FilePairMatcher()
        rule = MatchRule(
            type="glob", src_pattern="*.source.md", tgt_pattern="*.target.md"
        )
        pairs = matcher.match(str(temp_source_dir), str(temp_source_dir), [rule])
        assert len(pairs) == 3

    def test_glob_order(self, temp_source_dir):
        matcher = FilePairMatcher()
        rule = MatchRule(
            type="glob", src_pattern="*.source.md", tgt_pattern="*.target.md"
        )
        pairs = matcher.match(str(temp_source_dir), str(temp_source_dir), [rule])
        assert "ch1" in pairs[0].label
        assert "ch10" in pairs[2].label or "ch10" in pairs[2].entry_id

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            matcher = FilePairMatcher()
            rule = MatchRule(
                type="glob", src_pattern="*.source.md", tgt_pattern="*.target.md"
            )
            pairs = matcher.match(d, d, [rule])
            assert len(pairs) == 0
