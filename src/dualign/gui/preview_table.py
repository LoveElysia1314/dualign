"""
Dualign 0.7.0 — SuggestionPreviewTable + AiSuggestionItem

AI 建议表格的行数据模型 + 预览表。
7 列，与主对齐表完全一致，仅 3 列覆写为预览。
"""

from __future__ import annotations

from typing import Set

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QSizePolicy

from dualign.models.action import RepairAction
from dualign.models.marker import from_kind as _marker_from_kind
from dualign.models.marker import is_divider as _is_divider
from dualign.models.marker import AI_PREFIX
from dualign.gui.base_table import (
    BaseTextTable,
    make_score_cell,
    CHANGED_FLAG_ROLE,
    marker_cl,
    type_cl,
)
import dualign.gui.base_table as _color_table  # 主题感知颜色，通过模块访问

# ═══════════════════════════════════════════════════════════════
# AiSuggestionItem — 表格行数据（含子行支持）
# ═══════════════════════════════════════════════════════════════


class AiSuggestionItem:
    """AI 建议的表格行数据，支持子行跨行合并。

    每个 RepairAction 可能包含多行原文/译文（如合并[M]、拆分[S]），
    此时生成多个子行，子行的 snap_index/index 相同，sub 递增。
    """

    def __init__(
        self,
        snap_index: int,
        action: RepairAction,
        status: str = "pending",
        sub: int = 0,
        src_line: str = "",
        tgt_line: str = "",
        init_type: str = "",
        cur_type: str = "",
        init_score: float = 0.0,
        score: float = 0.0,
        n_src: int = 1,
        n_tgt: int = 1,
        init_src_text: str = "",
        init_tgt_text: str = "",
    ):
        self.snap_index = snap_index
        self.action = action
        self.status = status
        self.sub = sub
        self.kind = action.kind
        self.src_text = src_line
        self.tgt_text = tgt_line
        self.init_type = init_type
        self.cur_type = cur_type
        self.init_score = init_score
        self.score = score
        self.n_src = n_src
        self.n_tgt = n_tgt
        self.is_divider = False
        self.orig_score = init_score
        self.init_src_text = init_src_text
        self.init_tgt_text = init_tgt_text
        self.star_src: bool = False
        self.star_tgt: bool = False

    @property
    def marker(self) -> str:
        return self.action.marker if self.action else _marker_from_kind(self.kind)


