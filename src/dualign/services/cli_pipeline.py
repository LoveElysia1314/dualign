"""
Dualign 0.7.0 — CLI 对齐流水线

将 common.py 中与具体业务逻辑相关的对齐流水线提取到此，
common.py 回归纯工具函数库角色。

职责：编码 → 对齐 → 自动修复 → 导出文件 + report.json
"""

from __future__ import annotations

import os
import json
from pathlib import Path

from dualign.config import (
    get_report_cache_dir,
    get_embedding_cache_dir,
)
from dualign.common import (
    load_text_lines,
    content_hash,
)
from dualign.core import (
    align,
    AlignConfig,
    AlignmentResult,
)
from dualign.services.embedding_cache import EmbeddingCache
from dualign.services.cached_encoder import CachedEncoder


def align_chapter(
    src_path: str,
    tgt_path: str,
    repaired_dir: str = "",
    model=None,
    config=None,
    strategy: str = "src",
    output_dir: str = "",
) -> dict:
    """对齐单个章节。编码 → 对齐 → 自动修复 → 导出 repaired 文件 + report.json。

    repaired_dir 为空时使用默认报告缓存目录（get_report_cache_dir()）。
    output_dir 控制修复后 .md 的输出位置：
      - 非空时 .md 写到 output_dir
      - 空时写到 repaired_dir（兼容旧行为）

    Args:
        strategy: 自动修复策略 ("src" / "tgt" / "minimal")
    """
    if not repaired_dir:
        repaired_dir = get_report_cache_dir()
    if not output_dir:
        output_dir = repaired_dir

    sl = load_text_lines(src_path)
    tl = load_text_lines(tgt_path)

    entry_id = Path(src_path).stem.split(".")[0]
    cfg = config or AlignConfig()
    cache_dir = get_embedding_cache_dir(entry_id)

    # ── 空文本 ──
    if not sl or not tl:
        return _handle_empty(sl, tl, entry_id, repaired_dir)

    # ── 尝试嵌入缓存（SQLite 行级）──
    import os as _os

    db_path = _os.path.join(cache_dir, "vecs.db")
    ec = EmbeddingCache(db_path)

    model = _ensure_model(model)
    if model is None:
        return {"success": False, "error": "模型未加载"}

    # ── CachedEncoder: 统一缓存代理 ──
    cenc = CachedEncoder(model, ec)
    src_emb = cenc.encode(sl)
    tgt_emb = cenc.encode(tl)

    src_hash = content_hash(sl)
    tgt_hash = content_hash(tl)

    # ── 尝试从 report.json 恢复对齐结果 ──
    result = _try_load_cached_result(entry_id, repaired_dir, src_hash, tgt_hash)

    if result is None:
        result = align(
            sl,
            tl,
            src_emb,
            tgt_emb,
            cfg,
            encode_fn=cenc.encode,
        )

    # ── 质量评估 + 自动修复 + 报告导出 ──
    return _build_report(
        result,
        sl,
        tl,
        src_hash,
        tgt_hash,
        entry_id,
        repaired_dir,
        model,
        strategy,
        cfg,
        output_dir=output_dir,
    )


# ═══════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════


def _ensure_model(model):
    """确保模型已加载。"""
    if model is not None:
        return model
    from dualign.services.embedding import _try_lazy_load_model, load_model_for_provider

    model = _try_lazy_load_model()
    if model is None:
        try:
            model = load_model_for_provider()
        except Exception:
            return None
    return model


def _handle_empty(sl, tl, entry_id: str, repaired_dir: str):
    """处理空文本对。"""
    from dualign.services.quality_gate import (
        QUALITY_UNRELIABLE,
        REJECTION_LOW_ANCHOR_DENSITY,
    )

    indicators = {
        "anchor_density": 0.0,
        "gap_row_ratio": 0.0,
        "n_overflow_rows": 0,
        "n_src": len(sl),
        "n_tgt": len(tl),
    }
    empty_report = {
        "chapter_id": entry_id,
        "created_at": "",
        "src_hash": content_hash(sl),
        "tgt_hash": content_hash(tl),
        "quality": {
            "level": QUALITY_UNRELIABLE,
            "rejections": [REJECTION_LOW_ANCHOR_DENSITY],
            "indicators": indicators,
        },
        "ops": [],
        "stats": {"n_true_anchors": 0, "avg_similarity": 0.0},
        "repair_log": [],
    }
    os.makedirs(repaired_dir, exist_ok=True)
    from dualign.common import save_report

    save_report(empty_report, str(Path(repaired_dir) / f"{entry_id}.report.json"))
    return {
        "success": True,
        "ops": [],
        "report_path": str(Path(repaired_dir) / f"{entry_id}.report.json"),
        "quality": QUALITY_UNRELIABLE,
        "rejections": [REJECTION_LOW_ANCHOR_DENSITY],
    }


