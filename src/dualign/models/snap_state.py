"""
Dualign — SnapState: 三层化文本对状态

Layer 1: 原始对齐事实 — 写入后只读
Layer 2: 当前文本状态 — 随修复更新
Layer 3: 处理历史 — 随操作追加
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from dualign.models.state import AlignmentSnapshot, MISSING
from dualign.models.action import RepairAction
from dualign.models.marker import is_merge
from dualign.core import detect_language_mix, _smart_join_lines

logger = logging.getLogger(__name__)

# ── approval 四态管线 ──
# none → auto → agent → user（递进，flag 不推进管线）
APPROVAL_NONE = "none"
APPROVAL_AUTO = "auto"
APPROVAL_AGENT = "agent"
APPROVAL_USER = "user"

ALL_APPROVAL_STATES = [
    APPROVAL_NONE,
    APPROVAL_AUTO,
    APPROVAL_AGENT,
    APPROVAL_USER,
]

APPROVAL_LABELS = {
    APPROVAL_NONE: "未处理",
    APPROVAL_AUTO: "自动修复",
    APPROVAL_AGENT: "AI 审校",
    APPROVAL_USER: "用户审校",
}


# ═══════════════════════════════════════════════════════════════
# auto_repair_note — 生成 AI 可见的 auto_note 文本
# ═══════════════════════════════════════════════════════════════


def auto_repair_note(n_src: int, n_tgt: int, strategy: str, approval: str = "") -> str:
    """返回结构化 auto_note：`策略名 | 机器动作 | 补充`

    strategy: "minimal" | "src" | "tgt"
    当 snap 已自动修复 (approval=auto_repaired) 时动作表示已完成的操作；
    当 snap 未处理时动作为 would_*，表示「如果自动修复会怎么做」。

    输出示例:
      "src-first | merged | 合并3行原文→1行"
      "src-first | would_merge | 可合并3行原文→1行（待确认）"
    """
    strategy_name = {"minimal": "minimal", "src": "src-first", "tgt": "tgt-first"}.get(
        strategy, "src-first"
    )
    is_repaired = approval == APPROVAL_AUTO

    if n_src == 1 and n_tgt == 1:
        return ""

    if n_src > 1 and n_tgt == 1:
        # N:1 → src: split tgt, tgt: merge src
        if is_repaired:
            if strategy == "tgt":
                return f"{strategy_name} | merged | 合并{n_src}行原文→1行"
            return f"{strategy_name} | split | 拆分1行译文为{n_src}行匹配原文"
        if strategy == "minimal":
            return f"{strategy_name} | unrepaired | {n_src}:1 未自动处理（minimal 不自动合并）"
        if strategy == "tgt":
            return f"{strategy_name} | would_merge | 可合并{n_src}行原文→1行（语义优先，不强制）"
        return (
            f"{strategy_name} | would_split | 语义优先，拆分或合并均可（使用edit操作）"
        )

    if n_src == 1 and n_tgt > 1:
        # 1:M → src: merge tgt, tgt: split src
        if is_repaired:
            if strategy == "tgt":
                return f"{strategy_name} | split | 拆分1行原文为{n_tgt}行匹配译文"
            return f"{strategy_name} | merged | 合并{n_tgt}行译文→1行"
        if strategy == "minimal":
            return f"{strategy_name} | unrepaired | 1:{n_tgt} 未自动处理（minimal 不自动合并）"
        if strategy == "tgt":
            return f"{strategy_name} | would_split | 语义优先，拆分或合并均可（使用edit操作）"
        return f"{strategy_name} | would_merge | 可合并{n_tgt}行译文→1行（语义优先，不强制）"

    if n_src == 0 and n_tgt > 0:
        if is_repaired:
            action = "deleted" if strategy == "minimal" else "placeholder"
            return f"{strategy_name} | {action} | 已处理"
        if strategy == "minimal":
            return f"{strategy_name} | unrepaired | {n_tgt}行译文无对应原文（minimal 建议view后delete）"
        return f"{strategy_name} | would_delete | {n_tgt}行译文无对应原文（建议view确认后delete或保留）"

    if n_src > 0 and n_tgt == 0:
        if is_repaired:
            action = "deleted" if strategy == "minimal" else "placeholder"
            return f"{strategy_name} | {action} | 已处理"
        if strategy == "minimal":
            return f"{strategy_name} | unrepaired | {n_src}行原文无译文（minimal 建议edit补译）"
        return f"{strategy_name} | would_placeholder | 保留{n_src}行原文，译文需补⟢MISSING⟣"

    action = "processed" if is_repaired else "unrepaired"
    return f"{strategy_name} | {action} | {n_src}:{n_tgt} 未自动处理"


# ═══════════════════════════════════════════════════════════════
# parse_auto_note — 解析 auto_note 结构化字段（集中化入口）
# ═══════════════════════════════════════════════════════════════

WOULD_ACTIONS = frozenset(
    {"would_merge", "would_split", "would_delete", "would_placeholder"}
)


def parse_auto_note(auto_note: str) -> tuple[str, str, str]:
    """解析 auto_note 返回 (strategy, action, detail)。

    格式: `策略名 | 机器动作 | 补充说明`
    例如: `"src-first | would_split | 语义优先，拆分或合并均可"`

    返回:
      strategy: "src-first" / "tgt-first" / "minimal" / ""
      action:   "merged" / "split" / "would_merge" / "would_split" / "unrepaired" / "" 等
      detail:   补充文本
    """
    if not auto_note:
        return "", "", ""
    parts = auto_note.split("|", 2)
    strategy = parts[0].strip() if len(parts) > 0 else ""
    action = parts[1].strip() if len(parts) > 1 else ""
    detail = parts[2].strip() if len(parts) > 2 else ""
    return strategy, action, detail


def is_would_action(action: str) -> bool:
    """判断是否为 would_* 建议动作。"""
    return action in WOULD_ACTIONS


# ═══════════════════════════════════════════════════════════════
# compute_snap_preview — 为非 1:1 snap 计算合并/修复预览
# ═══════════════════════════════════════════════════════════════


def compute_snap_preview(snapshot: AlignmentSnapshot, snap_i: int) -> str:
    """为非 1:1 snap 计算将全部行连接为 1:1 后的文本预览。

    展示所有 src 行和所有 tgt 行分别拼接的结果。
    不修改任何实际状态。
    """
    s_idx, t_idx, _sc = snapshot.original_ops[snap_i]
    ls, lt = len(s_idx), len(t_idx)

    if ls == 0 and lt > 0:
        tgt_texts = [snapshot.tgt_text(j) for j in t_idx]
        merged_tgt = (
            _smart_join_lines(tgt_texts)
            if len(tgt_texts) > 1
            else (tgt_texts[0] if tgt_texts else "")
        )
        return f"0:{lt} 无原文 | 译文: {merged_tgt}"
    if ls > 0 and lt == 0:
        src_texts = [snapshot.src_text(i) for i in s_idx]
        merged_src = (
            _smart_join_lines(src_texts)
            if len(src_texts) > 1
            else (src_texts[0] if src_texts else "")
        )
        return f"{ls}:0 无译文 | 原文: {merged_src} | {MISSING}"
    if ls == 1 and lt == 1:
        return ""
    # N:1 / 1:M / N:M → 展示全部行拼接为 1:1 后的文本
    src_texts = [snapshot.src_text(i) for i in s_idx]
    tgt_texts = [snapshot.tgt_text(j) for j in t_idx]
    merged_src = _smart_join_lines([t for t in src_texts if t])
    merged_tgt = _smart_join_lines([t for t in tgt_texts if t])
    return f"1:1 预览\nsrc: {merged_src}\ntgt: {merged_tgt}"


# ═══════════════════════════════════════════════════════════════
# build_context_windows — 构建上下文窗口（集中化入口）
# ═══════════════════════════════════════════════════════════════


def build_context_windows(
    reviewable_ids: List[int],
    total: int,
    window_size: int = 3,
    merge_gap_threshold: int = 1,
) -> List[Tuple[int, int]]:
    """构建上下文窗口，相邻间距 ≤ merge_gap_threshold 时合并。

    Args:
        reviewable_ids: 待审的 snap 索引列表
        total: 总 snap 数
        window_size: 每侧上下文行数
        merge_gap_threshold: 窗口间距 ≤ 此值时合并。
                             默认 1：窗口间最多空 1 行时合并。
                             设为 0 则绝不合并。

    Returns:
        合并后的 (start, end) 窗口列表，已排序。
    """
    if not reviewable_ids:
        return []

    windows = [
        (max(0, sid - window_size), min(total - 1, sid + window_size))
        for sid in sorted(reviewable_ids)
    ]

    merged: List[Tuple[int, int]] = []
    for w in windows:
        if not merged or w[0] > merged[-1][1] + merge_gap_threshold + 1:
            merged.append(w)
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], w[1]))
    return merged


# ═══════════════════════════════════════════════════════════════
# SnapState
# ═══════════════════════════════════════════════════════════════


def _parse_type(pt: str) -> Tuple[int, int]:
    """解析 "N:M" → (N, M)。"""
    if ":" in pt:
        try:
            ls, lt = pt.split(":", 1)
            return int(ls), int(lt)
        except (ValueError, TypeError):
            pass
    return 1, 1


@dataclass
class SnapState:
    """单个文本对的三层状态。

    Layer 1 — 对齐完成后一次性写入，永不变化。
      消费者: 报告统计、GUI「原始非1:1」筛选

    Layer 2 — 每次文本内容变化后重新计算。
      消费者: AI 决策、GUI 渲染

    Layer 3 — 每次 repair 操作后更新。
      消费者: AI「需要验证吗」、GUI「操作记录」筛选
    """

    # ── Layer 1: 原始对齐事实（只读）──
    init_type: str = "1:1"  # 原始对齐类型
    init_score: float = 0.0  # 原始对齐评分
    is_low_score: bool = False  # 原始评分是否统计离群
    init_has_language_mix: bool = False  # 初始译文是否含中文（Layer 1 不可变）

    # ── Layer 2: 当前文本状态 ──
    n_src: int = 1  # 当前组内原文行数
    n_tgt: int = 1  # 当前组内译发行数
    has_missing: bool = False  # 当前文本含 ⟢MISSING⟣
    has_language_mix: bool = False  # 当前译文含中文（Layer 2 可变, 随编辑重新检测）
    is_deleted: bool = False  # 已被删除

    # ── Layer 3: 处理历史 ──
    approval: str = APPROVAL_NONE
    repair_count: int = 0
    last_source: str = ""  # "" / "auto" / "ai" / "user"
    last_operation: str = ""  # merge / split / edit / delete / ok / flag
    is_flagged: bool = False  # 用户手动标记需关注（异常类型 FLAGGED 的持久化状态）
    # ── 派生属性 ──

    @property
    def initial_anomaly_types(self) -> List[str]:
        """原始对齐事实（Layer 1）的异常分类——对齐器自动检测的结果，不可变。

        NON_1TO1 从 init_type 推导，MIX 从初始译文检测，LOW_SCORE 从 Z-score 判定。
        均不随修复操作变化。
        FLAGGED 不是对齐器检测的，不出现于此。
        """
        labels = []
        init_s, init_t = _parse_type(self.init_type)
        if init_s != 1 or init_t != 1:
            labels.append("NON_1TO1")
        if self.init_has_language_mix:
            labels.append("MIX")
        if self.is_low_score:
            labels.append("LOW_SCORE")
        return labels

    @property
    def current_anomaly_types(self) -> List[str]:
        """当前文本状态（Layer 2 + Layer 3）的异常分类——可变状态。

        NON_1TO1 基于两侧行数是否平衡（编辑/拆分产生 n:n 平衡结构时消失）。
        MIX 基于当前文本重新检测。
        FLAGGED 是用户动作。
        LOW_SCORE 是原始评分属性，不出现在此。
        """
        labels = []
        if self.n_src != self.n_tgt:
            labels.append("NON_1TO1")
        if self.has_language_mix:
            labels.append("MIX")
        if self.is_flagged:
            labels.append("FLAGGED")
        return labels

    @property
    def anomaly_types(self) -> List[str]:
        """向后兼容别名，指向 initial_anomaly_types。"""
        return self.initial_anomaly_types

    @property
    def is_reviewable(self) -> bool:
        """用户已审校或已删除 → 不再需审校。GUI 和 AI 共用。"""
        if self.approval == APPROVAL_USER:
            return False
        if self.is_deleted:
            return False
        return bool(self.current_anomaly_types)

    @property
    def signals(self) -> List[str]:
        """自然语言状态信号（供 AI 和 GUI 展示）。"""
        signals = []
        if self.approval == APPROVAL_AUTO:
            signals.append("已自动修复")
        if self.has_missing:
            signals.append("缺失待补")
        if self.has_language_mix:
            signals.append("译文含中文")
        if self.is_flagged:
            signals.append("标记待审")
        return signals


# ═══════════════════════════════════════════════════════════════
# SnapInfo — AI 视图（只含 Layer 2 + Layer 3 部分）
# ═══════════════════════════════════════════════════════════════


@dataclass
class SnapInfo:
    """AI 看到的 snap——不包含任何原始对齐事实。

    从 SnapState 的 Layer 2 + Layer 3 构建，供 AI Agent 使用。
    用 n_src_rows/n_tgt_rows 替代旧 cur_type 字符串。
    """

    snap_id: int
    # 当前文本（待审校状态）
    n_src_rows: int
    n_tgt_rows: int
    src_text: str
    tgt_text: str
    # 初始文本（对齐器原始输出，AI 操作基准）
    initial_n_src: int = 0
    initial_n_tgt: int = 0
    initial_src_text: str = ""
    initial_tgt_text: str = ""
    # 异常标记
    has_missing: bool = False
    has_language_mix: bool = False
    is_low_score: bool = False
    approval: str = ""

    @property
    def signals(self) -> List[str]:
        signals = []
        if self.has_missing:
            signals.append("缺失待补")
        if self.has_language_mix:
            signals.append("译文含中文")
        if self.is_low_score:
            signals.append("离群低分")
        return signals

    @property
    def is_reviewable(self) -> bool:
        """委托给 SnapState 的逻辑 —— approval != user + 有异常。NON_1TO1 基于原始对齐事实判定。"""
        if self.approval == APPROVAL_USER:
            return False
        if self.initial_n_src == 0 and self.initial_n_tgt == 0:
            return False
        return (
            self.initial_n_src != 1
            or self.initial_n_tgt != 1
            or self.has_missing
            or self.has_language_mix
            or self.is_low_score
        )

    def __str__(self) -> str:
        """生成 JSON 行格式，供 LLM 消费。

        src/tgt 以字符串数组形式输出，每元素一行——AI 可以直接在 JSON
        结构中看到每行的独立性，而非被 \\n 嵌入字符串模糊掉行边界。
        AI 从 src/tgt 数组长度即可推断行数，无需冗余的 n_src/n_tgt 字段。

        orig 字段标注初始类型的行数关系（如 "2:1"、"1:2"），
        AI 无需从 initial_* 推算即可知初始结构。
        initial_src/initial_tgt 始终展示（当存在时），使 AI 在 edit 决策时
        能直接参考初始文本——edit 操作的是初始文本，不是当前文本。
        """
        d = {"id": self.snap_id}
        sigs = self.signals
        if sigs:
            d["signals"] = sigs
        # 始终标注初始类型，AI 零推理成本获知初始行数关系
        d["orig"] = f"{self.initial_n_src}:{self.initial_n_tgt}"
        if self.src_text:
            d["src"] = [ln for ln in self.src_text.split("\n") if ln]
        else:
            d["src"] = []  # 显式空数组，表明原文不存在
        if self.tgt_text:
            d["tgt"] = [ln for ln in self.tgt_text.split("\n") if ln]
        else:
            d["tgt"] = []  # 显式空数组，表明译文不存在
        # 初始文本：仅当与当前文本不同时展示
        # orig 字段已提供初始类型信息，无需重复相同的文本内容
        if self.initial_src_text and self.initial_src_text != self.src_text:
            d["initial_src"] = [ln for ln in self.initial_src_text.split("\n") if ln]
        if self.initial_tgt_text and self.initial_tgt_text != self.tgt_text:
            d["initial_tgt"] = [ln for ln in self.initial_tgt_text.split("\n") if ln]
        return json.dumps(d, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# 统一构建函数
# ═══════════════════════════════════════════════════════════════


def _calc_low_score(scores: List[float], score: float, k: float = 3.0) -> bool:
    """Z-score 离群检测（委托给 quality_gate）。"""
    from dualign.services.quality_gate import is_statistical_low_score

    return is_statistical_low_score(score, scores, k=k)


def _derive_approval(action: Optional[RepairAction]) -> str:
    """从 RepairAction 推导四态管线 approval。

    none → auto → agent → user（递进）。
    flag 不推进管线：返回 NONE（调用方需向上游查找有效状态）。
    """
    if action is None:
        return APPROVAL_NONE
    if action.kind == "flag":
        return APPROVAL_NONE  # flag 不推进管线（无论来源）
    s = action.source
    if s == "auto":
        return APPROVAL_AUTO
    if s == "ai":
        return APPROVAL_AGENT
    if s == "user":
        return APPROVAL_USER
    # 兼容旧 source=""（视为 auto）
    if not s:
        return APPROVAL_AUTO
    return APPROVAL_NONE


def build_snap_states(
    snapshot: AlignmentSnapshot,
    src_lines: List[str],
    tgt_lines: List[str],
    repair_log: Optional[List[RepairAction]] = None,
    mu: float = None,
    sigma: float = None,
    k: float = 3.0,
) -> List[SnapState]:
    """统一的 SnapState 构建入口。

    从 AlignmentSnapshot 写入 Layer 1，从原始文本写入 Layer 2 初值，
    从 repair_log 写入 Layer 3。

    CLI 和 GUI 共用此函数。
    """
    log = repair_log or []
    total = len(snapshot.original_ops)

    # 1:1 对的评分列表（用于 Z-score 检测）
    scores_1to1 = [
        sc for s, t, sc in snapshot.original_ops if len(s) == 1 and len(t) == 1
    ]

    states: List[SnapState] = []

    for si in range(total):
        s_idx, t_idx, sc = snapshot.original_ops[si]
        ls, lt = len(s_idx), len(t_idx)
        init_type = f"{ls}:{lt}"

        # Layer 1
        is_low = (
            _calc_low_score(scores_1to1, float(sc)) if ls == 1 and lt == 1 else False
        )

        # Layer 2 初值
        src_raw = "\n".join(snapshot.src_text(i) for i in s_idx) if s_idx else ""
        tgt_raw = "\n".join(snapshot.tgt_text(j) for j in t_idx) if t_idx else ""
        has_missing = MISSING in src_raw or MISSING in tgt_raw
        has_mix = any(
            j < len(tgt_lines) and detect_language_mix(tgt_lines[j]) for j in t_idx
        )

        # Layer 3: flag 仅当是最新操作时才携带 FLAGGED 异常
        # 任何后续非 flag 操作（ok/edit/merge/delete 等）自动清除标记待查
        my_actions = [a for a in log if a.op_index == si]
        last_act_all = my_actions[-1] if my_actions else None
        is_flagged = last_act_all is not None and last_act_all.kind == "flag"
        non_flag_actions = [a for a in my_actions if a.kind != "flag"]
        last_act = non_flag_actions[-1] if non_flag_actions else None

        states.append(
            SnapState(
                # Layer 1
                init_type=init_type,
                init_score=float(sc),
                is_low_score=is_low,
                init_has_language_mix=has_mix,
                # Layer 2
                n_src=ls,
                n_tgt=lt,
                has_missing=has_missing,
                has_language_mix=has_mix,
                is_deleted=last_act is not None and last_act.kind == "delete",
                # Layer 3
                approval=_derive_approval(last_act),
                repair_count=len(non_flag_actions),
                last_source=last_act.source if last_act else "",
                last_operation=last_act.kind if last_act else "",
                is_flagged=is_flagged,
            )
        )

    return states


def refresh_snap_states(
    states: List[SnapState],
    snapshot: AlignmentSnapshot,
    ch_state,
    repair_log: List[RepairAction],
) -> List[SnapState]:
    """从 RepairState 更新 Layer 2 + Layer 3。

    保留 Layer 1 字段（init_type/init_score/is_low_score/init_has_language_mix），
    不随当前文本状态变化。NON_1TO1 / LOW_SCORE 均为基于原始状态的不可变异常。
    MIX（initial_anomaly_types）使用 init_has_language_mix（Layer 1 不可变），
    MIX（current_anomaly_types）使用 has_language_mix（Layer 2 可变——随编辑重新检测）。
    仅 FLAGGED 基于当前 flag 操作（Layer 3）。

    在 auto_repair 或 AI 审校后调用，保持 states 与最新修复状态同步。
    ch_state 是 ChapterState (repair_state.current)。
    """
    total = min(len(states), len(snapshot.original_ops))
    new_states = list(states)

    for si in range(total):
        g = ch_state.group(si)
        if g is None:
            new_states[si] = SnapState(
                init_type=states[si].init_type,
                init_score=states[si].init_score,
                is_low_score=states[si].is_low_score,
                init_has_language_mix=states[si].init_has_language_mix,
                n_src=states[si].n_src,
                n_tgt=states[si].n_tgt,
                has_missing=states[si].has_missing,
                has_language_mix=states[si].has_language_mix,
                is_deleted=True,
                approval=states[si].approval,
                repair_count=states[si].repair_count,
                last_source=states[si].last_source,
                last_operation=states[si].last_operation,
                is_flagged=states[si].is_flagged,
            )
            continue

        src = "\n".join(r.src_text for r in g.rows if r.src_text)
        tgt = "\n".join(r.tgt_text for r in g.rows if r.tgt_text)
        has_missing = MISSING in src or MISSING in tgt
        # 当前文本内容 → 重新检测语言杂糅（Layer 2 可变，随编辑更新）
        has_mix = detect_language_mix(tgt) if tgt.strip() else False

        # 基于当前文本内容计算 n_src/n_tgt，而非过时的对齐元数据
        if g.rows and is_merge(g.rows[0].marker):
            # 合并在逻辑层面已将文本对变为 1:1
            n_src = 1
            n_tgt = 1
        else:
            n_src = sum(1 for r in g.rows if r.src_text.strip())
            n_tgt = sum(1 for r in g.rows if r.tgt_text.strip())
        # 保护：至少 1 除非确实两边都空
        if n_src == 0 and n_tgt == 0:
            n_src = g.rows[0].n_src if g.rows else 1
            n_tgt = g.rows[0].n_tgt if g.rows else 1

        # Layer 3: flag 仅当是最新操作时才携带 FLAGGED
        my_actions = [a for a in repair_log if a.op_index == si]
        last_act_all = my_actions[-1] if my_actions else None
        is_flagged = last_act_all is not None and last_act_all.kind == "flag"
        non_flag_actions = [a for a in my_actions if a.kind != "flag"]
        last_act = non_flag_actions[-1] if non_flag_actions else None

        new_states[si] = SnapState(
            init_type=states[si].init_type,
            init_score=states[si].init_score,
            is_low_score=states[si].is_low_score,
            init_has_language_mix=states[si].init_has_language_mix,
            n_src=n_src,
            n_tgt=n_tgt,
            has_missing=has_missing,
            has_language_mix=has_mix,
            is_deleted=last_act is not None and last_act.kind == "delete",
            approval=_derive_approval(last_act),
            repair_count=len(non_flag_actions),
            last_source=last_act.source if last_act else states[si].last_source,
            last_operation=last_act.kind if last_act else states[si].last_operation,
            is_flagged=is_flagged,
        )

    return new_states


def snap_state_to_info(
    state: SnapState, snap_id: int, src_text: str, tgt_text: str
) -> SnapInfo:
    """将 SnapState 转换为 AI 视图 SnapInfo（只含 Layer 2 + Layer 3）。"""
    return SnapInfo(
        snap_id=snap_id,
        n_src_rows=state.n_src,
        n_tgt_rows=state.n_tgt,
        src_text=src_text,
        tgt_text=tgt_text,
        has_missing=state.has_missing,
        has_language_mix=state.has_language_mix,
        is_low_score=state.is_low_score,
        approval=state.approval,
        # initial_* 由调用方填充
    )
