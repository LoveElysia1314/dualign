"""
模式二：带进度回调
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

适用场景：中等数量文件对（10–200 对），需要进度报告以支持
          CLI 百分比输出或 GUI 进度条。
教学目的：展示如何在外围封装进度回调，无需 Dualign 提供任何
          回调机制——回调是纯消费端的抽象。

前置条件:
  - 同模式一
  - 理解模式一后，此模式增加的是结果聚合 + 进度回调两层封装

┌─────────────────────────────────────────────────────────────┐
│  消费端自行实现 BatchAlignResult 和 batch_align() 函数。     │
│  Dualign 不提供也不承诺提供这些——它们太场景相关了。           │
└─────────────────────────────────────────────────────────────┘
"""

import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from dualign.services.cli_pipeline import align_chapter

# ═══════════════════════════════════════════════════════════════
# 消费端自行定义的结果聚合结构
# ── 字段、统计方式完全由消费端决定
# ═══════════════════════════════════════════════════════════════


@dataclass
class BatchAlignResult:
    """通用的批量对齐结果——消费端自行定义。"""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0  # 幂等跳过
    errors: list[dict] = field(default_factory=list)
    duration: float = 0.0  # 总耗时


# ═══════════════════════════════════════════════════════════════
# 消费端自行实现的批量对齐函数
# ── on_progress 是普通的 Python Callable，不是框架特供
# ═══════════════════════════════════════════════════════════════


def batch_align(
    file_pairs: list[tuple[str, str]],
    output_dir: str = "output/",
    strategy: str = "src",
    *,
    skip_existing: bool = False,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> BatchAlignResult:
    """串行批量对齐，支持进度回调和跳过已对齐。

    Args:
        file_pairs: [(src_path, tgt_path), ...]
        skip_existing: 检查 report.json 存在性，存在则跳过
        on_progress: 进度回调 (current, total, status_msg)
    """
    import os
    from pathlib import Path

    result = BatchAlignResult(total=len(file_pairs))
    t0 = time.time()

    for i, (src, tgt) in enumerate(file_pairs):
        entry_id = Path(src).stem.split(".")[0]
        label = f"[{i+1}/{len(file_pairs)}] {Path(src).name}"

        # ── 可选：跳过已对齐 ──
        if skip_existing:
            report_path = Path(output_dir) / f"{entry_id}.report.json"
            if report_path.is_file():
                result.skipped += 1
                if on_progress:
                    on_progress(i + 1, len(file_pairs), f"{label} → ⏭ 已跳过")
                continue

        # ── 执行对齐 ──
        if on_progress:
            on_progress(i, len(file_pairs), f"{label} → 对齐中...")

        try:
            r = align_chapter(
                src_path=src,
                tgt_path=tgt,
                output_dir=output_dir,
                strategy=strategy,
            )
            if r.get("success"):
                result.succeeded += 1
            else:
                result.failed += 1
                result.errors.append(
                    {
                        "src": src,
                        "error": r.get("error", "unknown"),
                    }
                )
        except Exception as e:
            result.failed += 1
            result.errors.append(
                {
                    "src": src,
                    "error": str(e),
                }
            )

        if on_progress:
            q = (
                r.get("quality", "?")
                if isinstance(r, dict) and r.get("success")
                else "✗"
            )
            on_progress(i + 1, len(file_pairs), f"{label} → {q}")

    result.duration = time.time() - t0
    return result


# ═══════════════════════════════════════════════════════════════
# 使用示例
# ═══════════════════════════════════════════════════════════════

# ── 场景 A：CLI 输出（显示进度百分比）──
# result = batch_align(
#     file_pairs,
#     on_progress=lambda c, t, m: print(f"\r进度 {c}/{t}  {m}", end=""),
# )
# print(f"\n完成: {result.succeeded}/{result.total}  "
#       f"(失败: {result.failed}, 跳过: {result.skipped})  "
#       f"耗时: {result.duration:.1f}s")

# ── 场景 B：PySide6 GUI 进度条 ──
# def on_gui_progress(current, total, msg):
#     window.progress_bar.setValue(int(current / total * 100))
#     window.status_label.setText(msg)
#
# import threading
# thread = threading.Thread(
#     target=lambda: batch_align(file_pairs, on_progress=on_gui_progress),
#     daemon=True,
# )
# thread.start()

# ── 场景 C：跳过已对齐 ──
# result = batch_align(file_pairs, skip_existing=True)
