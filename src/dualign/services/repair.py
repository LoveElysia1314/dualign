"""
Dualign — RepairService: 修复操作统一入口

RepairState (不可变容器) = snapshot + repair_log
RepairService (纯函数集合) = replay + auto_repair + render_rows
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

import numpy as np

from dualign.models.state import AlignmentSnapshot, MISSING
from dualign.models.action import RepairAction
from dualign.models.action import AiProposalStore
from dualign.models.marker import (
    is_merge,
    is_deleted,
    is_approved,
    is_placeholder,
    is_edit,
    is_split,
    is_flagged,
    combine,
    is_divider,
    AI_PREFIX,
)
from dualign.models.state import (
    AlignedRow,
    SnapGroup,
    ChapterState,
)
from dualign.core import op_type_str, _smart_join_lines, AlignConfig, align
from dualign.services.embedding_cache import EmbeddingCache

# ═══════════════════════════════════════════════════════════════
# 1. 内部纯函数：重放辅助
# ═══════════════════════════════════════════════════════════════


def _apply_info_free(state: ChapterState, snap_i: int, marker: str) -> ChapterState:
    """info-free 操作: 只设 marker。文本在渲染时从 snapshot 重建。

    [P] 是例外：它需要将 cur_type 改为 "1:1" 并填充空侧文本，
    否则后续 [OK] 叠加时占位符文本会丢失。
    """
    g = state.group(snap_i)
    if g is None:
        return state

    if is_placeholder(marker):
        # [P]: 生成包含 ⟢MISSING⟣ 文本的 1:1 行
        s_idx, t_idx, _sc = state.snapshot.original_ops[snap_i]
        ls, lt, _, missing_side = _placeholder_info(s_idx, t_idx)
        if missing_side is not None:
            # N:0 或 0:M → 每行一个 (原文/⟢MISSING⟣, ⟢MISSING⟣/译文) 对
            if missing_side == "src":
                texts = [
                    (
                        "\u27e2MISSING\u27e3",
                        state.snapshot.tgt_text(t_idx[j]),
                    )
                    for j in range(lt)
                ]
            else:
                texts = [
                    (
                        state.snapshot.src_text(s_idx[i]),
                        "\u27e2MISSING\u27e3",
                    )
                    for i in range(ls)
                ]
            return _apply_info_full(
                state, snap_i, [t[0] for t in texts], [t[1] for t in texts], [], marker
            )
        return state.replace_snap(snap_i, g.with_marker(marker))

    # [OK] / [F] 是元标记（不含 [AI] 前缀时）：叠加到现有操作标记上
    # [AI][OK] / [AI][F] 是 AI 操作的完整标记，直接设置
    if (is_approved(marker) or is_flagged(marker)) and AI_PREFIX not in marker:
        existing = g.rows[0].marker if g.rows else ""
        new = combine(existing, marker)
        return state.replace_snap(snap_i, g.with_marker(new))

    return state.replace_snap(snap_i, g.with_marker(marker))


def _apply_info_full(
    state: ChapterState,
    snap_i: int,
    new_src: List[str],
    new_tgt: List[str],
    scores: List[float],
    marker: str,
) -> ChapterState:
    """info-full 操作: 完整替换为新文本对 (edit/split)。

    自动将数组元素中的换行符按行展开，过滤空行后 1:1 配对，
    确保预览表格正确拆分。
    """
    g = state.group(snap_i)
    if g is None:
        return state

    # 单侧未传文本 → 从 snapshot 取原始内容作为默认值
    # 使 AI 的 edit(new_tgt=[...]) 无需传 new_src 也能保留原文。
    if not new_src and not new_tgt:
        return state
    if not new_src:
        s_idx, t_idx, _ = state.snapshot.original_ops[snap_i]
        if s_idx:
            new_src = [state.snapshot.src_text(i) for i in s_idx]
        else:
            new_src = []
    if not new_tgt:
        s_idx, t_idx, _ = state.snapshot.original_ops[snap_i]
        if t_idx:
            new_tgt = [state.snapshot.tgt_text(j) for j in t_idx]
        else:
            new_tgt = []

    # 展开每个元素中的换行符为独立行，1:1 配对
    expanded_src: List[str] = []
    expanded_tgt: List[str] = []
    for s in new_src:
        for line in s.split("\n"):
            stripped = line.strip()
            if stripped:
                expanded_src.append(line if line != stripped else stripped)
            else:
                expanded_src.append(line)
    for t in new_tgt:
        for line in t.split("\n"):
            stripped = line.strip()
            if stripped:
                expanded_tgt.append(line if line != stripped else stripped)
            else:
                expanded_tgt.append(line)

    # 1:1 配对，短侧补空字符串
    n = max(len(expanded_src), len(expanded_tgt))
    texts = [
        (
            expanded_src[k] if k < len(expanded_src) else "",
            expanded_tgt[k] if k < len(expanded_tgt) else "",
        )
        for k in range(n)
    ]
    it = g.rows[0].init_type
    osc = g.rows[0].orig_score
    n = len(texts)
    rows: List[AlignedRow] = []
    for k in range(n):
        sc = scores[k] if k < len(scores) else (scores[0] if scores else osc)
        rows.append(
            AlignedRow(
                snap_index=snap_i,
                sub=k,
                init_type=it if k == 0 else "",
                cur_type="1:1",
                src_text=texts[k][0],
                tgt_text=texts[k][1],
                score=float(sc),
                orig_score=osc,
                n_src=n,
                n_tgt=n,
                marker=marker,
            )
        )
    return state.replace_snap(snap_i, SnapGroup(snap_i=snap_i, rows=tuple(rows)))


def _apply_multi_snap_merge(
    state: ChapterState,
    action: RepairAction,
    snap_list: List[int],
) -> ChapterState:
    """跨 snap 合并: 删除非 anchor snaps，在 anchor 处插入合并组。

    与单 snap 合并的视觉一致：每个子行显示对应 snap 的独立文本，
    子行之间用虚线分隔，不将全部文本合并到第一个单元格。
    """
    anchor = snap_list[0]

    # 删除非 anchor snaps
    for si in snap_list[1:]:
        state = state.remove_snap(si)

    # ── 收集所有捆绑 snap 的初始信息 ──
    init_types: List[str] = []
    init_scores: List[float] = []
    for si in snap_list:
        s_idx, t_idx, _sc = state.snapshot.original_ops[si]
        init_types.append(f"snap {si}\n{op_type_str(s_idx, t_idx)}")
        init_scores.append(float(_sc))
    ist = "\n".join(f"{s:.1%}" for s in init_scores) if len(init_scores) > 1 else ""

    total = len(snap_list)

    # 构建 anchor group: 保留每个被捆绑 snap 的原始 N:M 多行布局
    # 不同于旧版将所有文本压缩到一个单元格，新版维护每个 snap 的子行结构，
    # 子行间用 is_divider 的虚线分隔。
    rows: List[AlignedRow] = []
    sub = 0
    for k in range(total):
        si = snap_list[k]
        s_idx, t_idx, _sc = state.snapshot.original_ops[si]
        n = max(len(s_idx), len(t_idx))
        for r in range(n):
            src = state.snapshot.src_text(s_idx[r]) if r < len(s_idx) else ""
            tgt = state.snapshot.tgt_text(t_idx[r]) if r < len(t_idx) else ""
            rows.append(
                AlignedRow(
                    snap_index=anchor,
                    sub=sub,
                    init_type=init_types[k] if r == 0 else "",
                    cur_type=f"{len(s_idx)}:{len(t_idx)}" if r == 0 else "",
                    src_text=src,
                    tgt_text=tgt or "",
                    score=float(init_scores[k]),
                    orig_score=float(init_scores[k]),
                    n_src=len(s_idx),
                    n_tgt=len(t_idx),
                    marker="[M]",
                    init_score_text=ist if k == 0 and sub == 0 else "",
                )
            )
            sub += 1
    return state.replace_snap(anchor, SnapGroup(snap_i=anchor, rows=tuple(rows)))


def _apply_multi_snap_edit(
    state: ChapterState,
    action: RepairAction,
    snap_list: List[int],
) -> ChapterState:
    """跨 snap 校订：删除非 anchor snaps，合并到 anchor 一个 SnapGroup。

    Anchor 行的 init_type 换行拼接所有捆绑 snap 的初始类型，
    cur_type/score/text 独立对应每条校订后的 1:1 文本对。
    """
    d = action.data
    new_src: List[str] = d.get("new_src_lines", [])
    new_tgt: List[str] = d.get("new_tgt_lines", [])
    scores: List[float] = d.get("inherited_scores") or d.get("split_scores", [])

    anchor = snap_list[0]

    # ── 收集所有捆绑 snap 的初始信息 ──
    init_types: List[str] = []
    init_scores: List[float] = []
    init_scores_total = 0.0
    init_scores_n = 0
    for si in snap_list:
        s_idx, t_idx, _sc = state.snapshot.original_ops[si]
        init_types.append(f"snap {si}\n{op_type_str(s_idx, t_idx)}")
        init_scores.append(float(_sc))
        init_scores_total += float(_sc)
        init_scores_n += 1
    it = "\n---\n".join(init_types)
    osc = init_scores_total / init_scores_n if init_scores_n else 0.0
    # 平均分标 *，单个分数直接显示
    ist = ""
    if len(init_scores) > 1:
        ist = f"* {osc:.0%}"

    # ── 删除非 anchor snaps ──
    for si in snap_list[1:]:
        state = state.remove_snap(si)

    # ── 构建 anchor group：每个新文本对一行 ──
    n = max(len(new_src), len(new_tgt))
    rows: List[AlignedRow] = []
    for k in range(n):
        sc = scores[k] if k < len(scores) else (scores[0] if scores else osc)
        rows.append(
            AlignedRow(
                snap_index=anchor,
                sub=k,
                init_type=it if k == 0 else "",
                cur_type="1:1",
                src_text=new_src[k] if k < len(new_src) else "",
                tgt_text=new_tgt[k] if k < len(new_tgt) else "",
                score=float(sc),
                orig_score=osc,
                n_src=n,
                n_tgt=n,
                marker=action.marker,
                init_score_text=ist if k == 0 else "",
            )
        )
    state = state.replace_snap(anchor, SnapGroup(snap_i=anchor, rows=tuple(rows)))
    return state


def _placeholder_info(s_idx, t_idx):
    """返回 (ls, lt, type_str, missing_side)。

    type_str 是真实的类型名称，如 "1:0"、"3:0"、"0:2"。
    """
    ls, lt = len(s_idx), len(t_idx)
    if ls > 0 and lt == 0:
        return ls, lt, f"{ls}:{lt}", "tgt"
    if ls == 0 and lt > 0:
        return ls, lt, f"{ls}:{lt}", "src"
    return ls, lt, None, None


# ═══════════════════════════════════════════════════════════════
# 2. replay — 纯函数重放引擎
# ═══════════════════════════════════════════════════════════════


def replay(snapshot: AlignmentSnapshot, log: List[RepairAction]) -> ChapterState:
    """纯函数重放：snapshot × log → ChapterState。

    遍历所有 action，按 info-free / info-full 分类处理。
    """
    state = ChapterState.from_snapshot(snapshot)

    for act in log:
        snap_i = act.op_index
        if snap_i < 0 or snap_i >= len(snapshot.original_ops):
            continue

        # ── marker 由 RepairAction.marker 统一构建（含来源前缀）──
        _marker = act.marker

        # 多 snap 操作
        snaps = act.data.get("orig_snaps", [])
        if isinstance(snaps, list) and len(snaps) > 1:
            clean: List[int] = []
            seen: set = set()
            for s in snaps:
                try:
                    si = int(s)
                except (TypeError, ValueError):
                    continue
                if si not in seen:
                    seen.add(si)
                    clean.append(si)
            if len(clean) > 1:
                if act.kind == "merge":
                    state = _apply_multi_snap_merge(state, act, clean)
                    continue
                elif act.kind == "edit":
                    state = _apply_multi_snap_edit(state, act, clean)
                    continue

        # info-free: merge, delete, placeholder, flag, ok
        if act.kind == "merge":
            state = _apply_info_free(state, snap_i, _marker)
        elif act.kind == "delete":
            state = _apply_info_free(state, snap_i, _marker)
        elif act.kind in ("placeholder_src", "placeholder_tgt"):
            state = _apply_info_free(state, snap_i, _marker)
        elif act.kind == "flag":
            state = _apply_info_free(state, snap_i, _marker)
        elif act.kind == "ok":
            state = _apply_info_free(state, snap_i, _marker)

        # info-full: split, edit
        elif act.kind in ("split", "edit"):
            d = act.data
            new_src: List[str] = d.get("new_src_lines", [])
            new_tgt: List[str] = d.get("new_tgt_lines", [])
            scores: List[float] = d.get("split_scores") or d.get("inherited_scores", [])
            state = _apply_info_full(state, snap_i, new_src, new_tgt, scores, _marker)

    return state


# ═══════════════════════════════════════════════════════════════
# 3. RepairState — 不可变状态容器
# ═══════════════════════════════════════════════════════════════


@dataclass
class RepairState:
    """不可变修复状态容器。

    _snapshot + _repair_log → replay → ChapterState

    每次 apply() 返回新 RepairState，旧实例不变（支持撤销）。
    ai_proposal_store 独立于 repair_log——重置修复不会丢失 AI 建议。
    """

    _snapshot: AlignmentSnapshot
    _repair_log: List[RepairAction] = field(default_factory=list)
    _ai_proposal_store: AiProposalStore = field(default_factory=AiProposalStore)

    # ── 属性 ──

    @property
    def snapshot(self) -> AlignmentSnapshot:
        return self._snapshot

    @property
    def original_ops(self) -> list:
        return self._snapshot.ops_list

    @property
    def original_src_lines(self) -> list:
        return self._snapshot.src_list

    @property
    def original_tgt_lines(self) -> list:
        return self._snapshot.tgt_list

    @property
    def repair_log(self) -> list:
        return list(self._repair_log)

    @property
    def is_dirty(self) -> bool:
        return len(self._repair_log) > 0

    @property
    def current(self) -> ChapterState:
        """通过 replay() 每次重新计算当前状态。"""
        return replay(self._snapshot, self._repair_log)

    @property
    def ai_proposal_store(self) -> AiProposalStore:
        return self._ai_proposal_store

    def set_ai_proposal_store(self, store: AiProposalStore) -> RepairState:
        """返回一个替换了 AI 建议存储的新 RepairState 实例。

        用于批量清除/重置 AI 建议场景（不可变模式下替换 store）。
        """
        return RepairState(self._snapshot, self._repair_log, store)

    # ── 操作 ──

    def apply(self, action: RepairAction) -> RepairState:
        """应用操作，返回新 RepairState。

        自动清除受影响 snap 的历史操作（同 snap 同类操作去重）。
        """
        # 跨 snap 操作：清空 orig_snaps 中所有涉及 snap 的历史操作
        affected = {action.op_index}
        orig = action.data.get("orig_snaps", [])
        if isinstance(orig, list):
            for si in orig:
                try:
                    affected.add(int(si))
                except (TypeError, ValueError):
                    pass

        if action.kind in ("ok", "flag"):
            new_log = [
                a
                for a in self._repair_log
                if not (a.op_index in affected and a.kind == action.kind)
            ]
        else:
            new_log = [a for a in self._repair_log if a.op_index not in affected]
        new_log.append(action)
        return RepairState(self._snapshot, new_log, self._ai_proposal_store)

    def undo(self) -> RepairState:
        """撤销最后一条操作。"""
        if not self._repair_log:
            return self
        return RepairState(
            self._snapshot, self._repair_log[:-1], self._ai_proposal_store
        )

    def reset(self) -> RepairState:
        """重置所有修复，保留 AI 建议。"""
        return RepairState(self._snapshot, [], self._ai_proposal_store)

    def reset_op(self, op_index: int) -> RepairState:
        """重置指定文本对的修复，保留 AI 建议。"""
        return RepairState(
            self._snapshot,
            [a for a in self._repair_log if a.op_index != op_index],
            self._ai_proposal_store,
        )

    def action_for_op(self, op_index: int) -> Optional[RepairAction]:
        """查找指定 op_index 的最新 action。"""
        for a in reversed(self._repair_log):
            if a.op_index == op_index:
                return a
        return None

    # ── 构造器 ──

    @classmethod
    def from_ops(
        cls,
        original_ops: list,
        src_lines: list,
        tgt_lines: list,
        log: Optional[list] = None,
        ai_proposal_store: Optional[AiProposalStore] = None,
    ) -> RepairState:
        """从对齐结果构造 RepairState。"""
        return cls(
            AlignmentSnapshot.from_alignment(original_ops, src_lines, tgt_lines),
            list(log) if log else [],
            ai_proposal_store or AiProposalStore(),
        )

    # ── 序列化 ──

    def to_dict(self) -> dict:
        d = {
            "version": 1,
            "snapshot": {
                "original_ops": [
                    {"s": list(s), "t": list(t), "score": round(sc, 4)}
                    for s, t, sc in self._snapshot.original_ops
                ],
            },
            "repair_log": [a.to_dict() for a in self._repair_log],
        }
        d["ai_proposals"] = self._ai_proposal_store.to_dict()
        return d

    @classmethod
    def from_dict(
        cls, d: dict, src_lines: list, tgt_lines: list
    ) -> Optional[RepairState]:
        """从字典反序列化 RepairState（v1 格式）。

        v1 不存储文本行，由调用方传入重建 snapshot。
        """
        try:
            sd = d.get("snapshot", {})
            ops = [
                (tuple(o["s"]), tuple(o["t"]), float(o["score"]))
                for o in sd.get("original_ops", [])
            ]
            snap = AlignmentSnapshot.from_alignment(ops, src_lines, tgt_lines)
            log = [RepairAction.from_dict(a) for a in d.get("repair_log", [])]
            store = AiProposalStore.from_dict(d.get("ai_proposals", {}))
            return cls(snap, log, store)
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════
# 4. TableRow / TableViewModel (thin adapter)
# ═══════════════════════════════════════════════════════════════


@dataclass
class TableRow:
    """表格视图行（用于渲染）。"""

    snap_index: int
    sub: int
    n_src: int
    n_tgt: int
    init_type: str
    cur_type: str
    src_text: str
    tgt_text: str
    score: float
    orig_score: float = 0.0
    marker: str = ""
    init_score_text: str = ""  # 捆绑编辑多行分数

    @property
    def op_index(self) -> int:
        return self.snap_index

    @property
    def is_divider(self) -> bool:
        """仅合并 [M] 的行之间需要虚线分隔。

        委托给 marker.py 的 is_divider() 统一管理。
        """
        return is_divider(self.marker, self.sub)


@dataclass
class TableViewModel:
    """表视图数据 + 单元格合并规则。"""

    rows: List[TableRow]
    spans: Dict[Tuple[int, int], Tuple[int, int]] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# 5. make_table_view — 构建表视图
# ═══════════════════════════════════════════════════════════════


def _render_merged(
    group: SnapGroup,
    snapshot: AlignmentSnapshot,
    sub_count: int,
) -> List[TableRow]:
    """为合并操作重建 TableRow 列表（保留原始文本，由 marker + is_divider 驱动虚线和着色）。"""
    rows: List[TableRow] = []
    for r in group.rows:
        rows.append(_raw_table_row(r))
    return rows


def _raw_table_row(r: AlignedRow) -> TableRow:
    """直接从 AlignedRow 转换为 TableRow。"""
    return TableRow(
        snap_index=r.snap_index,
        sub=r.sub,
        n_src=r.n_src,
        n_tgt=r.n_tgt,
        init_type=r.init_type,
        cur_type=r.cur_type,
        src_text=r.src_text,
        tgt_text=r.tgt_text,
        score=r.score,
        orig_score=r.orig_score,
        marker=r.marker,
        init_score_text=getattr(r, "init_score_text", ""),
    )


def compute_spans(
    rows: List[TableRow],
    col_offset: int = 0,
    snap_col: int | None = None,
) -> Dict[Tuple[int, int], Tuple[int, int]]:
    """计算单元格合并跨度。

    col_offset: 表格前的固定列数（如 GUI 的 Snap 列），
                使跨度偏移正确列位置。
                模型层 col_offset=0，GUI 层 col_offset=1。
    snap_col:   Snap 列索引（如 0），传入后自动为 snap 列生成跨行合并。
                消除 GUI 层多处重复的 Snap 列合并循环。

    同 snap 内:
      col 0-1 (+offset): 始终跨满（初始类型/评分）
      col 2-3 (+offset): 仅不对称操作跨满（当前类型/评分）
      col 4   (+offset): 仅 n_src < n_tgt 时跨（原文）
      col 5   (+offset): 仅 n_src > n_tgt 时跨（译文）
    """
    o = col_offset
    spans: Dict[Tuple[int, int], Tuple[int, int]] = {}

    i = 0
    while i < len(rows):
        r = rows[i]
        j = i
        while j < len(rows) and rows[j].snap_index == r.snap_index:
            j += 1
        count = j - i

        if count > 1:
            if snap_col is not None:
                spans[(i, snap_col)] = (count, 1)

            # ── 按 init_type 识别子组（支持跨 snap 合并的正确分组）──
            # 相同非空 init_type 的连续行属于同一原始 snap；
            # 在每个子组内部做单-snap 级别的单元格合并。
            sub_ranges: List[Tuple[int, int]] = []  # (start, end)
            si = i
            while si < j:
                while si < j and rows[si].init_type == "":
                    si += 1
                if si >= j:
                    break
                ki = si
                key = rows[si].init_type
                while si < j and (
                    rows[si].init_type == key or rows[si].init_type == ""
                ):
                    si += 1
                sub_ranges.append((ki, si))

            if not sub_ranges:
                sub_ranges = [(i, j)]

            for sub_i, sub_j in sub_ranges:
                sub_count = sub_j - sub_i
                sr = rows[sub_i]
                if sub_count > 1:
                    spans[(sub_i, 0 + o)] = (sub_count, 1)
                    spans[(sub_i, 1 + o)] = (sub_count, 1)
                    if sr.n_src != sr.n_tgt:
                        spans[(sub_i, 2 + o)] = (sub_count, 1)
                        spans[(sub_i, 3 + o)] = (sub_count, 1)
                    if sr.n_src < sr.n_tgt:
                        spans[(sub_i, 4 + o)] = (sub_count, 1)
                    if sr.n_src > sr.n_tgt:
                        spans[(sub_i, 5 + o)] = (sub_count, 1)
        i = j

    return spans


def make_table_view(state: RepairState) -> TableViewModel:
    """从 RepairState 构建 TableViewModel。

    - [M]: 从 snapshot 重建合并文本，行数由 action.sub_count 决定
    - [D]: 保留原始文本但 score=0, marker=[D]
    - [E]/[S]/[P]/[OK]/[F]/无标记: 直接取 row 数据
    """
    ch = state.current
    snap = state.snapshot
    rows: List[TableRow] = []

    for g in ch.groups:
        if not g.rows:
            continue
        r0 = g.rows[0]

        if is_merge(r0.marker):
            # info-free: 从 snapshot 重建合并文本
            action = state.action_for_op(r0.snap_index)
            sub_count = action.sub_count if action else max(r0.n_src, r0.n_tgt)
            rows.extend(_render_merged(g, snap, sub_count))

        elif is_deleted(r0.marker):
            # 删除: 保留原始文本，score=0，保留原有 marker（含来源前缀如 [AI][D]）
            for r in g.rows:
                tr = _raw_table_row(r)
                tr.score = 0.0
                tr.marker = r.marker  # 保留原 marker（含 [AI] 等来源前缀）
                rows.append(tr)

        else:
            # [E]/[S]/[P]/[OK]/[F]/无标记: 直接取 row 数据
            for r in g.rows:
                rows.append(_raw_table_row(r))

    spans = compute_spans(rows)
    return TableViewModel(rows=rows, spans=spans)


# ═══════════════════════════════════════════════════════════════
# 6. RepairService — 修复操作统一入口
# ═══════════════════════════════════════════════════════════════


class RepairService:
    """所有修复操作的统一入口。GUI 和 CLI 共享同一套逻辑。"""

    # ── 公开 API ──

    @staticmethod
    def align_and_repair(
        src_lines: List[str],
        tgt_lines: List[str],
        model,
        strategy: str = "minimal",
        cache_dir: str = "",
    ) -> Tuple[List[str], List[str], List[float]]:
        """完整对齐+修复管线：文本行 → 1:1 文本对。

        用法:
          from dualign.services.repair import RepairService
          new_src, new_tgt, scores = RepairService.align_and_repair(
              src_lines, tgt_lines, model, strategy="minimal"
          )

        strategy:
          "src"     — 原文为准：拆分/占位
          "tgt"     — 译文为准：拆分/占位
          "minimal" — 最小信息量：仅合并+删除，不引入新信息（默认推荐）

        返回: (src_out_lines, tgt_out_lines, scores) — 长度相等，全 1:1。
        """
        src_emb = np.array(model.encode(src_lines, normalize_embeddings=True))
        tgt_emb = np.array(model.encode(tgt_lines, normalize_embeddings=True))

        # 缓存本次编码的嵌入
        if cache_dir:
            try:
                from dualign.services.embedding_cache import EmbeddingCache
                from dualign.services.cached_encoder import CachedEncoder

                ec = EmbeddingCache(os.path.join(cache_dir, "vecs.db"))
                cenc = CachedEncoder(model, ec)
                # 始终通过 CachedEncoder 查缓存，有则复用，无则编码
                src_emb = cenc.encode(src_lines)
                tgt_emb = cenc.encode(tgt_lines)
            except Exception:
                pass
        result = align(
            src_lines,
            tgt_lines,
            src_emb,
            tgt_emb,
            config=AlignConfig(),
            silent=True,
        )
        snap = AlignmentSnapshot.from_alignment(result.all_ops, src_lines, tgt_lines)
        state = RepairState(snap)
        repaired = RepairService.auto_repair(state, strategy=strategy, model=model)

        # 使用统一的 render_rows 确保 merge 组正确拼接文本
        src_out, tgt_out = RepairService.render_rows(repaired)

        # 从 render_rows 的语义重建分数列表（与行数一致）
        ch = repaired.current
        scores_out: List[float] = []
        for g in ch.groups:
            if not g.rows:
                continue
            r0 = g.rows[0]
            if is_deleted(r0.marker):
                continue
            if is_merge(r0.marker):
                # merge 组在 render_rows 中输出 1 行，取第一个子行分数
                scores_out.append(r0.score)
            else:
                # 非 merge 组：逐行输出，逐行取分数
                for r in g.rows:
                    scores_out.append(r.score)
        return src_out, tgt_out, scores_out

    @staticmethod
    def auto_repair(
        state: RepairState,
        strategy: str = "src",
        model=None,
        anchor_ratio: float = 1.0,
        max_anchor_gap: int = 0,
        cache: Optional[EmbeddingCache] = None,
    ) -> RepairState:
        """遍历所有非 1:1 的 snap，按策略一键修复。

        门控（任一触发即拒绝）：
          - anchor_ratio < 20%（锚点覆盖率不足）
          - max_anchor_gap > 50（大段无引导）

        核心原则: 每种策略保持首选侧不动，修改另一侧。

        核心原则: 每种策略保持首选侧不动，修改另一侧。
          - src-first:  保持原文不动 → 修改译文侧
          - tgt-first:  保持译文不动 → 修改原文侧
          - minimal:    不引入新信息（只合并，不拆分/插入）

        策略矩阵:
          | Type   | src-first        | tgt-first        | minimal     |
          |--------|------------------|------------------|-------------|
          | N:1    | split tgt  [S]   | merge src  [M]   | merge src [M]|
          | 1:M    | merge tgt  [M]   | split src  [S]   | merge tgt [M]|
          | 1:0    | placeholder [P]  | delete     [D]   | delete    [D]|
          | 0:1    | delete     [D]   | placeholder [P]  | delete    [D]|

        拆分需要 model。无 model 时回退到同类型 minimal 的合并操作。
        """
        # 门控：锚点覆盖率不足 或 大段无引导 → 拒绝
        if anchor_ratio < 0.20 or max_anchor_gap > 50:
            return state
        result = state
        snap = result.snapshot

        for snap_i in range(len(snap.original_ops)):
            s_idx, t_idx, _sc = snap.original_ops[snap_i]
            ls, lt = len(s_idx), len(t_idx)

            if ls == 1 and lt == 1:
                continue

            if ls > 1 and lt == 1:
                if strategy == "src" and model is not None:
                    result = RepairService.apply_split(
                        result, snap_i, "tgt", model, cache=cache
                    )
                else:
                    result = RepairService.repair_merge(result, snap_i)

            elif ls == 1 and lt > 1:
                if strategy == "tgt" and model is not None:
                    result = RepairService.apply_split(
                        result, snap_i, "src", model, cache=cache
                    )
                else:
                    result = RepairService.repair_merge(result, snap_i)

            elif ls > 0 and lt == 0:
                # 1:0 或 N:0（连续无匹配区间的容器）
                if strategy == "src":
                    result = RepairService.repair_placeholder(result, snap_i, "tgt")
                else:
                    result = RepairService.repair_delete(result, snap_i)

            elif ls == 0 and lt > 0:
                # 0:1 或 0:M（连续无匹配区间的容器）
                if strategy == "tgt":
                    result = RepairService.repair_placeholder(result, snap_i, "src")
                else:
                    result = RepairService.repair_delete(result, snap_i)

            else:
                # N:M（ls>1 and lt>1）→ 特殊区域，可直接标记占位符
                # 备择策略：留待人工或 AI 判断
                if strategy in ("src", "tgt"):
                    result = RepairService.repair_placeholder(result, snap_i, "tgt")
                else:
                    result = RepairService.repair_delete(result, snap_i)

        return result

    # ── 单步修复 ──

    @staticmethod
    def repair_merge(state: RepairState, snap_i: int) -> RepairState:
        """合并文本对，仅设 marker。"""
        s_idx, t_idx, _sc = state.snapshot.original_ops[snap_i]
        sub_count = max(len(s_idx), len(t_idx))
        return state.apply(
            RepairAction.make_merge(snap_i, sub_count=sub_count, source="auto")
        )

    @staticmethod
    def repair_delete(state: RepairState, snap_i: int) -> RepairState:
        """删除文本对。"""
        return state.apply(RepairAction.make_delete(snap_i, source="auto"))

    @staticmethod
    def repair_placeholder(state: RepairState, snap_i: int, side: str) -> RepairState:
        """占位符：保留非空侧，空侧填 MISSING。"""
        if side == "src":
            action = RepairAction.make_placeholder_src(snap_i, source="auto")
        else:
            action = RepairAction.make_placeholder_tgt(snap_i, source="auto")
        return state.apply(action)

    # ── 拆分 ──

    @staticmethod
    def apply_split(
        state: RepairState,
        snap_i: int,
        side: str,
        model=None,
        cache: Optional[EmbeddingCache] = None,
    ) -> RepairState:
        """拆分操作：硬分割少行侧 → 重对齐（仅 1:1 + N:1/1:M，无 1:0/0:1）→ 存为 split。

        side: 要拆分的一侧 ("src" 或 "tgt")。通常是少行侧。

        重对齐使用 AlignConfig(allow_deletions=False, allow_insertions=False)，
        禁止 DP 产生 1:0/0:1 操作；auto_repair 仅执行 merge，绝不 delete，
        确保 split+realign 不会丢弃任何内容。
        """
        from dualign.core.punctuation import UniversalSplitter

        snap = state.snapshot
        s_idx, t_idx, _sc = snap.original_ops[snap_i]

        raw_src = [snap.src_text(i) for i in s_idx]
        raw_tgt = [snap.tgt_text(j) for j in t_idx]

        # 1. 硬分割拆分侧
        if side == "src":
            parts: List[str] = []
            for line in raw_src:
                sub = UniversalSplitter.hard_split(line)
                parts.extend(sub if sub else [line])
            if len(parts) <= len(raw_src):
                return state
        else:
            parts: List[str] = []
            for line in raw_tgt:
                sub = UniversalSplitter.hard_split(line)
                parts.extend(sub if sub else [line])
            if len(parts) <= len(raw_tgt):
                return state

        if model is None:
            return state

        # 2. 重对齐：禁止 1:0/0:1，仅允许 1:1 + N:1/1:M
        src_in = parts if side == "src" else raw_src
        tgt_in = raw_tgt if side == "src" else parts

        # 始终通过 CachedEncoder 查缓存（如果 cache 可用），否则盲编码
        if cache is not None and model is not None:
            from dualign.services.cached_encoder import CachedEncoder

            cenc = CachedEncoder(model, cache)
            src_emb = cenc.encode(src_in)
            tgt_emb = cenc.encode(tgt_in)
        else:
            src_emb = np.array(model.encode(src_in, normalize_embeddings=True))
            tgt_emb = np.array(model.encode(tgt_in, normalize_embeddings=True))
        split_cfg = AlignConfig(
            allow_deletions=False,
            allow_insertions=False,
            allow_merge=True,
        )
        result = align(
            src_in,
            tgt_in,
            src_emb,
            tgt_emb,
            config=split_cfg,
            encode_fn=model.encode,
            silent=True,
        )
        snap_split = AlignmentSnapshot.from_alignment(result.all_ops, src_in, tgt_in)

        # auto-repair：仅合并，不引入 [D]/[P]
        split_state = RepairState(snap_split)
        repaired = split_state
        for si in range(len(snap_split.original_ops)):
            _s, _t, _ = snap_split.original_ops[si]
            if len(_s) != 1 or len(_t) != 1:
                repaired = RepairService.repair_merge(repaired, si)

        # 展平为 (src_out, tgt_out, scores)
        # 使用 render_rows 语义：合并组 [M] 拼接文本为 1 行，删除组 [D] 跳过
        ch = repaired.current
        snap_loc = repaired.snapshot
        new_src: List[str] = []
        new_tgt: List[str] = []
        scores: List[float] = []
        for g in ch.groups:
            if not g.rows:
                continue
            r0 = g.rows[0]
            marker = r0.marker
            s_idx, t_idx, _sc = (
                snap_loc.original_ops[g.snap_i]
                if g.snap_i < len(snap_loc.original_ops)
                else ((), (), 0.0)
            )

            if is_deleted(marker):
                continue

            if is_merge(marker):
                # 合并组：用 _smart_join_lines 拼接文本
                src_parts = [
                    snap_loc.src_text(i) for i in s_idx if snap_loc.src_text(i)
                ]
                tgt_parts = [
                    snap_loc.tgt_text(j) for j in t_idx if snap_loc.tgt_text(j)
                ]
                new_src.append(_smart_join_lines(src_parts))
                new_tgt.append(_smart_join_lines(tgt_parts))
                scores.append(r0.score)
            else:
                # 非合并组：逐行输出
                for r in g.rows:
                    new_src.append(r.src_text or "")
                    new_tgt.append(r.tgt_text or "")
                    scores.append(r.score)

        if not new_src or not new_tgt:
            return state

        # 3. 存为 info-full split action
        action = RepairAction.make_split(
            snap_i,
            new_src_lines=new_src,
            new_tgt_lines=new_tgt,
            split_scores=scores,
            side=side,
            source="auto",
        )
        return state.apply(action)

    # ── 跨 snap 操作 ──

    @staticmethod
    def repair_bundle_snaps(
        state: RepairState,
        snap_list: List[int],
    ) -> RepairState:
        """跨 snap 合并：将多个 snap 捆绑为一个文本对。

        snap_list 必须连续。非 anchor snaps 被移除，
        原文/译文均合并到 anchor snap。统一为 kind="merge"。

        自动消除占位、删除、拆分操作；但保留 edit 操作不撤销。
        """
        if len(snap_list) < 2:
            return state

        anchor = snap_list[0]

        # 选择性重置：消除 placeholder/delete/split，保留 edit
        for si in snap_list:
            action = state.action_for_op(si)
            if action is None:
                continue
            kind = action.kind
            # edit 操作保留，其余（placeholder_src/tgt, delete, split, ok, flag, merge）均重置
            if kind not in ("edit",):
                state = state.reset_op(si)

        action = RepairAction(
            op_index=anchor,
            kind="merge",
            data={"orig_snaps": list(snap_list)},
        )
        return state.apply(action)

    @staticmethod
    def valid_operations(state: RepairState, snap_i: int) -> Dict[str, bool]:
        """返回该 snap 可用的操作集合。GUI 据此启用/禁用按钮和菜单项。

        规则 (单 snap):
          N:1 (ls>1,lt==1): merge=Y, split=tgt, edit=Y
          1:M (ls==1,lt>1): merge=Y, split=src, edit=Y
          1:0 (ls>0,lt==0): merge=N, split=N, edit=Y
          0:1 (ls==0,lt>0): merge=N, split=N, edit=Y
          1:1:               merge=N, split=N, edit=Y

        多 snap 选中时 merge 始终可用（捆绑合并）。
        """
        snap = state.snapshot
        s_idx, t_idx, _sc = snap.original_ops[snap_i]
        ls, lt = len(s_idx), len(t_idx)

        is_non11 = ls != 1 or lt != 1
        is_10 = ls > 0 and lt == 0
        is_01 = ls == 0 and lt > 0

        # 已有操作时，某些操作被覆盖
        action = state.action_for_op(snap_i)
        has_action = action is not None

        ch = state.current
        g = ch.group(snap_i)
        is_11_now = g is not None and all(r.cur_type == "1:1" for r in g.rows)
        marker = g.rows[0].marker if g else ""
        resolved_to_11 = is_merge(marker) or is_placeholder(marker)
        is_del = is_deleted(marker)
        already_ok = is_approved(marker)

        return {
            "merge": is_non11 and not is_10 and not is_01,
            "split_src": ls > 1 and lt == 1,
            "split_tgt": ls == 1 and lt > 1,
            "edit": True,
            "ok": (is_11_now or resolved_to_11 or is_del) and not already_ok,
            "flag": True,
            "delete": True,
            "placeholder": is_10 or is_01,
            "reset": has_action,
        }

    # ── 跨 snap 校订 ──

    @staticmethod
    def repair_multi_edit(
        state: RepairState,
        snap_list: List[int],
        new_src_lines: List[str],
        new_tgt_lines: List[str],
        scores: Optional[List[float]] = None,
    ) -> RepairState:
        """跨 snap 校订：将多个连续文本对捆绑为一个编辑组。

        snap_list 必须连续。非 anchor snaps 被删除，anchor 存放合并后的文本。
        """
        if len(snap_list) < 2:
            return state

        anchor = snap_list[0]
        action = RepairAction.make_edit(
            anchor,
            orig_snaps=list(snap_list),
            new_src_lines=new_src_lines,
            new_tgt_lines=new_tgt_lines,
            inherited_scores=scores or [],
        )
        return state.apply(action)

    # ── 渲染/导出 ──

    @staticmethod
    def render_rows(
        state: RepairState,
    ) -> Tuple[List[str], List[str]]:
        """从 RepairState 重建 src/tgt 文本输出。

        规则:
          [D]: 跳过不输出
          [M]: 从 snapshot 取原始文本 + _smart_join 合并一侧
          [E]/[S]: 取 row.src_text / row.tgt_text
          [P]: 空侧输出 MISSING
          无标记: 从 snapshot 取原始文本
        """
        ch = state.current
        snap = state.snapshot

        src_out: List[str] = []
        tgt_out: List[str] = []

        for g in ch.groups:
            if not g.rows:
                # 空的 SnapGroup（可能来自跨 snap 操作后的残留），跳过
                continue
            r0 = g.rows[0]
            marker = r0.marker
            s_idx, t_idx, _sc = snap.original_ops[g.snap_i]

            if is_deleted(marker):
                continue  # 跳过删除

            if is_merge(marker):
                # merge: 原文译文均合并为一行
                # 检查是否为跨 snap 合并（bundle merge）
                action = state.action_for_op(g.snap_i)
                orig_snaps = action.data.get("orig_snaps", []) if action else []
                if len(orig_snaps) > 1:
                    # 跨 snap 合并：收集所有被捆绑 snap 的文本再拼接
                    src_parts: List[str] = []
                    tgt_parts: List[str] = []
                    for si in orig_snaps:
                        try:
                            si_int = int(si)
                        except (TypeError, ValueError):
                            continue
                        if si_int >= len(snap.original_ops):
                            continue
                        ss, tt, _ = snap.original_ops[si_int]
                        for i in ss:
                            t = snap.src_text(i)
                            if t:
                                src_parts.append(t)
                        for j in tt:
                            t = snap.tgt_text(j)
                            if t:
                                tgt_parts.append(t)
                    src_line = _smart_join_lines(src_parts) if src_parts else ""
                    tgt_line = _smart_join_lines(tgt_parts) if tgt_parts else ""
                else:
                    # 单 snap 合并：直接取当前 snap 的原始索引
                    src_line = _smart_join_lines([snap.src_text(i) for i in s_idx])
                    tgt_line = _smart_join_lines([snap.tgt_text(j) for j in t_idx])
                src_out.append(src_line)
                tgt_out.append(tgt_line)

            elif is_edit(marker) or is_split(marker):
                # info-full: 取内联文本
                for row in g.rows:
                    src_out.append(row.src_text or MISSING)
                    tgt_out.append(row.tgt_text or MISSING)

            elif is_placeholder(marker):
                # 占位符
                for row in g.rows:
                    src_out.append(row.src_text or MISSING)
                    tgt_out.append(row.tgt_text or MISSING)

            else:
                # 无标记: 直接从 group rows 输出（始终等行数，短侧补空）
                for row in g.rows:
                    src_out.append(row.src_text or "")
                    tgt_out.append(row.tgt_text or "")

        return src_out, tgt_out

    @staticmethod
    def render_to_files(
        state: RepairState,
        src_path: str,
        tgt_path: str,
    ) -> None:
        """从 RepairState 渲染原文/译文输出文件。

        Args:
            state:  当前或最终的 RepairState
            src_path: 原文输出路径
            tgt_path: 译文输出路径
        """
        from dualign.common import format_markdown_output

        src_lines, tgt_lines = RepairService.render_rows(state)
        os.makedirs(os.path.dirname(src_path) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(tgt_path) or ".", exist_ok=True)
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(format_markdown_output(src_lines))
        with open(tgt_path, "w", encoding="utf-8") as f:
            f.write(format_markdown_output(tgt_lines))

    @staticmethod
    def apply_ai_actions(
        state: RepairState, actions: List[RepairAction]
    ) -> RepairState:
        """批量应用 AI 审校操作。

        将 AI 返回的操作列表应用到 RepairState 上，
        返回包含 AI 操作的新 RepairState。
        ok 标记也被应用——它是 AI 认可当前状态的显式声明。
        """
        s = state
        for a in actions:
            s = s.apply(a)
        return s
