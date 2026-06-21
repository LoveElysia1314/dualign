"""
Dualign 0.7.0 — DP 对齐引擎
=============================
纯函数实现，无 GUI 依赖，可独立单元测试。

算法流水线 (Phase 1→5)
-----------------------
Phase 1: 递归双边信任余量锚点搜索
Phase 2: 受限 DP 补全微锚点（一次性兜底，不递归）
Phase 3: 全局枚举所有合法合并组合（N:1 / 1:M，无 N:M）
Phase 4: 批量编码与评分（CachedEncoder 自动缓存/复用）
Phase 5: 单一 DP 最终决选

设计原则
--------
- 锚点 = 满足双边互惠 + 信任余量的 (i,j) 对，作为"先验"切分问题
- 递归锚点搜索：分段后竞争对手减少 → 被遮挡锚点浮现
- 合并组合规则：连续 · ≥2行 · 恰好含 1 个基准行 · 长度 ≤ θ
- 仅一个 DP（Phase 5），不再区分受限/完整双轨

验证设计: 各假设的验证方法、数据需求、预期基线见 docs/validation-design.md。
当前在私有轻小说语料上的单次摸底: 语义分离 Cohen d=1.93，锚点共识 99.9%，
trust margin 对高度平行语料影响极小——其真实价值需在非逐句对应数据上验证。
"""

import sys
import time
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Set

import numpy as np

from .punctuation import PunctuationHandler

logger = logging.getLogger(__name__)
if not logger.handlers and not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
elif not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False


# ── 对齐器核心版本 ──
ALIGN_CORE_VERSION = "0.7.0"

# ── 双边信任余量锚点参数 ──
ANCHOR_MARGIN_SLOPE = 0.10
ANCHOR_MARGIN_INTERCEPT = 0.05
ANCHOR_MIN_SCORE = 0.60

# ── Phase 3 合并组合约束 ──
MERGE_LENGTH_LIMIT = 20
MERGE_MIN_LENGTH = 2

# ── 容器聚合上限 ──
MAX_CONTAINER_SIZE = 10


# ═══════════════════════════════════════════════════════════════
# 数据类型
# ═══════════════════════════════════════════════════════════════


@dataclass
class AlignConfig:
    """对齐参数配置"""

    allow_deletions: bool = True
    allow_insertions: bool = True
    allow_merge: bool = True


@dataclass
class AlignmentResult:
    """对齐结果"""

    all_ops: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]]
    anchors: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]]
    anchor_op_indices: dict
    stats: dict
    sim_matrix: Optional[np.ndarray] = None


# ═══════════════════════════════════════════════════════════════
# 底层工具函数
# ═══════════════════════════════════════════════════════════════


def count_punct_info(text: str) -> int:
    """统计文本中标点符号数量，作为语言无关的信息量代理。"""
    return PunctuationHandler.count_punctuation_line(text)


def op_type_str(s_tuple, t_tuple) -> str:
    ls, lt = len(s_tuple), len(t_tuple)
    if ls == 1 and lt == 1:
        return "1:1"
    if ls > 1 and lt >= 1:
        return f"{ls}:1"
    if ls >= 1 and lt > 1:
        return f"1:{lt}"
    if ls > 0 and lt == 0:
        return f"{ls}:0"
    if ls == 0 and lt > 0:
        return f"0:{lt}"
    if ls == 0 and lt == 0:
        return "0:0"
    return "?:?"


def _normalize(v: np.ndarray) -> np.ndarray:
    """L2 归一化，零向量保持不变。"""
    norm = np.linalg.norm(v)
    return v / norm if norm > 1e-12 else v


def _normalize_batch(v: np.ndarray) -> np.ndarray:
    """批量 L2 归一化，每行独立。"""
    norm = np.linalg.norm(v, axis=1, keepdims=True)
    norm = np.maximum(norm, 1e-12)
    return v / norm


# ═══════════════════════════════════════════════════════════════
# 核心评分函数
# ═══════════════════════════════════════════════════════════════


def pair_score(
    src_emb: np.ndarray,
    tgt_emb: np.ndarray,
    src_indices: Tuple[int, ...],
    tgt_indices: Tuple[int, ...],
    encode_fn=None,
    src_texts: Optional[List[str]] = None,
    tgt_texts: Optional[List[str]] = None,
) -> float:
    """1:1 点积评分；N:1/1:M 拼接后重新编码再评分。"""
    if not src_indices or not tgt_indices:
        return 0.0

    if len(src_indices) > 1 or len(tgt_indices) > 1:
        if encode_fn is None:
            return 0.0
        src_joined = _smart_join_lines(
            [src_texts[i] for i in src_indices] if src_texts else []
        )
        tgt_joined = _smart_join_lines(
            [tgt_texts[j] for j in tgt_indices] if tgt_texts else []
        )
        embs = encode_fn([src_joined, tgt_joined])
        vs = _normalize(embs[0])
        vt = _normalize(embs[1])
        return float(np.dot(vs, vt))

    if len(src_indices) == 1 and len(tgt_indices) == 1:
        vs = src_emb[src_indices[0]]
        vt = tgt_emb[tgt_indices[0]]
        return float(np.dot(vs, vt))

    return 0.0


