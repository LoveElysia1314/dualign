"""
Dualign — 质量门控

文档级质量评估（G1/G2/G3）+ 文本对级异常检测。
所有阈值可配置，消费端可通过 QualityGateConfig 覆写。

G1 → anchor_density 不足 → 对齐不可靠
G2 → gap_row_ratio 过高 → 间隙行占比异常（孤行 1:0 + 0:1）
G3 → n_overflow_rows > 0 → 合并编码触顶
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class QualityGateConfig:
    """质量门控配置 — 所有阈值可被消费端覆写。"""

    # G1: 锚点密度
    anchor_density_min: float = 0.60

    # G2: 孤行比例阈值
    gap_row_ratio_max: float = 0.10

    # 离群低分
    zscore_k: float = 3.0
    zscore_min_score: float = 0.6


# ── 文档级质量等级 ──
QUALITY_OK = "ok"
QUALITY_UNRELIABLE = "unreliable"
QUALITY_GAP_DOMINATED = "gap_dominated"

# ── 拒绝理由（可多项叠加）──
REJECTION_LOW_ANCHOR_DENSITY = "low_anchor_density"
REJECTION_GAP_DOMINATED = "gap_dominated"
REJECTION_MERGE_OVERFLOW = "merge_overflow"


def _gap_row_ratio(all_ops, n_src: int, n_tgt: int) -> float:
    """纯间隙行（孤立行 1:0 + 0:1）占总行数比例。"""
    n_orphan = sum(len(s) for s, t, _ in all_ops if not t) + sum(
        len(t) for s, t, _ in all_ops if not s
    )
    denom = n_src + n_tgt
    return n_orphan / denom if denom > 0 else 0.0


def assess_alignment_quality(
    stats: dict,
    n_src: int,
    n_tgt: int,
    gap_row_ratio: float,
    n_overflow_rows: int = 0,
    config: Optional[QualityGateConfig] = None,
) -> dict:
    """G1/G2/G3 质量门控。

    G1 → anchor_density 不足，直接返回 unreliable。
    G2 → gap_row_ratio 超阈值。
    G3 → merge overflow 独立检测，始终写入，不与 G2 冲突。

    Returns:
        {
            "quality": str,
            "rejections": list[str],
            "indicators": { "anchor_density", "gap_row_ratio", "n_overflow_rows", "n_src", "n_tgt" }
        }
    """
    cfg = config or QualityGateConfig()
    anchor_density = stats.get("anchor_density")
    if anchor_density is None:
        n_true = stats.get("n_true_anchors", 0)
        n_total = n_src + n_tgt
        anchor_density = n_true / n_total if n_total > 0 else 0.0

    indicators = {
        "anchor_density": round(anchor_density, 4),
        "gap_row_ratio": round(gap_row_ratio, 4),
        "n_overflow_rows": n_overflow_rows,
        "n_src": n_src,
        "n_tgt": n_tgt,
    }

    # G1 — 锚点不足
    if anchor_density < cfg.anchor_density_min:
        return {
            "quality": QUALITY_UNRELIABLE,
            "rejections": [REJECTION_LOW_ANCHOR_DENSITY],
            "indicators": indicators,
        }

    # G2/G3 并行独立
    rejections = []
    quality = QUALITY_OK

    if gap_row_ratio >= cfg.gap_row_ratio_max:
        quality = QUALITY_GAP_DOMINATED
        rejections.append(REJECTION_GAP_DOMINATED)

    if n_overflow_rows > 0:
        rejections.append(REJECTION_MERGE_OVERFLOW)

    return {
        "quality": quality,
        "rejections": rejections,
        "indicators": indicators,
    }


def is_statistical_low_score(
    score: float,
    scores_1to1: list,
    k: float = 3.0,
    min_score: float = 0.6,
) -> bool:
    """Z-score 离群低分检测。

    两个条件同时满足：
      1. Z > k（默认 k=3.0，极保守阈值）
      2. 绝对得分 < min_score（默认 0.6）
    """
    if len(scores_1to1) < 3:
        return False
    if score >= min_score:
        return False
    import numpy as np

    mu = float(np.mean(scores_1to1))
    sigma = float(np.std(scores_1to1, ddof=1))
    if sigma < 1e-8:
        return False
    z = (mu - score) / sigma
    return z > k
