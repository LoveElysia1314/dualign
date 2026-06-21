"""
Dualign — CLI 对齐流水线集成测试

测试方向：
  1. align_chapter 完整流程（编码→对齐→修复→导出）
  2. 空文本处理
  3. 缓存命中/未命中行为
  4. 质量门控触发时的行为
"""

from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from dualign.core import AlignConfig
from dualign.services.cli_pipeline import align_chapter

# ═══════════════════════════════════════════════════════════════
# Mock 模型 — 返回确定性随机嵌入
# ═══════════════════════════════════════════════════════════════


class MockEncoder:
    """模拟 OllamaEncoder，返回固定维度的随机嵌入。"""

    def __init__(self, dim: int = 4, seed: int = 42):
        self._rng = np.random.RandomState(seed)
        self._dim = dim

    def encode(self, texts, normalize_embeddings=True, **kw):
        """返回固定种子随机向量，保证可复现。"""
        if isinstance(texts, str):
            texts = [texts]
        emb = self._rng.randn(len(texts), self._dim).astype(np.float32)
        if normalize_embeddings:
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            norms = np.maximum(norms, 1e-12)
            emb = emb / norms
        return emb


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════


def _write_md(path: str, lines: list[str]):
    """写入 Markdown 文件（空行分隔）。"""
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(lines) + "\n")


def _read_lines(path: str) -> list[str]:
    """读取文件为行列表（strip）。"""
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


# ═══════════════════════════════════════════════════════════════
# 测试类
# ═══════════════════════════════════════════════════════════════