def _try_load_cached_result(entry_id, repaired_dir, src_hash, tgt_hash):
    """尝试从 report.json 恢复对齐结果。stats 为空时视为缓存未命中。"""
    report_path = Path(repaired_dir) / f"{entry_id}.report.json"
    if not report_path.is_file():
        return None
    try:
        with open(report_path, encoding="utf-8") as f:
            r = json.load(f)
        saved_src = r.get("src_hash", "")
        saved_tgt = r.get("tgt_hash", "")
        if saved_src != src_hash or saved_tgt != tgt_hash:
            return None
        ops_raw = r.get("ops", [])
        if not ops_raw:
            return None
        stats = r.get("stats", {})
        if not stats:
            return None  # 旧格式无 stats，强制重新对齐
        return AlignmentResult(
            all_ops=[
                (
                    tuple(o["s"]),
                    tuple(o["t"]),
                    float(o["sc"]),
                )
                for o in ops_raw
            ],
            anchors=[],
            anchor_op_indices={},
            stats=stats,
        )
    except Exception:
        return None


def _build_report(
    result,
    sl,
    tl,
    src_hash,
    tgt_hash,
    entry_id,
    repaired_dir,
    model,
    strategy,
    config,
    output_dir: str = "",
):
    """质量评估 → 自动修复 → 报告导出。"""
    if not output_dir:
        output_dir = repaired_dir

    from dualign.services.quality_gate import (
        QUALITY_UNRELIABLE,
        assess_alignment_quality,
        _gap_row_ratio,
    )
    from dualign.common import save_report
    from dualign.services.repair import RepairState, RepairService
    from dualign.models.state import AlignmentSnapshot
    from dualign.common import format_markdown_output

    n_src = len(sl)
    n_tgt = len(tl)
    report_path = Path(repaired_dir) / f"{entry_id}.report.json"

    # ── stats: 优先 result.stats，为空或缺失 n_true_anchors 时从 ops 推导 ──
    stats = result.stats if result.stats else {}
    if not stats.get("n_true_anchors"):
        # 从 ops 重新计算统计（缓存命中时 stats 可能为空或为旧格式）
        # 统计参与锚点的去重行数
        anchors_raw = getattr(result, "anchors", []) or []
        src_anchor_lines = {
            s for (s,), (t,), _ in anchors_raw if len(s) == 1 and len(t) == 1
        }
        tgt_anchor_lines = {
            t for (s,), (t,), _ in anchors_raw if len(s) == 1 and len(t) == 1
        }
        n_anchor_lines = len(src_anchor_lines) + len(tgt_anchor_lines)
        from dualign.core.aligner import _build_stats

        stats = _build_stats(
            n_src,
            n_tgt,
            result.all_ops,
            n_restricted_ops=len(result.all_ops),
            n_anchors=n_anchor_lines,
            elapsed=0,
            t_sim=0,
            t_info=0,
            t_anchor=0,
            t_dp=0,
        )

    gap_ratio = _gap_row_ratio(result.all_ops, n_src, n_tgt)
    n_overflow = result.stats.get("n_overflow_rows", 0) if result.stats else 0

    assessment = assess_alignment_quality(
        stats,
        n_src,
        n_tgt,
        gap_row_ratio=gap_ratio,
        n_overflow_rows=n_overflow,
    )
    quality = assessment["quality"]
    rejections = assessment.get("rejections", [])
    indicators = assessment["indicators"]

    # ── 公共 report 结构 ──
    import time as _time

    def _make_report(repair_log_actions=None):
        return {
            "chapter_id": entry_id,
            "created_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
            "src_hash": src_hash,
            "tgt_hash": tgt_hash,
            "quality": {
                "quality": quality,
                "rejections": rejections,
                "indicators": indicators,
            },
            "ops": [
                {"s": list(s), "t": list(t), "sc": round(float(sc), 4)}
                for s, t, sc in result.all_ops
            ],
            "stats": stats,
            "repair_log": (
                [a.to_dict() for a in repair_log_actions]
                if repair_log_actions is not None
                else []
            ),
        }

    if quality == QUALITY_UNRELIABLE:
        report_data = _make_report(repair_log_actions=[])
        save_report(report_data, str(report_path))
        return {
            "success": True,
            "ops": result.all_ops,
            "report_path": str(report_path),
            "src_path": "",
            "tgt_path": "",
            "quality": quality,
            "rejections": rejections,
        }

    # ── 自动修复 ──
    snapshot = AlignmentSnapshot.from_alignment(result.all_ops, sl, tl)
    state = RepairState(snapshot)
    repaired = RepairService.auto_repair(state, strategy=strategy, model=model)

    src_out, tgt_out = RepairService.render_rows(repaired)
    os.makedirs(output_dir, exist_ok=True)

    spath = Path(output_dir) / f"{entry_id}.source.md"
    tpath = Path(output_dir) / f"{entry_id}.target.md"

    with open(spath, "w", encoding="utf-8") as f:
        f.write(format_markdown_output(src_out))
    with open(tpath, "w", encoding="utf-8") as f:
        f.write(format_markdown_output(tgt_out))

    report_data = _make_report(repair_log_actions=repaired.repair_log)
    save_report(report_data, str(report_path))

    return {
        "success": True,
        "ops": result.all_ops,
        "report_path": str(report_path),
        "src_path": str(spath),
        "tgt_path": str(tpath),
        "quality": quality,
        "rejections": rejections,
    }
