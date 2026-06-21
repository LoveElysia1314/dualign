"""
Dualign — 对齐核心算法测试
"""

import numpy as np
import pytest
from dualign.core.aligner import (
    AlignConfig,
    AlignmentResult,
    align,
    op_type_str,
    _normalize,
    ALIGN_CORE_VERSION,
)


class TestOpTypeStr:
    def test_1to1(self):
        assert op_type_str((0,), (0,)) == "1:1"

    def test_2to1(self):
        assert op_type_str((0, 1), (0,)) == "2:1"

    def test_1to3(self):
        assert op_type_str((0,), (0, 1, 2)) == "1:3"

    def test_delete(self):
        assert op_type_str((0,), ()) == "1:0"

    def test_insert(self):
        assert op_type_str((), (0,)) == "0:1"

    def test_multi_delete(self):
        assert op_type_str((0, 1, 2), ()) == "3:0"

    def test_multi_insert(self):
        assert op_type_str((), (0, 1)) == "0:2"


class TestAlignEmpty:
    def test_both_empty(self):
        result = align([], [], np.empty((0, 3)), np.empty((0, 3)))
        assert len(result.all_ops) == 0
        assert result.stats["n_source"] == 0
        assert result.stats["n_target"] == 0

    def test_single_line(self):
        emb = np.array([[1.0]], dtype=np.float64)
        result = align(["hello"], ["你好"], emb, emb)
        assert len(result.all_ops) >= 1

    def test_src_empty_tgt_nonempty(self):
        emb = np.eye(3)
        result = align([], ["a", "b", "c"], np.empty((0, 3)), emb)
        for s, t, _ in result.all_ops:
            assert len(s) == 0  # 全部是 0:1

    def test_tgt_empty_src_nonempty(self):
        emb = np.eye(3)
        result = align(["a", "b", "c"], [], emb, np.empty((0, 3)))
        for s, t, _ in result.all_ops:
            assert len(t) == 0  # 全部是 1:0


class TestAlignConfig:
    def test_default_config(self):
        cfg = AlignConfig()
        assert cfg.allow_insertions is True
        assert cfg.allow_deletions is True

    def test_custom_config(self):
        cfg = AlignConfig(allow_insertions=False, allow_deletions=False)
        assert cfg.allow_insertions is False
        assert cfg.allow_deletions is False


class TestAlignStats:
    def test_stats_keys(self):
        emb = np.eye(3)
        result = align(["a", "b", "c"], ["x", "y", "z"], emb, emb)
        required = {"n_source", "n_target", "n_1to1", "avg_similarity"}
        assert required.issubset(result.stats.keys())

    def test_version_present(self):
        assert len(ALIGN_CORE_VERSION) > 0
        assert "." in ALIGN_CORE_VERSION


class TestNormalize:
    def test_unit_length(self):
        v = np.array([3.0, 4.0])
        n = _normalize(v)
        assert abs(np.linalg.norm(n) - 1.0) < 1e-10

    def test_zero_vector(self):
        n = _normalize(np.array([0.0, 0.0]))
        assert np.linalg.norm(n) == 0.0