# ═══════════════════════════════════════════════════════════════
# 锚点搜索（双边信任余量 + 单调链 DP）
# ═══════════════════════════════════════════════════════════════


def bilateral_trust_margin(score: float) -> float:
    """双边信任余量公式: margin = 0.10 × score - 0.05"""
    if score < ANCHOR_MIN_SCORE:
        return float("inf")
    return ANCHOR_MARGIN_SLOPE * score - ANCHOR_MARGIN_INTERCEPT


def find_bilateral_anchors(
    sim_matrix: np.ndarray,
) -> List[Tuple[Tuple[int, ...], Tuple[int, ...], float]]:
    """双边信任余量锚点搜索（向量化）。"""
    n, m = sim_matrix.shape

    src_top1 = np.max(sim_matrix, axis=1)
    tgt_top1 = np.max(sim_matrix, axis=0)

    src_margins = np.where(
        src_top1 >= ANCHOR_MIN_SCORE,
        ANCHOR_MARGIN_SLOPE * src_top1 - ANCHOR_MARGIN_INTERCEPT,
        np.inf,
    )
    tgt_margins = np.where(
        tgt_top1 >= ANCHOR_MIN_SCORE,
        ANCHOR_MARGIN_SLOPE * tgt_top1 - ANCHOR_MARGIN_INTERCEPT,
        np.inf,
    )

    src_pass = sim_matrix >= (src_top1 - src_margins).reshape(-1, 1)
    tgt_pass = sim_matrix >= (tgt_top1 - tgt_margins).reshape(1, -1)
    min_pass = sim_matrix >= ANCHOR_MIN_SCORE
    mask = src_pass & tgt_pass & min_pass

    rows, cols = np.where(mask)
    candidates = [
        ((int(i),), (int(j),), float(sim_matrix[i, j])) for i, j in zip(rows, cols)
    ]
    return candidates


def select_monotonic_anchors_weighted(
    anchors: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]],
) -> List[Tuple[Tuple[int, ...], Tuple[int, ...], float]]:
    """加权单调链 DP：保持 src/tgt 严格递增，最大化置信度总和。"""
    if not anchors:
        return []
    N = len(anchors)
    dp = np.zeros(N, dtype=np.float64)
    prev = -np.ones(N, dtype=int)

    for i in range(N):
        dp[i] = anchors[i][2]
        prev[i] = -1
        ai_src_end = anchors[i][0][-1] if anchors[i][0] else -1
        ai_tgt_end = anchors[i][1][-1] if anchors[i][1] else -1

        for j in range(i):
            aj_src_end = anchors[j][0][-1] if anchors[j][0] else -1
            aj_tgt_end = anchors[j][1][-1] if anchors[j][1] else -1
            if aj_src_end < ai_src_end and aj_tgt_end < ai_tgt_end:
                cand = dp[j] + anchors[i][2]
                if cand > dp[i]:
                    dp[i] = cand
                    prev[i] = j

    best_idx = int(np.argmax(dp))
    result = []
    while best_idx != -1:
        result.append(anchors[best_idx])
        best_idx = prev[best_idx]
    result.reverse()
    return result


def _validate_coverage(
    all_ops: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]],
    n: int,
    m: int,
) -> None:
    """验证每行恰好使用一次。"""
    used_src = set()
    used_tgt = set()
    dup_src = set()
    dup_tgt = set()
    for s_tuple, t_tuple, _ in all_ops:
        for si in s_tuple:
            if si in used_src:
                dup_src.add(si)
            used_src.add(si)
        for tj in t_tuple:
            if tj in used_tgt:
                dup_tgt.add(tj)
            used_tgt.add(tj)

    if dup_src:
        logger.error(f"源行重复 ({len(dup_src)} 行): {sorted(dup_src)}")
    if dup_tgt:
        logger.error(f"译文行重复 ({len(dup_tgt)} 行): {sorted(dup_tgt)}")

    missing_src = set(range(n)) - used_src
    missing_tgt = set(range(m)) - used_tgt
    if missing_src:
        logger.error(f"源行缺失 ({len(missing_src)} 行): {sorted(missing_src)[:20]}...")
    if missing_tgt:
        logger.error(
            f"译文行缺失 ({len(missing_tgt)} 行): {sorted(missing_tgt)[:20]}..."
        )


def _count_op_types(
    all_ops: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]],
) -> dict:
    """统计各操作类型数量。"""
    counts = {"1to1": 0, "merge": 0, "split": 0, "delete": 0, "insert": 0}
    for s, t, _ in all_ops:
        if len(s) == 1 and len(t) == 1:
            counts["1to1"] += 1
        elif len(s) > 1 and len(t) >= 1:
            counts["merge"] += 1
        elif len(s) >= 1 and len(t) > 1:
            counts["split"] += 1
        elif len(s) > 0 and len(t) == 0:
            counts["delete"] += 1
        elif len(s) == 0 and len(t) > 0:
            counts["insert"] += 1
    return counts


def _normalize_int_types(ops):
    """Ensure no NumPy type leakage."""
    return [
        (tuple(int(x) for x in s), tuple(int(x) for x in t), float(sc))
        for s, t, sc in ops
    ]