class SuggestionPreviewTable(BaseTextTable):
    """AI 建议执行效果预览表。7 列，3 列覆写为预览数据。

    使用基类钩子体系，无需覆写 _render() 整体流程。
    """

    COL_HEADERS = [
        "Snap",
        "初始类型",
        "初始评分",
        "预览状态",
        "预览评分",
        "原文预览",
        "译文预览",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._show_scores = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _configure_table(self):
        super()._configure_table()
        hdr = self.table.horizontalHeader()
        from dualign.gui.base_table import calc_snap_width as _csw

        for ci in range(5):
            hdr.setSectionResizeMode(ci, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(0, _csw(0))
        hdr.resizeSection(1, 64)
        hdr.resizeSection(2, 60)
        hdr.resizeSection(3, 64)
        hdr.resizeSection(4, 60)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)

    # ── 基类钩子实现 ──

    def _pre_render(self):
        """渲染前：评分列明细→数字，紧凑→6px 色带。"""
        show = self._show_scores
        hdr = self.table.horizontalHeader()
        _HEADERS = self.COL_HEADERS
        for col in (2, 4):
            self.table.setColumnHidden(col, False)
            hdr.resizeSection(col, 60 if show else 6)
            self.table.horizontalHeaderItem(col).setText(_HEADERS[col] if show else "")

    def _get_span_col_offset(self) -> int:
        """第 0 列是 Snap，span 从第 1 列开始。"""
        return 1

    def _get_snap_col(self) -> int | None:
        """预览表也有 Snap 列 (col 0)。"""
        return 0

    def _apply_table_spans(self, spans: dict):
        """应用标准 span（Snap 列已由基类 _compute_table_spans 处理）。"""
        table = self.table
        for (sr, col), (rs, cs) in spans.items():
            if sr < len(self._items) and rs > 1 and col < table.columnCount():
                table.setSpan(sr, col, rs, cs)

    def _get_hidden_cur_rows(self, spans: dict) -> Set[int]:
        """col_offset=1 时，预览状态列在 table col 3（即 span col 3）。"""
        hidden = set()
        for (sr, c_), (_, cnt) in spans.items():
            if c_ == 3 and cnt > 1:
                for ri in range(sr + 1, sr + cnt):
                    hidden.add(ri)
        return hidden

    def _get_divider_rows(self) -> Set[int]:
        return {i for i, it in enumerate(self._items) if _is_divider(it.marker, it.sub)}

    def _extra_row_kwargs(self, row: int, item, hidden_cur_rows: Set[int]) -> dict:
        return {"hide_cur": row in hidden_cur_rows}

    def _render_row(self, row: int, item: AiSuggestionItem, hide_cur: bool = False):
        """渲染一行。

        评分列与主对齐表行为一致：
          - 紧凑模式 (not _show_scores): 评分列隐藏，类型列 (1,3) 设 BackgroundRole
            供 HighlightDelegate 绘制 5px 竖条色带
          - 明细模式 (_show_scores): 评分列显示，文本本身用 score_to_color 着色

        星标：与主对齐表统一使用 has_snap_text_changed 语义，
        比较 init_src_text/init_tgt_text 与当前预览文本。
        """
        marker = item.marker
        # 预览表中 [AI] 前缀冗余（整表已是 AI 建议上下文），剥离后显示
        display_marker = marker.replace(AI_PREFIX, "").strip()
        is_first = item.sub == 0
        is_del = "[D]" in marker or marker == "[D]"

        # 统一方案下 [AI] 前缀仅与操作标记结合出现（如 [AI][OK]），
        # 剥离前缀后自然显示为 [OK]，无需独立转译。

        snap_text = str(item.snap_index) if is_first else ""
        self._set_cell(row, 0, snap_text, align=Qt.AlignCenter)

        # Col 1: 初始类型
        init_text = item.init_type
        ic = type_cl(item.init_type) if item.init_type else _color_table.TYPE_CL_11
        self._set_cell(row, 1, init_text, fg=ic.name(), align=Qt.AlignCenter)

        # Col 2: 初始评分（紧凑模式→色带，明细模式→数字）
        self.table.setItem(row, 2, make_score_cell(item.init_score, self._show_scores))

        # Col 3: 预览状态（显示时剥离 [AI] 前缀）
        show_cur = is_first or not hide_cur
        if item.status == "已应用":
            cur = "✓ " + (display_marker if (display_marker and show_cur) else "")
        else:
            cur = display_marker if (display_marker and show_cur) else ""
        # 颜色：纯 AI 确认时用 [OK] 色（绿色），否则用原始 marker 色
        color_marker = display_marker if marker == AI_PREFIX else marker
        fg_cur = marker_cl(color_marker) if color_marker else _color_table.TYPE_CL_11
        self._set_cell(row, 3, cur, fg=fg_cur.name(), align=Qt.AlignCenter)

        # Col 4: 预览评分（紧凑模式→色带，明细模式→数字）
        self.table.setItem(row, 4, make_score_cell(item.score, self._show_scores))

        # Col 5-6: 原文/译文预览 — 星标由 _rebuild_ai_suggestions 按语义计算
        src_changed = item.star_src
        tgt_changed = item.star_tgt

        for col, text, changed in (
            (5, item.src_text, src_changed),
            (6, item.tgt_text, tgt_changed),
        ):
            if is_del:
                cell = self._set_cell(
                    row, col, text, fg=_color_table.TEXT_CL_DELETED.name()
                )
                f = cell.font()
                f.setStrikeOut(True)
                cell.setFont(f)
            elif marker:
                self._set_cell(row, col, text, fg=marker_cl(color_marker).name())
            else:
                self._set_cell(row, col, text, fg=_color_table.TEXT_CL_NORMAL.name())
            if changed:
                cell = self.table.item(row, col)
                if cell:
                    cell.setData(CHANGED_FLAG_ROLE, True)

    def sync_from_main_table(self, main_table, filter_panel):
        """同步列宽和评分列可见性。

        评分列可见性与主表一致（受「评分明细」复选框控制）。
        """
        self._show_scores = filter_panel.show_scores if filter_panel else False
        self._render()
        from PySide6.QtCore import QTimer

        if main_table.horizontalHeader().count() < 7:
            QTimer.singleShot(50, self._adjust_row_heights)
            return

        QTimer.singleShot(50, lambda: self._sync_widths(main_table))

    def _sync_widths(self, main_table):
        """实际执行列宽同步。只调尺寸不调内容。"""
        if main_table.horizontalHeader().count() < 7:
            self._adjust_row_heights()
            return
        hdr = main_table.horizontalHeader()
        phdr = self.table.horizontalHeader()
        for sc, dc in ((0, 0), (1, 1), (3, 3), (5, 5), (6, 6)):
            sz = hdr.sectionSize(sc)
            if sz > 40:
                phdr.resizeSection(dc, sz)
        self._adjust_row_heights()