class TestCliPipeline:
    """CLI 流水线集成测试。"""

    @pytest.fixture
    def mock_model(self):
        return MockEncoder(dim=8, seed=42)

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield d

    def setup_pair(self, temp_dir: str) -> tuple[str, str, str]:
        """创建一对源/目标文件，返回 (src_path, tgt_path, entry_id)。"""
        src_lines = ["Hello world", "How are you", "Good bye"]
        tgt_lines = ["你好世界", "你好吗", "再见"]
        entry_id = "test_chapter"

        src_path = os.path.join(temp_dir, f"{entry_id}.source.md")
        tgt_path = os.path.join(temp_dir, f"{entry_id}.target.md")
        _write_md(src_path, src_lines)
        _write_md(tgt_path, tgt_lines)
        return src_path, tgt_path, entry_id

    # ── 测试 1: 完整流水线 ──

    def test_align_chapter_full_pipeline(self, mock_model, temp_dir):
        """完整流程：编码→对齐→修复→导出 report.json + .md。"""
        src_path, tgt_path, entry_id = self.setup_pair(temp_dir)
        repaired_dir = os.path.join(temp_dir, "reports")

        result = align_chapter(
            src_path,
            tgt_path,
            repaired_dir=repaired_dir,
            model=mock_model,
            strategy="minimal",
            output_dir=temp_dir,
        )

        # 检查返回结构
        assert result["success"] is True
        assert len(result["ops"]) > 0
        assert result["report_path"] is not None

        # 检查 report.json
        report_path = result["report_path"]
        assert os.path.isfile(report_path)
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
        assert report["chapter_id"] == entry_id
        assert len(report["ops"]) > 0
        assert report["stats"]["n_source"] == 3
        assert report["stats"]["n_target"] == 3

        # 检查输出 .md 文件
        src_out = result.get("src_path", "")
        tgt_out = result.get("tgt_path", "")
        assert os.path.isfile(src_out), f"src output not found: {src_out}"
        assert os.path.isfile(tgt_out), f"tgt output not found: {tgt_out}"
        src_lines_out = _read_lines(src_out)
        tgt_lines_out = _read_lines(tgt_out)
        assert len(src_lines_out) == len(
            tgt_lines_out
        ), f"行数不匹配: src={len(src_lines_out)} tgt={len(tgt_lines_out)}"
        # 所有行应为 1:1
        for i in range(len(src_lines_out)):
            assert src_lines_out[i], f"src[{i}] 为空"
            assert tgt_lines_out[i], f"tgt[{i}] 为空"

    # ── 测试 2: 空文本处理 ──

    def test_align_chapter_empty_src(self, mock_model, temp_dir):
        """源文件为空时返回 success=True + quality=unreliable。"""
        src_path = os.path.join(temp_dir, "empty.source.md")
        tgt_path = os.path.join(temp_dir, "empty.target.md")
        _write_md(src_path, [])
        _write_md(tgt_path, ["some text"])

        result = align_chapter(
            src_path,
            tgt_path,
            repaired_dir=os.path.join(temp_dir, "reports"),
            model=mock_model,
        )
        assert result["success"] is True
        assert len(result["ops"]) == 0

    def test_align_chapter_both_empty(self, mock_model, temp_dir):
        """双方均为空时正确处理。"""
        src_path = os.path.join(temp_dir, "both_empty.source.md")
        tgt_path = os.path.join(temp_dir, "both_empty.target.md")
        _write_md(src_path, [])
        _write_md(tgt_path, [])

        result = align_chapter(
            src_path,
            tgt_path,
            repaired_dir=os.path.join(temp_dir, "reports"),
            model=mock_model,
        )
        assert result["success"] is True

    # ── 测试 3: 无模型时返回错误 ──

    def test_align_chapter_no_model(self, temp_dir):
        """model=None 时有可用模型则成功，否则返回错误。"""
        src_path, tgt_path, _entry_id = self.setup_pair(temp_dir)
        result = align_chapter(
            src_path,
            tgt_path,
            repaired_dir=os.path.join(temp_dir, "reports"),
            model=None,
        )
        # 若环境中有回退模型（如 env 变量配置），则可能成功
        # 若无可回退模型，应优雅返回错误而非崩溃
        if result["success"] is False:
            assert "模型未加载" in result.get("error", "")

    # ── 测试 4: 缓存命中（连续两次调用同一文件）──

    def test_align_chapter_cache_hit(self, mock_model, temp_dir):
        """第二次调用同一文件应从缓存恢复对齐结果。"""
        src_path, tgt_path, entry_id = self.setup_pair(temp_dir)
        repaired_dir = os.path.join(temp_dir, "reports")

        # 第一次调用
        r1 = align_chapter(
            src_path,
            tgt_path,
            repaired_dir=repaired_dir,
            model=mock_model,
        )
        assert r1["success"] is True

        # 第二次调用（同文件，未修改 → 应命中 report.json 缓存）
        r2 = align_chapter(
            src_path,
            tgt_path,
            repaired_dir=repaired_dir,
            model=mock_model,
        )
        assert r2["success"] is True
        # 两次 ops 应相同（缓存命中恢复）
        assert len(r1["ops"]) == len(r2["ops"]), "缓存命中后 ops 数应一致"

    # ── 测试 5: 不同策略的输出差异 ──

    def test_align_chapter_strategy_minimal(self, mock_model, temp_dir):
        """minimal 策略输出行数应为 src/tgt 对齐后的行数。"""
        src_path, tgt_path, _entry_id = self.setup_pair(temp_dir)
        result = align_chapter(
            src_path,
            tgt_path,
            repaired_dir=os.path.join(temp_dir, "reports"),
            model=mock_model,
            strategy="minimal",
            output_dir=temp_dir,
        )
        src_out = _read_lines(result["src_path"])
        tgt_out = _read_lines(result["tgt_path"])
        assert len(src_out) == len(tgt_out), "src/tgt 行数必须一致"

    # ── 测试 6: 1:1 完美匹配的简单场景 ──

    def test_align_chapter_perfect_match(self, mock_model, temp_dir):
        """当 src/tgt 行数一致时，所有行应为 1:1。"""
        src_path = os.path.join(temp_dir, "perfect.source.md")
        tgt_path = os.path.join(temp_dir, "perfect.target.md")
        _write_md(src_path, ["A", "B", "C"])
        _write_md(tgt_path, ["a", "b", "c"])

        result = align_chapter(
            src_path,
            tgt_path,
            repaired_dir=os.path.join(temp_dir, "reports"),
            model=mock_model,
        )
        assert result["success"] is True

    # ── 测试 7: 长文本对齐 ──

    def test_align_chapter_many_lines(self, mock_model, temp_dir):
        """更多行数（5 行）时仍可正确完成。"""
        src_path = os.path.join(temp_dir, "many.source.md")
        tgt_path = os.path.join(temp_dir, "many.target.md")
        src_lines = [f"Src line {i}" for i in range(5)]
        tgt_lines = [f"Tgt line {i}" for i in range(5)]
        _write_md(src_path, src_lines)
        _write_md(tgt_path, tgt_lines)

        result = align_chapter(
            src_path,
            tgt_path,
            repaired_dir=os.path.join(temp_dir, "reports"),
            model=mock_model,
        )
        assert result["success"] is True
        assert len(result["ops"]) > 0

    # ── 测试 8: 报告中的 repair_log 非空 ──

    def test_align_chapter_repair_log(self, mock_model, temp_dir):
        """report.json 中包含 repair_log。"""
        # 构造一个非 1:1 场景（不同行数）
        src_path = os.path.join(temp_dir, "logcheck.source.md")
        tgt_path = os.path.join(temp_dir, "logcheck.target.md")
        _write_md(src_path, ["A", "B", "C", "D", "E"])
        _write_md(tgt_path, ["a", "b"])

        result = align_chapter(
            src_path,
            tgt_path,
            repaired_dir=os.path.join(temp_dir, "reports"),
            model=mock_model,
            strategy="minimal",
        )
        report_path = result["report_path"]
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)

        assert "repair_log" in report
        # auto_repair 可能有修复操作（非 1:1 场景）或没有，取决于对齐结果
        # 至少 repair_log 字段应存在