def _anchor_index(all_ops, anchors):
    """标注 all_ops 中哪些对应锚点。"""
    anchor_set = {(s[0], t[0]) for s, t, _ in anchors if len(s) == 1 and len(t) == 1}
    result = {}
    for idx, (s_tuple, t_tuple, _) in enumerate(all_ops):
        if len(s_tuple) == 1 and len(t_tuple) == 1:
            if (s_tuple[0], t_tuple[0]) in anchor_set:
                result[idx] = "primary"
    return result


# ═══════════════════════════════════════════════════════════════
# 受限 DP
# ═══════════════════════════════════════════════════════════════


def _restricted_dp(
    sim_matrix: np.ndarray,
    config: AlignConfig,
) -> Tuple[List[Tuple[Tuple[int, ...], Tuple[int, ...], float]], float]:
    """受限 DP：仅 1:1/1:0/0:1 三种移动，无 N:1/1:M。

    回溯标记: 0=1:1, 3=src删除, 4=tgt插入。
    """
    n, m = sim_matrix.shape
    dp = np.full((n + 1, m + 1), -np.inf, dtype=np.float64)
    bt = np.zeros((n + 1, m + 1), dtype=np.int8)
    bt[:] = -1
    dp[0, 0] = 0.0

    for i in range(n + 1):
        for j in range(m + 1):
            cur = dp[i, j]
            if cur == -np.inf:
                continue
            if i < n and j < m:
                s = float(sim_matrix[i, j])
                nv = cur + s
                if nv > dp[i + 1, j + 1]:
                    dp[i + 1, j + 1] = nv
                    bt[i + 1, j + 1] = 0
            if i < n and config.allow_deletions:
                if cur > dp[i + 1, j]:
                    dp[i + 1, j] = cur
                    bt[i + 1, j] = 3
            if j < m and config.allow_insertions:
                if cur > dp[i, j + 1]:
                    dp[i, j + 1] = cur
                    bt[i, j + 1] = 4

    ops = []
    ci, cj = n, m
    while (ci, cj) != (0, 0):
        t = bt[ci, cj]
        if t == -1:
            break
        if t == 0:
            ops.append(((ci - 1,), (cj - 1,), float(sim_matrix[ci - 1, cj - 1])))
            ci -= 1
            cj -= 1
        elif t == 3:
            ops.append(((ci - 1,), (), 0.0))
            ci -= 1
        else:
            ops.append(((), (cj - 1,), 0.0))
            cj -= 1
    ops.reverse()
    return ops, float(dp[n, m])


# ═══════════════════════════════════════════════════════════════
# Phase 1 — 递归双边信任余量锚点搜索
# ═══════════════════════════════════════════════════════════════


def _recursive_anchor_search(
    sim_matrix: np.ndarray,
    n: int,
    m: int,
    depth: int = 0,
    max_depth: int = 100,
) -> List[Tuple[Tuple[int, ...], Tuple[int, ...], float]]:
    """Phase 1: 递归双边信任余量锚点搜索。

    核心洞察：在全局范围内，某个正确的 (i,j) 可能因同行其他高分候选
    压制 top-1 而被否掉。分段后竞争对手减少，可能重新通过双边检查。
    """
    if depth >= max_depth:
        return []

    raw = find_bilateral_anchors(sim_matrix)
    if not raw:
        return []

    anchors = select_monotonic_anchors_weighted(raw)
    if not anchors:
        return []

    all_anchors: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]] = list(anchors)

    prev_s, prev_t = -1, -1
    for a_s, a_t, _a_sc in anchors:
        s, t = a_s[0], a_t[0]

        sub_s, sub_e = prev_s + 1, s
        sub_t_start, sub_t_end = prev_t + 1, t
        sub_n = sub_e - sub_s
        sub_m = sub_t_end - sub_t_start

        if sub_n > 0 and sub_m > 0:
            sub_sim = sim_matrix[sub_s:sub_e, sub_t_start:sub_t_end]
            sub_anchors = _recursive_anchor_search(
                sub_sim, sub_n, sub_m, depth + 1, max_depth
            )
            for sa_s, sa_t, sa_sc in sub_anchors:
                all_anchors.append(
                    (
                        tuple(sub_s + x for x in sa_s),
                        tuple(sub_t_start + y for y in sa_t),
                        sa_sc,
                    )
                )

        prev_s, prev_t = s, t

    sub_s, sub_e = prev_s + 1, n
    sub_t_start, sub_t_end = prev_t + 1, m
    sub_n = sub_e - sub_s
    sub_m = sub_t_end - sub_t_start

    if sub_n > 0 and sub_m > 0:
        sub_sim = sim_matrix[sub_s:sub_e, sub_t_start:sub_t_end]
        sub_anchors = _recursive_anchor_search(
            sub_sim, sub_n, sub_m, depth + 1, max_depth
        )
        for sa_s, sa_t, sa_sc in sub_anchors:
            all_anchors.append(
                (
                    tuple(sub_s + x for x in sa_s),
                    tuple(sub_t_start + y for y in sa_t),
                    sa_sc,
                )
            )

    all_anchors.sort(key=lambda a: (a[0][0], a[1][0]))
    # 子段锚点排序后天然单调——递归产生的锚点严格位于父锚点间隙内。
    return all_anchors


