"""
Dualign 0.7.0 — BaseTextTable: 文本对表格基类

封装 QTableWidget 的通用设置、跨行合并、颜色单元创建、焦点/滚动管理等。
同时包含 HighlightDelegate（统一高亮/虚线/色标）和颜色工具函数。

子类只需定义:
  - COL_HEADERS: 列标题列表
  - _render_row(i, item) -> int: 渲染第 i 行数据，返回 snap_index
"""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar, List, Optional, Set, Tuple
import math

from PySide6.QtCore import Qt, QPointF, QTimer, QSize
from PySide6.QtGui import QColor, QPen, QPainter, QTextDocument
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QStyle,
)

from dualign.services.repair import compute_spans
from dualign.models.marker import resolve_hex_color
from dualign.gui.theme import T

# ═══════════════════════════════════════════════════════════════
# 自定义数据角色
# ═══════════════════════════════════════════════════════════════

CHANGED_FLAG_ROLE = Qt.ItemDataRole.UserRole + 100


def calc_snap_width(max_snap: int) -> int:
    """根据最大 snap 索引计算 Snap 列固定宽度。

    < 10000 (4 位): 40px, 此后每多一位 +8px
    """
    digits = len(str(max_snap))
    return 40 + max(0, digits - 4) * 8


# ═══════════════════════════════════════════════════════════════
# 颜色工具
# ═══════════════════════════════════════════════════════════════


def score_to_color(score: float) -> QColor:
    """评分 → 颜色。0→红, 0.5→黄, 1→绿（RdYlGn 平滑线性插值）。"""
    s = max(0.0, min(1.0, score))
    if s <= 0.5:
        t = s / 0.5
        r = int(220 + 35 * t)
        g = int(50 + 205 * t)
        b = 50
    else:
        t = (s - 0.5) / 0.5
        r = int(255 - 205 * t)
        g = 220
        b = 50
    return QColor(r, g, b)


_DARK_TYPE_11 = "#B0B0B0"
_DARK_TYPE_NON11 = "#C09500"
_DARK_TYPE_10_01 = "#D32F2F"
_DARK_TEXT_NORMAL = "#D0D0D0"
_DARK_TEXT_DELETED = "#B85454"
_DARK_TEXT_CONTEXT = "#8C8C8C"

_LIGHT_TYPE_11 = "#5A5A5A"
_LIGHT_TYPE_NON11 = "#8B6914"
_LIGHT_TYPE_10_01 = "#C62828"
_LIGHT_TEXT_NORMAL = "#2C2C2C"
_LIGHT_TEXT_DELETED = "#B71C1C"
_LIGHT_TEXT_CONTEXT = "#757575"

TYPE_CL_11 = QColor(_DARK_TYPE_11)
TYPE_CL_NON11 = QColor(_DARK_TYPE_NON11)
TYPE_CL_10_01 = QColor(_DARK_TYPE_10_01)

TEXT_CL_NORMAL = QColor(_DARK_TEXT_NORMAL)
TEXT_CL_DELETED = QColor(_DARK_TEXT_DELETED)
TEXT_CL_CONTEXT = QColor(_DARK_TEXT_CONTEXT)

_SELECTED_BG = QColor(79, 195, 247, 50)
_DIVIDER_COLOR = QColor("#B0B0B0")


def refresh_theme_colors():
    """根据当前主题刷新模块级颜色常量。"""
    from dualign.gui.theme import T as _T

    global TYPE_CL_11, TYPE_CL_NON11, TYPE_CL_10_01
    global TEXT_CL_NORMAL, TEXT_CL_DELETED, TEXT_CL_CONTEXT
    global _SELECTED_BG, _DIVIDER_COLOR
    if _T.is_dark:
        TYPE_CL_11 = QColor(_DARK_TYPE_11)
        TYPE_CL_NON11 = QColor(_DARK_TYPE_NON11)
        TYPE_CL_10_01 = QColor(_DARK_TYPE_10_01)
        TEXT_CL_NORMAL = QColor(_DARK_TEXT_NORMAL)
        TEXT_CL_DELETED = QColor(_DARK_TEXT_DELETED)
        TEXT_CL_CONTEXT = QColor(_DARK_TEXT_CONTEXT)
        _SELECTED_BG = QColor(79, 195, 247, 50)
        _DIVIDER_COLOR = QColor("#B0B0B0")
    else:
        TYPE_CL_11 = QColor(_LIGHT_TYPE_11)
        TYPE_CL_NON11 = QColor(_LIGHT_TYPE_NON11)
        TYPE_CL_10_01 = QColor(_LIGHT_TYPE_10_01)
        TEXT_CL_NORMAL = QColor(_LIGHT_TEXT_NORMAL)
        TEXT_CL_DELETED = QColor(_LIGHT_TEXT_DELETED)
        TEXT_CL_CONTEXT = QColor(_LIGHT_TEXT_CONTEXT)
        _SELECTED_BG = QColor(0, 122, 204, 40)
        _DIVIDER_COLOR = QColor("#8C8C8C")


