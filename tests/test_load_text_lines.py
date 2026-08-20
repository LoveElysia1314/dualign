"""load_text_lines 基础行为：空白行过滤、缺失文件、CRLF 兼容。

注意：⟢MISSING⟣ 占位符不应在正文文档中出现。若正文出现该符号，
说明数据有问题（占位符被错误写入正文），load_text_lines 不应对其
特殊处理而掩盖问题——按普通非空行对待，让问题暴露。
"""

from __future__ import annotations

import pytest

from dualign.common import load_text_lines

MISSING = "\u27e2MISSING\u27e3"


def test_load_text_lines_skips_blank_lines(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("line1\n\nline2\n\nline3\n", encoding="utf-8")
    assert load_text_lines(str(p)) == ["line1", "line2", "line3"]


def test_load_text_lines_treats_missing_placeholder_as_plain_line(tmp_path):
    """正文中的 ⟢MISSING⟣ 按普通非空行处理（不掩盖数据问题）。"""
    p = tmp_path / "doc.md"
    p.write_text(
        "line1\n\n" + MISSING + "\n\nline2\n",
        encoding="utf-8",
    )
    lines = load_text_lines(str(p))
    assert lines == ["line1", MISSING, "line2"]


def test_load_text_lines_strips_whitespace(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("  a  \n\tb\t\n", encoding="utf-8")
    assert load_text_lines(str(p)) == ["a", "b"]


def test_load_text_lines_missing_file_returns_empty(tmp_path):
    p = tmp_path / "missing.md"
    assert load_text_lines(str(p)) == []


def test_load_text_lines_preserves_crlf(tmp_path):
    p = tmp_path / "doc.md"
    p.write_bytes(("a\r\n\r\nb\r\n").encode("utf-8"))
    assert load_text_lines(str(p)) == ["a", "b"]