# ═══════════════════════════════════════════════════════════════
# Phase 2 — 受限 DP 补全微锚点
# ═══════════════════════════════════════════════════════════════


def _supplement_micro_anchors(
    anchors: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]],
    sim_matrix: np.ndarray,
    n: int,
    m: int,
    config: AlignConfig,
) -> List[Tuple[Tuple[int, ...], Tuple[int, ...], float]]:
    """Phase 2: 在未覆盖区域用受限 DP 补全微锚点。"""
    if not anchors:
        rdp_ops, _ = _restricted_dp(sim_matrix, config)
        micro = []
        for s, t, sc in rdp_ops:
            if len(s) == 1 and len(t) == 1:
                micro.append((s, t, sc))
        return micro

    micro: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]] = []

    prev_s, prev_t = -1, -1
    sorted_anchors = sorted(anchors, key=lambda a: (a[0][0], a[1][0]))
    for a_s, a_t, _a_sc in sorted_anchors:
        s, t = a_s[0], a_t[0]

        sub_s, sub_e = prev_s + 1, s
        sub_t_start, sub_t_end = prev_t + 1, t
        sub_n = sub_e - sub_s
        sub_m = sub_t_end - sub_t_start

        if sub_n > 0 and sub_m > 0:
            sub_sim = sim_matrix[sub_s:sub_e, sub_t_start:sub_t_end]
            rdp_ops, _ = _restricted_dp(sub_sim, config)
            for r_s, r_t, r_sc in rdp_ops:
                if len(r_s) == 1 and len(r_t) == 1:
                    micro.append(
                        (
                            tuple(sub_s + x for x in r_s),
                            tuple(sub_t_start + y for y in r_t),
                            r_sc,
                        )
                    )

        prev_s, prev_t = s, t

    sub_s, sub_e = prev_s + 1, n
    sub_t_start, sub_t_end = prev_t + 1, m
    sub_n = sub_e - sub_s
    sub_m = sub_t_end - sub_t_start

    if sub_n > 0 and sub_m > 0:
        sub_sim = sim_matrix[sub_s:sub_e, sub_t_start:sub_t_end]
        rdp_ops, _ = _restricted_dp(sub_sim, config)
        for r_s, r_t, r_sc in rdp_ops:
            if len(r_s) == 1 and len(r_t) == 1:
                micro.append(
                    (
                        tuple(sub_s + x for x in r_s),
                        tuple(sub_t_start + y for y in r_t),
                        r_sc,
                    )
                )

    return micro


# ═══════════════════════════════════════════════════════════════
# Phase 3 — 全局枚举所有合法合并组合
# ═══════════════════════════════════════════════════════════════


def _enumerate_merge_combos(
    anchors: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]],
    n: int,
    m: int,
) -> Tuple[
    List[Tuple[Tuple[int, ...], Tuple[int, ...]]],
    List[Tuple[Tuple[int, ...], Tuple[int, ...]]],
]:
    """Phase 3: 从锚点集合出发，全局枚举所有合法合并组合。

    规则：连续 · ≥2行 · 恰好含1个基准行 · ≤MERGE_LENGTH_LIMIT。
    """
    B_S: Set[int] = {a[0][0] for a in anchors}
    B_T: Set[int] = {a[1][0] for a in anchors}

    src_combos: List[Tuple[Tuple[int, ...], Tuple[int, ...]]] = []
    tgt_combos: List[Tuple[Tuple[int, ...], Tuple[int, ...]]] = []

    theta = MERGE_LENGTH_LIMIT
    min_len = MERGE_MIN_LENGTH

    for a_s, a_t, _a_sc in anchors:
        src_idx = a_s[0]
        tgt_idx = a_t[0]

        # ── Src 侧枚举 ──
        left: List[int] = [src_idx]
        for i in range(src_idx - 1, -1, -1):
            if i in B_S:
                break
            if len(left) >= theta:
                break
            left.insert(0, i)
            if len(left) >= min_len:
                src_combos.append((tuple(left), (tgt_idx,)))

        right: List[int] = [src_idx]
        for i in range(src_idx + 1, n):
            if i in B_S:
                break
            if len(right) >= theta:
                break
            right.append(i)
            if len(right) >= min_len:
                src_combos.append((tuple(right), (tgt_idx,)))

        # ── Tgt 侧枚举 ──
        left_t: List[int] = [tgt_idx]
        for j in range(tgt_idx - 1, -1, -1):
            if j in B_T:
                break
            if len(left_t) >= theta:
                break
            left_t.insert(0, j)
            if len(left_t) >= min_len:
                tgt_combos.append(((src_idx,), tuple(left_t)))

        right_t: List[int] = [tgt_idx]
        for j in range(tgt_idx + 1, m):
            if j in B_T:
                break
            if len(right_t) >= theta:
                break
            right_t.append(j)
            if len(right_t) >= min_len:
                tgt_combos.append(((src_idx,), tuple(right_t)))

    return src_combos, tgt_combos


# ═══════════════════════════════════════════════════════════════
# Phase 4 — 批量编码与评分
# ═══════════════════════════════════════════════════════════════