def type_cl(init_type: str) -> QColor:
    """初始/当前类型 → 颜色。"""
    if not init_type:
        return TYPE_CL_11
    try:
        ls, lt = (int(x) for x in init_type.split(":", 1))
    except (ValueError, AttributeError):
        return TYPE_CL_11
    if ls == 0 or lt == 0:
        return TYPE_CL_10_01
    return TYPE_CL_11 if ls == 1 and lt == 1 else TYPE_CL_NON11


def marker_cl(marker: str) -> QColor:
    """操作标记 → QColor。"""
    if not marker:
        return TYPE_CL_11
    return QColor(resolve_hex_color(marker))


_ANOMALY_COLORS = {
    "NON_1TO1": "#FFB300",
    "MIX": "#AB47BC",
    "LOW_SCORE": "#FF7043",
    "FLAGGED": "#EF5350",
}

# 多异常时的确定性优先级（严重度排序）
_ANOMALY_PRIORITY = ["FLAGGED", "MIX", "NON_1TO1", "LOW_SCORE"]


def priority_anomaly_type(atypes: set) -> str | None:
    """从异常类型集合中按优先级返回最高优先级的类型。

    确保多异常时颜色选择是确定性的。
    """
    if not atypes:
        return None
    for t in _ANOMALY_PRIORITY:
        if t in atypes:
            return t
    return next(iter(atypes))  # 未知类型兜底


def anomaly_cl(atype: str) -> QColor:
    """异常类型 → 颜色。"""
    h = _ANOMALY_COLORS.get(atype)
    return QColor(h) if h else TYPE_CL_11


def compute_text_colors(
    snap_index: int,
    marker: str,
    atypes: set,
    action: any,
    snapshot: any,
) -> tuple[bool, bool]:
    """判断该 Snap 的 src/tgt 侧是否有变化，供文本列着色使用。"""
    if action is None:
        return False, False

    src_changed = False
    tgt_changed = False
    s_idx, t_idx, _sc = snapshot.original_ops[snap_index]

    if action.kind == "edit":
        d = action.data
        new_src = d.get("new_src_lines")
        new_tgt = d.get("new_tgt_lines")
        edit_side = d.get("edit_side")
        orig_src = [snapshot.src_text(i) for i in s_idx]
        orig_tgt = [snapshot.tgt_text(j) for j in t_idx]
        if new_src is not None:
            src_changed = new_src != orig_src
        if new_tgt is not None:
            tgt_changed = new_tgt != orig_tgt
        if not src_changed and not tgt_changed:
            pass
        elif edit_side == "src" and src_changed:
            tgt_changed = False
        elif edit_side == "tgt" and tgt_changed:
            src_changed = False
    elif action.kind in ("merge",):
        src_changed = True
        tgt_changed = True
    elif action.kind == "split":
        d = action.data
        new_src = d.get("new_src_lines")
        new_tgt = d.get("new_tgt_lines")
        orig_src = [snapshot.src_text(i) for i in s_idx]
        orig_tgt = [snapshot.tgt_text(j) for j in t_idx]
        src_changed = (new_src is not None) and (new_src != orig_src)
        tgt_changed = (new_tgt is not None) and (new_tgt != orig_tgt)
    elif action.kind in ("ok", "flag", "placeholder_src", "placeholder_tgt"):
        src_changed = True
        tgt_changed = True

    return src_changed, tgt_changed


