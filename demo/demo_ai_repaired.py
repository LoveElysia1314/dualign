#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Dualign — AI Repair Agent Demo

端到端流程: 对齐 → 自动修复 → AI 审校 → 评分 → 输出

前置条件:
  - DEEPSEEK_API_KEY 环境变量
  - Ollama 已启动 + 嵌入模型（默认 leoipulsar/harrier-0.6b）
"""

from __future__ import annotations

import sys
import os
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_DEMO = Path(__file__).resolve().parent
if str(_DEMO) not in sys.path:
    sys.path.insert(0, str(_DEMO))

from demo import SRC_PATH, TGT_PATH, CACHE_DIR, OUTPUT_DIR

# AI 审校状态写入路径
_REPORT_PATH = CACHE_DIR / "reports" / "sample.report.json"

# ═══════════════════════════════════════════════════════════════
# 1. 标准答案（评分用）
# ═══════════════════════════════════════════════════════════════

EXPECTED = [
    (1, "edit"),
    (6, ["ok", "merge"]),
    (8, "edit"),
    (21, ["ok", "merge", "edit"]),
    (32, "delete"),
    (38, ["ok", "split"]),
]


def score_actions(actions: list, expected: list) -> tuple:
    """对比 AI 操作与标准答案。"""
    actual = sorted((a.op_index, a.kind) for a in actions)
    exp_ids = {e[0] for e in expected}

    def kind_matches(ek, ak):
        return ak in ek if isinstance(ek, list) else ek == ak

    passed = sum(
        1
        for si, k in expected
        if any(si == asi and kind_matches(k, ak) for asi, ak in actual)
    )
    failed = len(expected) - passed
    extra = [(si, k) for si, k in actual if si not in exp_ids]
    return passed, failed, extra


# ═══════════════════════════════════════════════════════════════
# 2. 对齐流水线
# ═══════════════════════════════════════════════════════════════


def run_alignment(strategy: str = "src"):
    """嵌入编码 → 对齐 → 自动修复 → 审校上下文。"""
    from collections import Counter
    from dualign.core import align, AlignConfig, op_type_str
    from dualign.models.state import AlignmentSnapshot
    from dualign.services.repair import RepairState, RepairService
    from dualign.services.ai_repair_agent import build_chapter_context
    from dualign.services.embedding import _try_lazy_load_model
    from dualign.services.embedding_cache import EmbeddingCache
    from dualign.services.cached_encoder import CachedEncoder
    from dualign.common import load_text_lines

    t0 = time.time()
    src_lines = load_text_lines(str(SRC_PATH))
    tgt_lines = load_text_lines(str(TGT_PATH))
    # 估算段落数：空行分隔
    src_breaks = []
    try:
        with open(str(SRC_PATH), "r", encoding="utf-8") as _f:
            src_breaks = [i for i, ln in enumerate(_f, 1) if not ln.strip()]
    except Exception:
        pass
    print(
        f"  原文: {len(src_lines)} 行 ({len(src_breaks)} 段落), 译文: {len(tgt_lines)} 行"
    )

    model = _try_lazy_load_model()
    if model is None:
        print("  X 嵌入模型未加载")
        return None, None, None

    os.makedirs(CACHE_DIR, exist_ok=True)

    # ── 使用 EmbeddingCache（SQLite 行级）替代旧 .npz ──
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
    tc = Counter(op_type_str(s, t) for s, t, _ in result.all_ops)
    print(f"  对齐: {len(result.all_ops)} 对, {dict(tc)}")
    print(
        f"  真锚点: {result.stats['n_true_anchors']}, 均分: {result.stats['avg_similarity']:.4f}"
    )

    raw_state = RepairState(snapshot)
    repaired_state = RepairService.auto_repair(
        raw_state, strategy=strategy, model=model
    )
    print(f"  自动修复: {len(repaired_state.repair_log)} 个操作")

    ctx = build_chapter_context(
        repaired_state,
        strategy=strategy,
        model=model,
        chapter_id="ch01",
        chapter_title="与天使相遇",
    )
    print(f"\n  待审: {len(ctx.reviewable_infos)} Snap\n")
    for info in ctx.reviewable_infos:
        sigs = ", ".join(info.signals) or "-"
        print(f"  [{info.snap_id}] {info.n_src_rows}:{info.n_tgt_rows} [{sigs}]")

    return repaired_state, ctx, t0


# ═══════════════════════════════════════════════════════════════
# 3. AI 审校
# ═══════════════════════════════════════════════════════════════


def run_agent(ctx, strategy: str = "src", state=None):
    """运行 AI 审校 Agent，返回 actions + token 统计。"""
    from dualign.services.ai_repair_agent import AiRepairAgent, AgentEvent

    agent = AiRepairAgent(
        backend="deepseek", max_turns=20, verbose=False, strategy=strategy
    )
    tok = {"in": 0, "out": 0, "cache": 0}
    turn_log = []

    def on_event(evt: AgentEvent):
        if evt.type == "llm_response":
            u = evt.usage or {}
            tok["in"] += u.get("prompt_tokens", 0)
            tok["out"] += u.get("completion_tokens", 0)
            tok["cache"] += u.get("cached_tokens", 0)
        elif evt.type == "tool_start":
            _print_tool(evt)
        elif evt.type == "tool_result":
            print(f"      ↳ {evt.tool_result[:120].replace(chr(10), ' ')}")
        elif evt.type == "done":
            turn_log[:] = getattr(evt, "turn_log", []) or []

    ts = time.time()
    actions = agent.run(ctx, on_event=on_event, initial_state=state)
    elapsed = time.time() - ts
    return actions, tok, elapsed, turn_log


def _print_tool(evt):
    from dualign.services.ai_repair_agent import _ACTION_ICON

    name = evt.tool_name
    args = evt.tool_args or {}
    icon = _ACTION_ICON.get(name, "❓")
    if name in ("ok", "edit", "merge", "delete", "flag"):
        detail = ""
        if name == "edit":
            new = args.get("new_tgt", args.get("new_src", []))
            if isinstance(new, list) and new:
                detail = f" → {new[0][:50]}"
        elif name == "flag":
            detail = f" note={args.get('note', '')[:40]}"
        target = args.get("snap_range", args.get("snap_id", "?"))
        print(f"    {icon} {name}({target}){detail}")
    elif name == "view":
        print(f"    {icon} view(pair_spec={args.get('pair_spec', '?')})")
    else:
        print(f"    ? {name}({args})")


# ═══════════════════════════════════════════════════════════════
# 4. 报告与输出
# ═══════════════════════════════════════════════════════════════


def save_reports(ctx, actions, turn_log, tok, elapsed):
    from dualign.services.ai_repair_agent import (
        compute_cost,
        format_action,
        dump_agent_debug,
        dump_agent_raw,
    )
    from dualign.common import set_ai_review

    passed, failed, extra = score_actions(actions, EXPECTED)
    hit_rate = f"{passed}/{len(EXPECTED)}"
    cost = compute_cost(tok["in"], tok["cache"], tok["out"])
    turns = len(turn_log)

    # ── 写入 AI 审校状态到 report.json ──
    if _REPORT_PATH.is_file():
        set_ai_review(str(_REPORT_PATH), "completed", "")

    print("\n  ── 最终操作列表 ──")
    for a in actions:
        print(format_action(a, ctx))

    print(f"\n  V {hit_rate} 标准答案命中")
    if failed:
        print(f"  X {failed} 项偏离")
    for si, k in extra:
        print(f"  + Snap[{si}] {k}")
    print(f"  轮次: {turns}  耗时: {elapsed:.1f}s")
    print(f"  Token: 输入 {tok['in']} (缓存 {tok['cache']}) -> 输出 {tok['out']}")
    print(f"  费用: ${cost:.6f}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _debug_path = str(OUTPUT_DIR / "sample.review.debug.md")
    _raw_path = str(OUTPUT_DIR / "sample.review.raw.md")
    dump_agent_debug(
        ctx,
        actions,
        turn_log,
        _debug_path,
        prompt_tokens=tok["in"],
        cache_tokens=tok["cache"],
        completion_tokens=tok["out"],
        elapsed=elapsed,
        extra_info=f"标准答案 {hit_rate}",
    )
    dump_agent_raw(
        ctx,
        actions,
        turn_log,
        _raw_path,
        prompt_tokens=tok["in"],
        cache_tokens=tok["cache"],
        completion_tokens=tok["out"],
        elapsed=elapsed,
    )
    print(f"\n  Debug: {_debug_path}")
    print(f"  Raw:   {_raw_path}")


def render_output(state, actions):
    from dualign.services.repair import RepairService

    final_state = state
    for a in actions:
        final_state = final_state.apply(a)
    sp = str(OUTPUT_DIR / "sample.source.ai_repaired.md")
    tp = str(OUTPUT_DIR / "sample.target.ai_repaired.md")
    RepairService.render_to_files(final_state, sp, tp)
    print(f"  V src: {sp}")
    print(f"  V tgt: {tp}")


# ═══════════════════════════════════════════════════════════════
# 5. 主流程
# ═══════════════════════════════════════════════════════════════


def main() -> int:
    try:
        if not os.environ.get("DEEPSEEK_API_KEY"):
            print("=" * 50)
            print("❌ DEEPSEEK_API_KEY 未设置")
            print("   请在环境变量中设置您的 DeepSeek API Key:")
            print("   $env:DEEPSEEK_API_KEY = 'sk-your-key-here'")
            print("=" * 50)
            return 1

        print("=" * 60)
        print("第 1 步: 对齐流水线")
        print("=" * 60)
        state, ctx, t0 = run_alignment(strategy="src")
        if state is None:
            return 1

        # ── 无待审异常 → 写入 ai_review: skipped ──
        if not ctx.reviewable_ids:
            print("\n  待审列表为空，跳过 AI 审校")
            from dualign.common import set_ai_review

            if _REPORT_PATH.is_file():
                set_ai_review(str(_REPORT_PATH), "skipped", "无待审核异常")
            return 0

        print("\n" + "=" * 60)
        print("Agent")
        print("=" * 60)
        actions, tok, elapsed, turn_log = run_agent(ctx, strategy="src", state=state)

        print("\n" + "=" * 60)
        print("结果")
        print("=" * 60)
        save_reports(ctx, actions, turn_log, tok, elapsed)

        if actions:
            render_output(state, actions)

        print(f"\n  turns={len(turn_log)}  time={elapsed:.1f}s  tok_in={tok['in']}")
        return 0
    except RuntimeError as e:
        print(f"\n{'='*50}")
        print(str(e))
        print(f"{'='*50}\n")
        return 1
    except Exception as e:
        print(f"\n❌ 未预期的错误: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