def _build_merge_scores(
    src_combos: List[Tuple[Tuple[int, ...], Tuple[int, ...]]],
    tgt_combos: List[Tuple[Tuple[int, ...], Tuple[int, ...]]],
    src_lines: List[str],
    tgt_lines: List[str],
    src_emb: np.ndarray,
    tgt_emb: np.ndarray,
    encode_fn,
) -> Dict[Tuple[Tuple[int, ...], Tuple[int, ...]], float]:
    """Phase 4: 批量编码所有合并组合并评分。

    encode_fn 应为 CachedEncoder.encode()——自动查缓存/编码/回存。
    """
    scores: Dict[Tuple[Tuple[int, ...], Tuple[int, ...]], float] = {}

    if encode_fn is None:
        return scores

    src_cjk = _precompute_cjk_ends(src_lines)
    tgt_cjk = _precompute_cjk_ends(tgt_lines)

    # ── N:1 (src 合并) ──
    src_texts: List[str] = []
    src_keys: List[Tuple[Tuple[int, ...], Tuple[int, ...]]] = []
    for c_s, c_t in src_combos:
        text = _smart_join_lines_fast(
            [src_lines[i] for i in c_s],
            [src_cjk[i] for i in c_s],
        )
        src_texts.append(text)
        src_keys.append((c_s, c_t))

    if src_texts:
        src_embs_n = _normalize_batch(encode_fn(src_texts))
        for idx, (c_s, c_t) in enumerate(src_keys):
            sc = float(np.dot(src_embs_n[idx], tgt_emb[c_t[0]]))
            scores[(c_s, c_t)] = sc

    # ── 1:M (tgt 合并) ──
    tgt_texts: List[str] = []
    tgt_keys: List[Tuple[Tuple[int, ...], Tuple[int, ...]]] = []
    for c_s, c_t in tgt_combos:
        text = _smart_join_lines_fast(
            [tgt_lines[j] for j in c_t],
            [tgt_cjk[j] for j in c_t],
        )
        tgt_texts.append(text)
        tgt_keys.append((c_s, c_t))

    if tgt_texts:
        tgt_embs_n = _normalize_batch(encode_fn(tgt_texts))
        for idx, (c_s, c_t) in enumerate(tgt_keys):
            sc = float(np.dot(src_emb[c_s[0]], tgt_embs_n[idx]))
            scores[(c_s, c_t)] = sc

    return scores


# ═══════════════════════════════════════════════════════════════
# Phase 5 — 单一 DP 最终决选
# ═══════════════════════════════════════════════════════════════


def _final_dp(
    anchors: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]],
    src_combos: List[Tuple[Tuple[int, ...], Tuple[int, ...]]],
    tgt_combos: List[Tuple[Tuple[int, ...], Tuple[int, ...]]],
    merge_scores: Dict[Tuple[Tuple[int, ...], Tuple[int, ...]], float],
    n: int,
    m: int,
    config: AlignConfig,
) -> List[Tuple[Tuple[int, ...], Tuple[int, ...], float]]:
    """Phase 5: 单一 DP 最终决选。"""
    if n == 0 and m == 0:
        return []

    INF_NEG = -1e308

    ops: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]] = []
    op_ranges: List[Tuple[int, int, int, int, float]] = []

    for a_s, a_t, a_sc in anchors:
        op_ranges.append((a_s[0], a_s[0] + 1, a_t[0], a_t[0] + 1, a_sc))
        ops.append((a_s, a_t, a_sc))

    for c_s, c_t in src_combos:
        sc = merge_scores.get((c_s, c_t), 0.0)
        op_ranges.append((c_s[0], c_s[-1] + 1, c_t[0], c_t[0] + 1, sc))
        ops.append((c_s, c_t, sc))

    for c_s, c_t in tgt_combos:
        sc = merge_scores.get((c_s, c_t), 0.0)
        op_ranges.append((c_s[0], c_s[0] + 1, c_t[0], c_t[-1] + 1, sc))
        ops.append((c_s, c_t, sc))

    if not ops:
        # 无候选：退化为全部孤行
        out: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]] = []
        for i in range(n):
            out.append(((i,), (), 0.0))
        for j in range(m):
            out.append(((), (j,), 0.0))
        return out

    dp = np.full((n + 1, m + 1), INF_NEG, dtype=np.float64)
    # 回溯标记: bt_op=-2=删除, -3=插入, >=0=候选操作索引
    bt_i = np.full((n + 1, m + 1), -1, dtype=np.int32)
    bt_j = np.full((n + 1, m + 1), -1, dtype=np.int32)
    bt_op = np.full((n + 1, m + 1), -1, dtype=np.int32)
    dp[0, 0] = 0.0

    ops_by_start = defaultdict(list)
    for idx, (si, se, ti, te, sc) in enumerate(op_ranges):
        ops_by_start[(si, ti)].append(idx)

    for i in range(n + 1):
        for j in range(m + 1):
            cur = dp[i, j]
            if cur <= INF_NEG / 2:
                continue

            if i < n and config.allow_deletions:
                if cur > dp[i + 1, j]:
                    dp[i + 1, j] = cur
                    bt_i[i + 1, j] = i
                    bt_j[i + 1, j] = j
                    bt_op[i + 1, j] = -2

            if j < m and config.allow_insertions:
                if cur > dp[i, j + 1]:
                    dp[i, j + 1] = cur
                    bt_i[i, j + 1] = i
                    bt_j[i, j + 1] = j
                    bt_op[i, j + 1] = -3

            for op_idx in ops_by_start.get((i, j), []):
                si, se, ti, te, sc = op_ranges[op_idx]
                nv = cur + sc
                if se <= n + 1 and te <= m + 1 and nv > dp[se, te]:
                    dp[se, te] = nv
                    bt_i[se, te] = i
                    bt_j[se, te] = j
                    bt_op[se, te] = op_idx

    result = []
    ci, cj = n, m
    visited = set()
    while (ci, cj) != (0, 0):
        if (ci, cj) in visited:
            break
        visited.add((ci, cj))

        op_idx = int(bt_op[ci, cj])
        pi = int(bt_i[ci, cj])
        pj = int(bt_j[ci, cj])

        if op_idx == -1:
            break
        elif op_idx == -2:
            result.append(((ci - 1,), (), 0.0))
            ci, cj = pi, pj
        elif op_idx == -3:
            result.append(((), (cj - 1,), 0.0))
            ci, cj = pi, pj
        else:
            c_s, c_t, c_sc = ops[op_idx]
            result.append((c_s, c_t, c_sc))
            ci, cj = pi, pj

    result.reverse()
    return result