def has_snap_text_changed(
    snap_index: int,
    action: any,
    snapshot: any,
) -> tuple[bool, bool]:
    """判断该 Snap 的文本内容是否与初始对齐输出不同（用于星标）。"""
    if action is None:
        return False, False

    s_idx, t_idx, _sc = snapshot.original_ops[snap_index]

    if action.kind == "edit":
        d = action.data
        new_src = d.get("new_src_lines")
        new_tgt = d.get("new_tgt_lines")
        edit_side = d.get("edit_side")
        orig_src = [snapshot.src_text(i) for i in s_idx]
        orig_tgt = [snapshot.tgt_text(j) for j in t_idx]
        src_ch = (new_src is not None) and (new_src != orig_src)
        tgt_ch = (new_tgt is not None) and (new_tgt != orig_tgt)
        if edit_side == "src" and src_ch:
            tgt_ch = False
        elif edit_side == "tgt" and tgt_ch:
            src_ch = False
        return src_ch, tgt_ch

    if action.kind == "placeholder_src":
        return True, False
    if action.kind == "placeholder_tgt":
        return False, True

    if action.kind == "split":
        d = action.data or {}
        new_src = d.get("new_src_lines")
        new_tgt = d.get("new_tgt_lines")
        orig_src = [snapshot.src_text(i) for i in s_idx]
        orig_tgt = [snapshot.tgt_text(j) for j in t_idx]
        src_ch = (new_src is not None) and (new_src != orig_src)
        tgt_ch = (new_tgt is not None) and (new_tgt != orig_tgt)
        return src_ch, tgt_ch

    if action.kind == "merge":
        return False, False
    return False, False


def text_color_for_side(
    for_src: bool,
    src_changed: bool,
    tgt_changed: bool,
    is_del: bool,
    is_ctx: bool,
    marker: str,
    atypes: set,
) -> QColor:
    """根据变化状态和 marker/异常类型返回文本前景色。"""
    if is_del:
        return TEXT_CL_DELETED
    if is_ctx:
        return TEXT_CL_CONTEXT
    changed = src_changed if for_src else tgt_changed
    if changed:
        return marker_cl(marker) if marker else TYPE_CL_NON11
    if marker:
        return anomaly_cl(priority_anomaly_type(atypes)) if atypes else TEXT_CL_NORMAL
    if atypes:
        return anomaly_cl(priority_anomaly_type(atypes))
    return TEXT_CL_NORMAL


# ═══════════════════════════════════════════════════════════════
# HighlightDelegate — 统一高亮 + 虚线分隔 + 星标
# ═══════════════════════════════════════════════════════════════


