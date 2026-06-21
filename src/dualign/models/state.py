"""
Dualign — ChapterState: 重放后的章节状态

数据流: AlignmentSnapshot + RepairAction[] → replay() → ChapterState

核心原则:
  1. snap_i 始终指向 original_ops[snap_i]（外部索引永不变化）
  2. sub 仅在 SnapGroup.rows 内部有意义
  3. info-free 操作仅存 marker，文本在渲染时从 snapshot 重建
  4. info-full 操作存储完整新文本
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple, Optional, List

from dualign.models.marker import (
    is_resolved_to_11,
    needs_zero_score,
    is_merge,
)
from dualign.core import op_type_str

# ═══════════════════════════════════════════════════════════════
# AlignmentSnapshot — 不可变对齐快照
# ═══════════════════════════════════════════════════════════════

OpT = Tuple[Tuple[int, ...], Tuple[int, ...], float]
MISSING = "\u27e2MISSING\u27e3"


@dataclass(frozen=True)
class AlignmentSnapshot:
    """对齐完成时的不可变快照。

    original_ops:        对齐操作序列 [(src_indices, tgt_indices, score), ...]
    original_src_lines:  原始原文行
    original_tgt_lines:  原始译文行

    外部索引 snap_i 始终指向 original_ops[snap_i]。
    """

    original_ops: Tuple[OpT, ...]
    original_src_lines: Tuple[str, ...]
    original_tgt_lines: Tuple[str, ...]

    @classmethod
    def from_alignment(
        cls, all_ops: list, src_lines: list, tgt_lines: list
    ) -> AlignmentSnapshot:
        """从对齐结果构造快照。"""
        return cls(
            original_ops=tuple((tuple(s), tuple(t), float(sc)) for s, t, sc in all_ops),
            original_src_lines=tuple(src_lines),
            original_tgt_lines=tuple(tgt_lines),
        )

    @property
    def ops_list(self) -> list:
        return list(self.original_ops)

    @property
    def src_list(self) -> list:
        return list(self.original_src_lines)

    @property
    def tgt_list(self) -> list:
        return list(self.original_tgt_lines)

    def src_text(self, idx: int) -> str:
        if 0 <= idx < len(self.original_src_lines):
            return self.original_src_lines[idx].rstrip()
        return ""

    def tgt_text(self, idx: int) -> str:
        if 0 <= idx < len(self.original_tgt_lines):
            return self.original_tgt_lines[idx].rstrip()
        return ""

    def to_dict(self) -> dict:
        return {
            "original_ops": [
                {"s": list(s), "t": list(t), "score": round(sc, 4)}
                for s, t, sc in self.original_ops
            ],
            "original_src_lines": list(self.original_src_lines),
            "original_tgt_lines": list(self.original_tgt_lines),
        }


# ═══════════════════════════════════════════════════════════════
# AlignedRow — 表格行数据载体
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AlignedRow:
    """单个表格行（不可变数据载体）。

    snap_index:  外部索引（指向 snapshot.original_ops）
    sub:         内部相对索引（0, 1, 2...）
    init_type:   初始对齐类型 ("3:1", "1:2", "1:1" 等)
    cur_type:    当前类型（通常是 "1:1"）
    src_text:    原文文本
    tgt_text:    译文文本
    score:       当前评分
    orig_score:  初始评分（来自 snapshot）
    n_src:       原文行数
    n_tgt:       译文行数
    marker:      操作标记 ("" / "[M]" / "[S]" / "[E]" / "[D]" / "[P]" / "[F]" / "[OK]")
    """

    snap_index: int
    sub: int
    init_type: str
    cur_type: str
    src_text: str
    tgt_text: str
    score: float
    orig_score: float
    n_src: int
    n_tgt: int
    marker: str = ""
    init_score_text: str = ""  # 捆绑编辑时多行分数文本

    @property
    def is_divider(self) -> bool:
        """仅合并 [M] 的行之间需要虚线分隔。

        委托给 marker.py 的 is_divider() 统一管理。
        """
        from dualign.models.marker import is_divider as _is_divider

        return _is_divider(self.marker, self.sub)


# ═══════════════════════════════════════════════════════════════
# SnapGroup — 一个初始文本对的当前状态
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SnapGroup:
    """一个初始对齐文本对 (snap_i) 的当前状态。

    snap_i: 外部索引，指向 AlignmentSnapshot.original_ops，永不变化。
    rows:   内部子行 (sub=0,1,2...)。info-free 操作只改 marker。
    """

    snap_i: int
    rows: Tuple[AlignedRow, ...]

    # ── 构造器 ──

    @classmethod
    def from_snapshot(cls, snap_i: int, snapshot: AlignmentSnapshot) -> SnapGroup:
        """从快照构建初始 SnapGroup。"""
        s_idx, t_idx, sc = snapshot.original_ops[snap_i]
        it = op_type_str(s_idx, t_idx)
        n = max(len(s_idx), len(t_idx))
        rows: List[AlignedRow] = []
        for sub in range(n):
            rows.append(
                AlignedRow(
                    snap_index=snap_i,
                    sub=sub,
                    init_type=it if sub == 0 else "",
                    cur_type=it,
                    src_text=snapshot.src_text(s_idx[sub]) if sub < len(s_idx) else "",
                    tgt_text=snapshot.tgt_text(t_idx[sub]) if sub < len(t_idx) else "",
                    score=float(sc),
                    orig_score=float(sc),
                    n_src=len(s_idx),
                    n_tgt=len(t_idx),
                )
            )
        return cls(snap_i=snap_i, rows=tuple(rows))

    # ── 修改器（返回新 SnapGroup） ──

    def with_marker(self, marker: str) -> SnapGroup:
        """对所有行设置相同的 marker。返回新 SnapGroup。

        兼容格式: "[M]", "[AI][OK]", "[M] [AI][OK]"
        cur_type 改为 1:1 的条件: marker 含 [M], [S], [P], [OK]
        """
        new_cur = "1:1" if is_resolved_to_11(marker) else self.rows[0].cur_type
        zero_score = needs_zero_score(marker)
        # [M]: 保留原始 n_src/n_tgt，让 _compute_spans 能正确判断少行侧的列跨行合并。
        #      例如 2:1 → 译文列跨行，第 2 行继承译文文本。
        # [S]/[P]/[OK]: 逻辑上变为 1:1，各子行独立显示。
        if is_merge(marker):
            logical_n_src = self.rows[0].n_src
            logical_n_tgt = self.rows[0].n_tgt
        elif is_resolved_to_11(marker):
            logical_n_src = 1
            logical_n_tgt = 1
        else:
            logical_n_src = self.rows[0].n_src
            logical_n_tgt = self.rows[0].n_tgt
        return SnapGroup(
            snap_i=self.snap_i,
            rows=tuple(
                AlignedRow(
                    snap_index=r.snap_index,
                    sub=r.sub,
                    init_type=r.init_type,
                    cur_type=new_cur,
                    src_text=r.src_text,
                    tgt_text=r.tgt_text,
                    score=0.0 if zero_score else r.score,
                    orig_score=r.orig_score,
                    n_src=logical_n_src,
                    n_tgt=logical_n_tgt,
                    marker=marker,
                )
                for r in self.rows
            ),
        )

    def with_text(
        self, texts: List[tuple], scores: List[float], marker: str = "[E]"
    ) -> SnapGroup:
        """info-full 操作：用完整新文本对替换。texts = [(src, tgt), ...]"""
        it = self.rows[0].init_type
        osc = self.rows[0].orig_score
        n = len(texts)
        return SnapGroup(
            snap_i=self.snap_i,
            rows=tuple(
                AlignedRow(
                    snap_index=self.snap_i,
                    sub=0,
                    init_type=it if k == 0 else "",
                    cur_type="1:1",
                    src_text=texts[k][0],
                    tgt_text=texts[k][1],
                    score=(
                        scores[k] if k < len(scores) else (scores[0] if scores else osc)
                    ),
                    orig_score=osc,
                    n_src=n,
                    n_tgt=n,
                    marker=marker,
                )
                for k in range(n)
            ),
        )


# ═══════════════════════════════════════════════════════════════
# ChapterState — 重放后的章节状态
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ChapterState:
    """整章状态：所有 SnapGroup 的有序集合。

    groups:   按 snap_i 排序的 SnapGroup 元组
    snapshot: 原始对齐快照（始终引用，不做拷贝）

    GUI 渲染统一入口: ChapterState.rows
    """

    groups: Tuple[SnapGroup, ...]
    snapshot: AlignmentSnapshot

    # ── 构造器 ──

    @classmethod
    def from_snapshot(cls, snapshot: AlignmentSnapshot) -> ChapterState:
        """从快照构建初始 ChapterState。"""
        return cls(
            groups=tuple(
                SnapGroup.from_snapshot(i, snapshot)
                for i in range(len(snapshot.original_ops))
            ),
            snapshot=snapshot,
        )

    # ── 属性 ──

    @property
    def rows(self) -> Tuple[AlignedRow, ...]:
        """所有行（按 snap_i 排序）。GUI 渲染统一入口。"""
        result: List[AlignedRow] = []
        for g in self.groups:
            result.extend(g.rows)
        return tuple(result)

    # ── 查询 ──

    def group(self, snap_i: int) -> Optional[SnapGroup]:
        """按外部索引查找 SnapGroup。"""
        for g in self.groups:
            if g.snap_i == snap_i:
                return g
        return None

    # ── 结构操作（返回新 ChapterState） ──

    def replace_snap(self, snap_i: int, group: SnapGroup) -> ChapterState:
        """替换指定 snap 的 group。"""
        return ChapterState(
            groups=tuple(group if g.snap_i == snap_i else g for g in self.groups),
            snapshot=self.snapshot,
        )

    def remove_snap(self, snap_i: int) -> ChapterState:
        """删除指定 snap。"""
        return ChapterState(
            groups=tuple(g for g in self.groups if g.snap_i != snap_i),
            snapshot=self.snapshot,
        )