# ═══════════════════════════════════════════════════════════════
# 主流水线 — align()
# ═══════════════════════════════════════════════════════════════


def align(
    src_lines: List[str],
    tgt_lines: List[str],
    src_emb: np.ndarray,
    tgt_emb: np.ndarray,
    config: Optional[AlignConfig] = None,
    encode_fn=None,
    build_merge_cache: bool = True,
    silent: bool = False,
) -> AlignmentResult:
    """完整对齐流水线。

    Phase 1: 递归双边信任余量锚点搜索
    Phase 2: 受限 DP 补全微锚点
    Phase 3: 全局枚举所有合法合并组合
    Phase 4: 批量编码与评分（CachedEncoder 自动缓存/复用合并嵌入）
    Phase 5: 单一 DP 最终决选
    """
    if config is None:
        config = AlignConfig()

    n, m = len(src_lines), len(tgt_lines)

    if n == 0 or m == 0:
        return AlignmentResult(
            all_ops=[],
            anchors=[],
            anchor_op_indices={},
            stats={
                "n_source": n,
                "n_target": m,
                "n_restricted_ops": 0,
                "n_true_anchors": 0,
                "total_ops": 0,
                "n_1to1": 0,
                "n_merge": 0,
                "n_split": 0,
                "n_delete": 0,
                "n_insert": 0,
                "avg_similarity": 0.0,
                "align_time_s": 0.0,
                "sim_time_s": 0.0,
                "info_time_s": 0.0,
                "anchor_time_s": 0.0,
                "dp_time_s": 0.0,
            },
        )

    t0 = time.perf_counter()
    _log = logger.debug if silent else logger.info
    _log(f"对齐: {n}原文行×{m}译文行 [v{ALIGN_CORE_VERSION}]")

    sim_matrix = np.dot(src_emb, tgt_emb.T)
    t1 = time.perf_counter()

    # Phase 1
    anchors = _recursive_anchor_search(sim_matrix, n, m)
    n_true_anchors = len(anchors)  # 纯真锚点（Phase 1 结果）
    t2 = time.perf_counter()

    # Phase 2
    n_pseudo_anchors = 0
    if config.allow_merge:
        micro = _supplement_micro_anchors(anchors, sim_matrix, n, m, config)
        if micro:
            all_anchors_raw = anchors + micro
            all_anchors_raw.sort(key=lambda a: (a[0][0], a[1][0]))
            anchors = select_monotonic_anchors_weighted(all_anchors_raw)
            n_pseudo_anchors = len(anchors) - n_true_anchors
            if n_pseudo_anchors < 0:
                n_pseudo_anchors = 0  # 加权DP可能丢弃低分真锚点，保底
    t3 = time.perf_counter()

    if not anchors:
        ops: list = []
        for i in range(n):
            ops.append(((i,), (), 0.0))
        for j in range(m):
            ops.append(((), (j,), 0.0))
        ops = _normalize_int_types(ops)
        elapsed = time.perf_counter() - t0
        stats = _build_stats(n, m, ops, 0, 0, elapsed, t1 - t0, 0, t2 - t1, t3 - t2)
        return AlignmentResult(
            all_ops=ops,
            anchors=[],
            anchor_op_indices={},
            stats=stats,
            sim_matrix=sim_matrix,
        )

    # Phase 3
    src_combos: List[Tuple[Tuple[int, ...], Tuple[int, ...]]] = []
    tgt_combos: List[Tuple[Tuple[int, ...], Tuple[int, ...]]] = []
    merge_scores: Dict[Tuple[Tuple[int, ...], Tuple[int, ...]], float] = {}

    if config.allow_merge and build_merge_cache:
        src_combos, tgt_combos = _enumerate_merge_combos(anchors, n, m)
    # Phase 4
    if src_combos or tgt_combos:
        merge_scores = _build_merge_scores(
            src_combos,
            tgt_combos,
            src_lines,
            tgt_lines,
            src_emb,
            tgt_emb,
            encode_fn,
        )
    t5 = time.perf_counter()

    # Phase 5
    all_ops = _final_dp(anchors, src_combos, tgt_combos, merge_scores, n, m, config)
    t6 = time.perf_counter()

    all_ops = _merge_consecutive_solo_ops(all_ops)
    all_ops = _normalize_int_types(all_ops)
    anchors = _normalize_int_types(anchors)
    _validate_coverage(all_ops, n, m)

    anchor_op_indices = _anchor_index(all_ops, anchors)

    # 真锚点行数：每个真锚点涉及 1 条原文行 + 1 条译文行，LIS 保证不重复
    n_anchor_lines = 2 * n_true_anchors

    elapsed = time.perf_counter() - t0
    n_overflow = _count_overflow_rows(anchors, n, m)
    stats = _build_stats(
        n,
        m,
        all_ops,
        len(anchors),
        n_anchor_lines,
        elapsed,
        t1 - t0,
        0,
        t2 - t1 + (t3 - t2),
        t6 - t5,
        n_overflow_rows=n_overflow,
    )

    _log(
        f"对齐完成: {n}原文×{m}译文 "
        f"真锚点{n_true_anchors} 赝锚点{n_pseudo_anchors} "
        f"锚点率{n_anchor_lines/(n+m):.0%} "
        f"均分{stats['avg_similarity']:.3f} "
        f"{elapsed:.1f}s"
    )

    return AlignmentResult(
        all_ops=all_ops,
        anchors=anchors,
        anchor_op_indices=anchor_op_indices,
        stats=stats,
        sim_matrix=sim_matrix,
    )


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _build_stats(
    n,
    m,
    all_ops,
    n_restricted_ops,
    n_anchors,
    elapsed,
    t_sim,
    t_info,
    t_anchor,
    t_dp,
    n_containers=0,
    n_overflow_rows=0,
):
    """构建对齐统计信息字典。"""
    total_sim = sum(op[2] for op in all_ops)
    avg_sim = total_sim / len(all_ops) if all_ops else 0.0
    op_counts = _count_op_types(all_ops)

    n_non11_src = sum(
        len(s) for s, t, sc in all_ops if not (len(s) == 1 and len(t) == 1)
    )
    n_non11_tgt = sum(
        len(t) for s, t, sc in all_ops if not (len(s) == 1 and len(t) == 1)
    )
    n_non11 = n_non11_src + n_non11_tgt
    non11_row_ratio = n_non11 / (n + m) if (n + m) > 0 else 0.0

    n_orphan_rows = sum(len(s) for s, t, _ in all_ops if not t) + sum(
        len(t) for s, t, _ in all_ops if not s
    )
    orphan_row_ratio = n_orphan_rows / (n + m) if (n + m) > 0 else 0.0
    container_ratio = n_containers / max(n, m) if max(n, m) > 0 else 0.0
    n_11 = op_counts["1to1"]

    # 锚点覆盖率 = 参与锚点的去重行数 / (原文行 + 译文行)
    anchor_density = n_anchors / (n + m) if (n + m) > 0 else 0.0

    return {
        "n_source": n,
        "n_target": m,
        "n_restricted_ops": n_restricted_ops,
        "n_true_anchors": n_anchors,
        "anchor_density": round(anchor_density, 4),
        "n_11_anchored": n_11,
        "max_anchor_gap": 0,
        "n_overflow_rows": n_overflow_rows,
        "n_containers": n_containers,
        "n_1to1": n_11,
        "n_merge": op_counts["merge"],
        "n_split": op_counts["split"],
        "n_delete": op_counts["delete"],
        "n_insert": op_counts["insert"],
        "n_fix": op_counts.get("merge", 0)
        + op_counts.get("split", 0)
        + op_counts.get("delete", 0)
        + op_counts.get("insert", 0),
        "avg_similarity": round(avg_sim, 4),
        "align_time_s": round(elapsed, 3),
        "sim_time_s": round(t_sim, 3),
        "info_time_s": round(t_info, 3),
        "anchor_time_s": round(t_anchor, 3),
        "dp_time_s": round(t_dp, 3),
        "non11_row_ratio": round(non11_row_ratio, 4),
        "orphan_row_ratio": round(orphan_row_ratio, 4),
        "container_ratio": round(container_ratio, 4),
        "n_orphan_rows": n_orphan_rows,
    }