class HighlightDelegate(QStyledItemDelegate):
    """自定义委托：统一处理高亮、分隔虚线、星标、底部边框。

    底部边框（实线/虚线）完全由 paint() 绘制，QSS 不设 border-bottom。
    分隔虚线为单元格级——仅在该单元格下方有同一合并组的子行时绘制。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_rows: Set[int] = set()
        self._focused_row: Optional[int] = None
        self._divider_cells: Set[Tuple[int, int]] = set()  # {(row, col), ...}

    def set_selected_rows(self, rows: Set[int]):
        self._selected_rows = rows

    def set_focused_row(self, row: Optional[int]):
        self._focused_row = row

    def set_divider_cells(self, cells: Set[Tuple[int, int]]):
        """设置需要底部虚线的单元格集合。{(row, col), ...}"""
        self._divider_cells = cells

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        """用 QTextDocument 精确计算 cell 高度（word-wrap + CJK 安全）。

        宽度直接从表头取实际列宽，消除 option.rect.width() 与真实列宽之间的像素级偏差。
        """
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return super().sizeHint(option, index)

        col = index.column()
        # 仅 col 5/6（原文/译文）按列宽 word-wrap；其它列单行
        if col not in (5, 6):
            return super().sizeHint(option, index)

        table = self.parent()
        if table is None or not isinstance(table, QTableWidget):
            return super().sizeHint(option, index)

        # 从表头取实际列宽——消除 option.rect 与列宽之间的像素偏差
        col_w = table.columnWidth(col)
        if col_w <= 0:
            return super().sizeHint(option, index)

        # 扣除 QSS padding（基类：2px 左右，即 4）
        txt_w = col_w - 4
        if txt_w < 20:
            txt_w = col_w

        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setTextWidth(txt_w)
        doc.setPlainText(text)

        h = int(doc.size().height()) + 4  # QSS 垂直 padding: 2+2
        return QSize(col_w, h)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index,
    ):
        row = index.row()
        rect = option.rect

        bg = index.data(Qt.ItemDataRole.BackgroundRole)
        if bg is not None:
            painter.save()
            painter.fillRect(rect, bg)
            painter.restore()

        # 禁用 hover 高亮——HighlightDelegate 完全接管视觉
        opt = option
        opt.state &= ~QStyle.StateFlag.State_MouseOver

        super().paint(painter, opt, index)

        # 变化标记：★ 星标
        if index.data(CHANGED_FLAG_ROLE):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            dot_color = QColor("#FF9800")
            painter.setBrush(dot_color)
            painter.setPen(Qt.PenStyle.NoPen)
            margin = 3
            d = 7
            cx = rect.right() - margin - d // 2
            cy = rect.top() + margin + d // 2
            painter.drawEllipse(QPointF(cx, cy), d / 2, d / 2)
            painter.restore()

        # 选中高亮
        if row in self._selected_rows:
            painter.save()
            painter.fillRect(rect, _SELECTED_BG)
            painter.restore()

        # ── 底部边框（统一 1px，像素对齐消除跨表格线宽差异）──
        col = index.column()
        grid_color = QColor(T.BORDER_DIM)
        is_divider = (row, col) in self._divider_cells

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _w = 1.25
        if is_divider:
            pen = QPen(grid_color, _w, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([2, 3])
        else:
            pen = QPen(grid_color, _w, Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        painter.restore()


# ═══════════════════════════════════════════════════════════════
# 评分单元格工厂 — 统一创建评分单元格
# ═══════════════════════════════════════════════════════════════

# 三态颜色（与 score_manager.SCORE_STATE_* 对应）
_SCORE_PENDING_CL = QColor("#888888")
_SCORE_LOADING_CL = QColor("#AAAAAA")
_SCORE_FAILED_CL = QColor("#666666")


def make_score_cell(
    score: Optional[float] = None,
    detail_mode: bool = False,
    state: str = "ready",
    precision: int = 1,
) -> QTableWidgetItem:
    """创建评分单元格。

    Args:
        score: 分数值（None 时使用状态色）
        detail_mode: 明细模式（显示数字）或紧凑模式（色带）
        state: 评分状态 "pending" / "loading" / "ready" / "failed"
        precision: 百分比小数位数（默认 1 位，如 85.3%）

    Returns:
        QTableWidgetItem（不可编辑，居中对齐）
    """
    if state in ("pending", "loading", "failed"):
        clr = {
            "pending": _SCORE_PENDING_CL,
            "loading": _SCORE_LOADING_CL,
            "failed": _SCORE_FAILED_CL,
        }.get(state, _SCORE_PENDING_CL)
        if detail_mode:
            label = {"pending": "—", "loading": "…", "failed": "✗"}.get(state, "—")
            it = QTableWidgetItem(label)
            it.setForeground(clr)
        else:
            it = QTableWidgetItem("")
            it.setBackground(clr)
    else:
        # ready 状态：正常显示
        clr = score_to_color(score) if score is not None else _SCORE_PENDING_CL
        if detail_mode:
            it = QTableWidgetItem(
                f"{score:.{precision}%}" if score is not None else "—"
            )
            it.setForeground(clr)
        else:
            it = QTableWidgetItem("")
            it.setBackground(clr)
    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it


# 窄垂直滚动条样式（~8px 宽，无箭头按钮，hover 加深）
THIN_SCROLLBAR_CSS = (
    "QScrollBar:vertical {"
    "  width: 8px;"
    "  background: transparent;"
    "  margin: 0;"
    "}"
    "QScrollBar::handle:vertical {"
    "  background: palette(mid);"
    "  min-height: 20px;"
    "  border-radius: 4px;"
    "}"
    "QScrollBar::handle:vertical:hover {"
    "  background: palette(dark);"
    "}"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
    "  height: 0;"
    "}"
    "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
    "  background: transparent;"
    "}"
)


class BaseTextTable(QWidget):
    """文本对表格基类，提供跨行合并、颜色辅助和焦点管理。"""

    # ── 子类必须重写 ──
    COL_HEADERS: ClassVar[List[str]] = []
    """列标题列表，决定表格列数。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list = []
        self._last_width = 0  # 用于 resizeEvent 防抖
        self._render_spans: dict = {}  # 最近一次渲染的 span 数据
        self._render_spanned_cells: Set[Tuple[int, int]] = set()
        self._build_ui()

    # ══════════════════════════════════════════════════════════
    # UI 构建
    # ══════════════════════════════════════════════════════════

    def _build_ui(self):
        """构建 QTableWidget 及布局。

        子类可重写以添加额外控件（如详情面板）。
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.COL_HEADERS))
        self.table.setHorizontalHeaderLabels(self.COL_HEADERS)
        self._configure_table()
        layout.addWidget(self.table, 1)

    def _configure_table(self):
        """配置 QTableWidget 的通用属性。

        子类可重写以调整设置。
        """
        table = self.table
        hdr = table.horizontalHeader()
        hdr.setMinimumSectionSize(0)
        hdr.setStretchLastSection(False)
        for col in range(len(self.COL_HEADERS)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        # 末尾列默认 Stretch
        if self.COL_HEADERS:
            hdr.setSectionResizeMode(
                len(self.COL_HEADERS) - 1, QHeaderView.ResizeMode.Stretch
            )

        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.setWordWrap(True)
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        table.verticalHeader().setMinimumSectionSize(22)
        # 始终显示垂直滚动条，避免列宽因滚动条显隐抖动
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        table.itemClicked.connect(self._on_item_clicked)

        # 虚线分隔委托
        self._divider_delegate = HighlightDelegate(table)
        table.setItemDelegate(self._divider_delegate)

        # 基础样式：底部边框由 HighlightDelegate 在 paint() 中绘制，
        # 以实现分割行原文/译文列虚线效果。右侧边框由 QSS 提供（统一）。
        table.setStyleSheet(
            "QTableWidget { outline: none; }"
            "QTableWidget::item {"
            f"  border-right: 1px solid {T.BORDER_DIM};"
            "  padding: 2px;"
            "}"
            "QTableWidget::item:hover {"
            "  background: transparent;"
            "}"
            # 表头使用与表体相同的 border-right，消除纵向线偏移
            "QHeaderView::section {"
            f"  border-right: 1px solid {T.BORDER_DIM};"
            "  border-bottom: none;"
            "  border-top: none;"
            "  border-left: none;"
            "  padding: 2px;"
            "}"
            # 窄滚动条
            + THIN_SCROLLBAR_CSS
        )

    # ══════════════════════════════════════════════════════════
    # 数据加载
    # ══════════════════════════════════════════════════════════

    def set_items(self, items: list):
        """加载数据并渲染表格。"""
        self._items = items
        self._render()

    def _render(self):
        """清空并重新渲染表格。

        使用钩子方法体系，子类无需覆写整个 _render()，
        只需按需重写以下钩子：
          _pre_render()                      — 渲染前设置（如列可见性）
          _get_span_col_offset()             — 跨行合并偏移量
          _compute_table_spans()             — 计算跨行合并（已集成偏移量）
          _apply_table_spans()               — 应用跨行合并（可添加额外规则）
          _get_hidden_cur_rows()             — 被 span 覆盖的当前类型行
          _get_divider_rows()                — 虚线分隔行
          _extra_row_kwargs()                — 传递给 _render_row 的额外参数
          _post_render()                     — 渲染后收尾
        """
        table = self.table
        table.setUpdatesEnabled(False)
        table.setRowCount(len(self._items))
        table.clearSpans()
        table.clearContents()

        # 钩子 1: 渲染前
        self._pre_render()

        # 跨行合并
        spans = self._compute_table_spans()
        self._apply_table_spans(spans)
        # 缓存 span 元数据供 _adjust_row_heights 使用
        self._render_spans = spans
        self._render_spanned_cells = set()
        for (sr, col), (rs, cs) in spans.items():
            if rs > 1:
                for r in range(sr + 1, sr + rs):
                    for c in range(col, col + cs):
                        self._render_spanned_cells.add((r, c))

        # 元数据
        hidden_cur = self._get_hidden_cur_rows(spans)
        # 单元格级分隔虚线：合并 [M] 子行之间画虚线
        _divider_cells: Set[Tuple[int, int]] = set()
        for _i, _item in enumerate(self._items):
            _sub = getattr(_item, "sub", 0)
            if (
                _sub > 0
                and _i - 1 >= 0
                and getattr(self._items[_i - 1], "snap_index", -1)
                == getattr(_item, "snap_index", -2)
            ):
                _first = self._items[_i - _sub]
                if "[M]" in (getattr(_first, "marker", "") or ""):
                    _prev = _i - 1
                    for _col in (5, 6):
                        if (_prev, _col) not in self._render_spanned_cells and (
                            _prev,
                            _col,
                        ) not in self._render_spans:
                            _divider_cells.add((_prev, _col))
        self._divider_delegate.set_divider_cells(_divider_cells)

        # 渲染每行
        for i, item in enumerate(self._items):
            kwargs = self._extra_row_kwargs(i, item, hidden_cur)
            self._render_row(i, item, **kwargs)

        self._adjust_row_heights()
        self._post_render()
        table.setUpdatesEnabled(True)

    # ── 可重写钩子（默认空实现）──

    def _pre_render(self):
        """渲染前钩子：设置列可见性等。"""

    def _get_span_col_offset(self) -> int:
        """跨行合并的列偏移量。

        如果表格第 0 列不是 init_type（如 Snap 列），
        子类应返回偏移量使 span 正确对齐。
        """
        return 0

    def _get_snap_col(self) -> int | None:
        """Snap 列索引。有 Snap 列时返回列号，无时返回 None。

        基类返回 None（无 Snap 列），子类（如主对齐表）可重写返回 0。
        """
        return None

    def _compute_table_spans(self) -> dict:
        """计算跨行合并。子类可重写以改变 col_offset 或 snap_col。"""
        return compute_spans(
            self._items,
            col_offset=self._get_span_col_offset(),
            snap_col=self._get_snap_col(),
        )

    def _apply_table_spans(self, spans: dict):
        """将 spans 应用到表格。子类可重写以添加额外合并规则。"""
        for (sr, col), (rs, cs) in spans.items():
            if sr < len(self._items) and rs > 1:
                self.table.setSpan(sr, col, rs, cs)

    def _get_hidden_cur_rows(self, spans: dict) -> Set[int]:
        """返回当前类型/评分列被 span 覆盖（隐藏）的行索引。"""
        return set()

    def _get_divider_rows(self) -> Set[int]:
        """返回需要虚线分隔的行索引集合。"""
        return set()

    def _extra_row_kwargs(self, row: int, item, hidden_cur_rows: Set[int]) -> dict:
        """返回传递给 _render_row 的额外关键字参数。"""
        return {}

    def _post_render(self):
        """渲染后钩子。"""

    def _deficit_fill_row_heights(self):
        """deficit-fill 行高计算：替代 resizeRowsToContents。

        对跨行合并的 span anchor 格不返回全文高度（避免撑高 anchor 行），
        改用 deficit-fill 均摊算法：基线只取非 span 列，span 内容高度不足时
        等额追加到子行。
        """
        table = self.table
        table.setWordWrap(True)
        table.setTextElideMode(Qt.TextElideMode.ElideNone)

        font = table.font()
        col_widths = [table.columnWidth(c) for c in range(table.columnCount())]
        PAD = 4  # 垂直 padding 2+2
        spanned = self._render_spanned_cells
        spans = self._render_spans

        def _cell_px(row_i: int, col_i: int) -> int:
            if (row_i, col_i) in spanned:
                return 0
            it = table.item(row_i, col_i)
            if it is None:
                return 0
            txt = it.text() or ""
            if not txt:
                return 0
            cw = col_widths[col_i] if col_i < len(col_widths) else 80
            txt_w = cw - 4
            if txt_w < 20:
                txt_w = cw
            doc = QTextDocument()
            doc.setDefaultFont(font)
            doc.setTextWidth(txt_w)
            doc.setPlainText(txt)
            return int(doc.size().height()) + PAD

        # 收集跨行 span 的所有锚点格坐标
        anchor_coords: Set[Tuple[int, int]] = set()
        for (sr, col), (rs, _) in spans.items():
            if rs > 1:
                anchor_coords.add((sr, col))

        # 基线：仅取非 span 锚点格的原生高度
        base = [0] * len(self._items)
        for i in range(len(self._items)):
            for ci in range(table.columnCount()):
                if (i, ci) in anchor_coords or (i, ci) in spanned:
                    continue
                base[i] = max(base[i], _cell_px(i, ci))

        # 对每个跨行 span（col 1-6）做 deficit-fill
        for (sr, col), (rs, _) in sorted(spans.items()):
            if rs <= 1 or col == 0:
                continue
            span_h = _cell_px(sr, col)
            current = sum(base[sr : sr + rs])
            deficit = span_h - current
            if deficit > 0:
                extra = int(math.ceil(deficit / rs))
                for ri in range(sr, sr + rs):
                    base[ri] += extra

        for i, h in enumerate(base):
            table.setRowHeight(i, h)

    def _adjust_row_heights(self):
        """行高计算入口，委托给 deficit-fill 算法。"""
        self._deficit_fill_row_heights()

    def resizeEvent(self, event):
        """窗口/容器尺寸变化时重新计算行高。"""
        super().resizeEvent(event)
        w = event.size().width()
        if abs(w - self._last_width) > 20 and self._items:
            self._last_width = w
            QTimer.singleShot(0, self._adjust_row_heights)

    @abstractmethod
    def _render_row(self, row: int, item) -> None:
        """渲染第 row 行。

        子类必须实现此方法，使用 _set_cell 填充各列。
        item 的类型由子类的 set_items 决定。
        """

    # ══════════════════════════════════════════════════════════
    # 单元格辅助
    # ══════════════════════════════════════════════════════════

    def _set_cell(
        self,
        row: int,
        col: int,
        text: str,
        fg: Optional[str] = None,
        bg: Optional[str] = None,
        align: Optional[Qt.AlignmentFlag] = None,
        tooltip: Optional[str] = None,
    ) -> QTableWidgetItem:
        """创建并设置一个单元格。返回 QTableWidgetItem 以供额外设置。

        注意：不处理空文本替换——子行中被 span 覆盖的单元格
        值虽不会被渲染，但仍会被设为空字符串以避免干扰调试。
        """
        cell = QTableWidgetItem(text)
        if fg:
            cell.setForeground(QColor(fg))
        if bg:
            cell.setBackground(QColor(bg))
        if align is not None:
            cell.setTextAlignment(align)
        if tooltip:
            cell.setToolTip(tooltip)
        # 设置 UserRole 供焦点/highlight 查找使用
        if row < len(self._items):
            snap_i = getattr(self._items[row], "snap_index", None)
            if snap_i is not None:
                cell.setData(Qt.ItemDataRole.UserRole, snap_i)
        self.table.setItem(row, col, cell)
        return cell

    # ══════════════════════════════════════════════════════════
    # 焦点 / 滚动
    # ══════════════════════════════════════════════════════════

    def focus_snap(self, snap_i: int) -> bool:
        """高亮指定 Snap 的所有子行（含跨行合并子行），返回是否找到。

        使用 HighlightDelegate 绘制高亮，而非 QItemSelection
        （NoSelection 模式下 QItemSelection 无视觉反馈）。
        """
        matched_indices = []
        for i, item in enumerate(self._items):
            if getattr(item, "snap_index", None) == snap_i:
                matched_indices.append(i)

        if not matched_indices:
            return False

        # 通知 HighlightDelegate 选中行
        delegate = getattr(self, "_divider_delegate", None)
        if delegate is not None:
            delegate.set_selected_rows(set(matched_indices))
            delegate.set_focused_row(matched_indices[0])
            self.table.viewport().update()

        # 滚动到首行
        first = self.table.item(matched_indices[0], 0)
        if first:
            self.table.scrollToItem(
                first,
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )

        self._on_focus_snap_found(snap_i)
        return True

    def _on_focus_snap_found(self, snap_i: int):
        """找到焦点 Snap 后的额外处理。子类可重写（如显示详情面板）。"""

    def _on_item_clicked(self, item):
        """行点击 — 子类可重写以处理信号发送。"""

    @property
    def count(self) -> int:
        return len(self._items)
