#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Dualign 对齐 + 自动修复基础管线

用法:
  python dualign/demo/demo_cli.py

输出:
  - output/sample.source.repaired.md
  - output/sample.target.repaired.md
  - output/sample.stats.json
"""

from __future__ import annotations

import sys, os, json, time, hashlib
from pathlib import Path

# ── 确保项目根目录在 sys.path 中（支持 python demo/demo_cli.py 直接运行）──
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from demo import (
    RAW_DIR,
    SRC_PATH,
    TGT_PATH,
    CACHE_DIR,
    OUTPUT_DIR,
)

_stats: dict = {}


def _paragraph_break_lines(path: str):
    """加载文本行，同时记录段落分隔（空行）位置。"""
    lines = []
    breaks = []
    with open(path, "r", encoding="utf-8") as f:
        for i, ln in enumerate(f, 1):
            s = ln.strip()
            if s:
                lines.append(s)
            else:
                breaks.append(len(lines))
    return lines, breaks


def run_alignment_pipeline():
    from dualign.core import align, AlignConfig, op_type_str
    from dualign.models.state import AlignmentSnapshot
    from dualign.services.repair import RepairState, RepairService
    from dualign.services.embedding import _try_lazy_load_model
    from dualign.services.embedding_cache import EmbeddingCache
    from dualign.services.cached_encoder import CachedEncoder
    from dualign.common import load_text_lines

    t0 = time.time()

    print("=" * 60)
    print("第 1 步: 对齐 + 自动修复")
    print("=" * 60)

    src_lines, src_breaks = _paragraph_break_lines(str(SRC_PATH))
    tgt_lines = load_text_lines(str(TGT_PATH))
    print(
        f"  原文: {len(src_lines)} 行 ({len(src_breaks)} 段落), 译文: {len(tgt_lines)} 行"
    )

    model = _try_lazy_load_model()
    if model is None:
        print(
            "  X 嵌入模型未加载。请先启动 Ollama: ollama pull leoipulsar/harrier-0.6b"
        )
        return None

    os.makedirs(CACHE_DIR, exist_ok=True)

    # ── CachedEncoder: 统一缓存代理 ──
    db_path = os.path.join(str(CACHE_DIR), "vecs.db")
    ec = EmbeddingCache(db_path)
    cenc = CachedEncoder(model, ec)
    src_emb = cenc.encode(src_lines)
    tgt_emb = cenc.encode(tgt_lines)
    print(f"  V 编码完成（缓存命中率 {cenc.cache_hit_rate:.0%}）")

    cfg = AlignConfig()
    result = align(
        src_lines,
        tgt_lines,
        src_emb,
        tgt_emb,
        config=cfg,
        encode_fn=cenc.encode,
    )

    snapshot = AlignmentSnapshot.from_alignment(result.all_ops, src_lines, tgt_lines)

    from collections import Counter

    tc = Counter(op_type_str(s, t) for s, t, _ in result.all_ops)
    print(f"  对齐: {len(result.all_ops)} 对, {dict(tc)}")
    print(
        f"  真锚点: {result.stats['n_true_anchors']}, 均分: {result.stats['avg_similarity']:.4f}"
    )

    state = RepairState(snapshot)
    repaired = RepairService.auto_repair(state, strategy="src", model=model)
    print(f"  自动修复: {len(repaired.repair_log)} 个操作")

    from dualign.services.ai_repair_agent import ChapterContext

    ctx = ChapterContext.from_repair_state(
        repaired, "ch01", "与天使相遇", strategy="src"
    )
    print(f"  异常对: {len(ctx.reviewable_ids)}")

    _stats["align_elapsed"] = round(time.time() - t0, 1)
    _stats["src_lines"] = len(src_lines)
    _stats["tgt_lines"] = len(tgt_lines)
    _stats["total_pairs"] = len(result.all_ops)
    _stats["auto_repairs"] = len(repaired.repair_log)
    _stats["anomalies"] = len(ctx.reviewable_infos)

    return {
        "repaired": repaired,
        "ctx": ctx,
        "src_lines": src_lines,
        "tgt_lines": tgt_lines,
        "src_breaks": src_breaks,
    }


def main():
    print()
    print("=" * 60)
    print("Dualign 对齐 + 自动修复 - 基础管线")
    print("=" * 60)
    print()

    t_start = time.time()
    data = run_alignment_pipeline()
    if data is None:
        return 1

    from dualign.common import format_markdown_output
    from dualign.services.repair import RepairService

    repaired = data["repaired"]
    src_out, tgt_out = RepairService.render_rows(repaired)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    src_path = OUTPUT_DIR / "sample.source.repaired.md"
    tgt_path = OUTPUT_DIR / "sample.target.repaired.md"
    src_path.write_text(format_markdown_output(src_out), encoding="utf-8")
    tgt_path.write_text(format_markdown_output(tgt_out), encoding="utf-8")

    _stats["total_elapsed"] = round(time.time() - t_start, 1)
    print(f"\n  输出: {src_path}")
    print(f"  输出: {tgt_path}")
    print(f"  总耗时: {_stats['total_elapsed']}s\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