def _merge_consecutive_solo_ops(
    ops: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]],
) -> List[Tuple[Tuple[int, ...], Tuple[int, ...], float]]:
    """将 DP 输出中连续的同侧孤行分批合并为 N:0 / 0:M。"""
    if not ops:
        return []

    result: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]] = []
    buf: List[str] = []
    buf_srcs: List[Tuple[int, ...]] = []

    def _flush():
        if not buf:
            return
        if buf[0] == "src_solo":
            for chunk_start in range(0, len(buf_srcs), MAX_CONTAINER_SIZE):
                chunk = buf_srcs[chunk_start : chunk_start + MAX_CONTAINER_SIZE]
                merged = tuple(sorted(set().union(*chunk)))
                result.append((merged, (), 0.0))
        else:
            for chunk_start in range(0, len(buf_srcs), MAX_CONTAINER_SIZE):
                chunk = buf_srcs[chunk_start : chunk_start + MAX_CONTAINER_SIZE]
                merged = tuple(sorted(set().union(*chunk)))
                result.append(((), merged, 0.0))

    for s_idx, t_idx, sc in ops:
        ls, lt = len(s_idx), len(t_idx)
        if ls >= 1 and lt >= 1:
            _flush()
            buf.clear()
            buf_srcs.clear()
            result.append((s_idx, t_idx, sc))
        elif ls >= 1 and lt == 0:
            cur_type = "src_solo"
            if buf and buf[-1] != cur_type:
                _flush()
                buf.clear()
                buf_srcs.clear()
            buf.append(cur_type)
            buf_srcs.append(s_idx)
        elif ls == 0 and lt >= 1:
            cur_type = "tgt_solo"
            if buf and buf[-1] != cur_type:
                _flush()
                buf.clear()
                buf_srcs.clear()
            buf.append(cur_type)
            buf_srcs.append(t_idx)
        else:
            _flush()
            buf.clear()
            buf_srcs.clear()
            result.append((s_idx, t_idx, sc))

    _flush()
    return result


