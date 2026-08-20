#!/usr/bin/env python
"""Dualign report-first Demo.

Every run copies the bundled documents into a fresh temporary workspace. The
Demo writes a work report and optional reader previews there; the tracked
examples under ``demo/raw`` remain immutable.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    from dualign.common import format_markdown_output
    from dualign.demo import get_demo_paths
    from dualign.services.ai_repair_agent import ChapterContext
    from dualign.services.cli_pipeline import align_documents
    from dualign.services.embedding import _try_lazy_load_model
    from dualign.services.report_io import (
        load_report,
        materialize_reader_rows,
        repair_state_from_report,
    )

    document_a, document_b, label = get_demo_paths()
    workspace = Path(document_a).parent
    report_path = workspace / "sample.report.json"

    print("=" * 60)
    print("Dualign 对齐与校订 Demo")
    print("=" * 60)
    print(f"  样例: {label}")
    print(f"  工作区: {workspace}")
    print("  内置样例保持只读，本次操作仅影响上述临时副本。")

    model = _try_lazy_load_model()
    if model is None:
        print("  ✗ 嵌入模型未加载。请先配置并启动嵌入模型。")
        return 1

    started = time.time()
    result = align_documents(
        document_a,
        document_b,
        str(report_path),
        model=model,
        strategy="src",
    )
    if not result.get("success"):
        print(f"  ✗ 对齐失败: {result.get('error', '未知错误')}")
        return 1

    report = load_report(report_path)
    state = repair_state_from_report(report, document_a, document_b)
    context = ChapterContext.from_repair_state(
        state,
        "sample",
        "与天使相遇",
        strategy="src",
    )
    print(f"  关系: {len(report['ops'])}")
    print(f"  自动校订: {len(report['repair_log'])} 个操作")
    print(f"  待审: {len(context.reviewable_ids)} 个关系")
    print(f"  工作报告: {report_path}")

    rows_a, rows_b = materialize_reader_rows(report_path, document_a, document_b)
    preview_dir = workspace / "preview"
    preview_dir.mkdir()
    preview_a = preview_dir / "sample.document-a.md"
    preview_b = preview_dir / "sample.document-b.md"
    preview_a.write_text(format_markdown_output(rows_a), encoding="utf-8")
    preview_b.write_text(format_markdown_output(rows_b), encoding="utf-8")
    print(f"  阅读器预览: {preview_dir}")
    print(f"  耗时: {time.time() - started:.1f}s")
    print("  如需体验覆写，请在 GUI 审查差异后选择“覆写源文档…”。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