def _count_overflow_rows(
    anchors: List[Tuple[Tuple[int, ...], Tuple[int, ...], float]],
    n: int,
    m: int,
) -> int:
    """统计因合并长度超限（MERGE_LENGTH_LIMIT）而无法被任何锚点吞并的自由行数。

    当两个相邻锚点之间的间距 > 2×θ 时，中间的自由行超出左右锚点各 θ 行
    的可达范围，无法被任何合并组合覆盖。这些"双重孤儿"行是合并编码触顶
    的量化指标。
    """
    if not anchors:
        return 0

    theta = MERGE_LENGTH_LIMIT
    sorted_a = sorted(anchors, key=lambda a: (a[0][0], a[1][0]))
    overflow = 0

    for k in range(len(sorted_a) - 1):
        s1 = sorted_a[k][0][0]
        s2 = sorted_a[k + 1][0][0]
        t1 = sorted_a[k][1][0]
        t2 = sorted_a[k + 1][1][0]

        gap_s = s2 - s1 - 1
        gap_t = t2 - t1 - 1

        if gap_s > 2 * theta:
            overflow += gap_s - 2 * theta
        if gap_t > 2 * theta:
            overflow += gap_t - 2 * theta

    # 首段：第一个锚点之前的自由行
    s0 = sorted_a[0][0][0]
    t0 = sorted_a[0][1][0]
    if s0 > theta:
        overflow += s0 - theta
    if t0 > theta:
        overflow += t0 - theta

    # 末段：最后一个锚点之后的自由行
    sl = sorted_a[-1][0][0]
    tl = sorted_a[-1][1][0]
    gap_end_s = n - 1 - sl
    gap_end_t = m - 1 - tl
    if gap_end_s > theta:
        overflow += gap_end_s - theta
    if gap_end_t > theta:
        overflow += gap_end_t - theta

    return overflow


# ═══════════════════════════════════════════════════════════════
# 文本拼接辅助
# ═══════════════════════════════════════════════════════════════


def _smart_join_lines(lines: List[str], sep: str = None) -> str:
    """拼接多行文本。"""
    if not lines:
        return ""
    result = lines[0].rstrip()
    for nxt in lines[1:]:
        nxt = nxt.strip()
        if not nxt:
            continue
        if sep is not None:
            result += sep + nxt
            continue
        if result:
            last = result[-1]
            is_cjk = (
                "\u4e00" <= last <= "\u9fff"
                or "\u3000" <= last <= "\u303f"
                or "\uff00" <= last <= "\uffef"
                or "\u3400" <= last <= "\u4dbf"
            )
            result += nxt if is_cjk else " " + nxt
        else:
            result = nxt
    return result


_CJK_RANGES = (
    ("\u4e00", "\u9fff"),
    ("\u3000", "\u303f"),
    ("\uff00", "\uffef"),
    ("\u3400", "\u4dbf"),
)


def _is_cjk(c: str) -> bool:
    """单字符 CJK 判断。"""
    for lo, hi in _CJK_RANGES:
        if lo <= c <= hi:
            return True
    return False


def _precompute_cjk_ends(lines: List[str]) -> List[bool]:
    """预计算每行是否以 CJK 字符结尾。"""
    result = [False] * len(lines)
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if stripped:
            result[i] = _is_cjk(stripped[-1])
    return result


def _smart_join_lines_fast(lines: List[str], cjk_ends: List[bool]) -> str:
    """_smart_join_lines 的快速变体。"""
    if not lines:
        return ""
    result = lines[0].rstrip()
    prev_cjk = cjk_ends[0] if cjk_ends else False
    for i in range(1, len(lines)):
        nxt = lines[i].strip()
        if not nxt:
            continue
        if result and not prev_cjk:
            result += " " + nxt
        else:
            result += nxt
        prev_cjk = cjk_ends[i]
    return result
