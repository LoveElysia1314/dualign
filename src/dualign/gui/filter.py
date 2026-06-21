"""
Dualign — 筛选面板

两组独立筛选，并行展示：
  - 异常类型: 非1:1, 语言杂糅, 低分, 标记待查
  - 处理状态: 未处理, 自动修复, AI 审校, 用户审校

同组内 OR，跨组 AND，空组=全部通过。
"""

from __future__ import annotations

from typing import Optional
from dataclasses import dataclass

from PySide6.QtCore import Signal, QSize, QRectF
from PySide6.QtGui import QLinearGradient, QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QGroupBox,
    QCheckBox,
    QPushButton,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QComboBox,
    QSizePolicy,
)

from dualign.gui.theme import FG_SECONDARY, BORDER_DIM, disabled_fg
from dualign.gui.base_table import _ANOMALY_COLORS

# ═══════════════════════════════════════════════════════════════
# SnapFilter — 筛选条件数据类
# ═══════════════════════════════════════════════════════════════


@dataclass
class SnapFilter:
    """筛选条件。

    同组 OR / AND 由 cross_group_op 控制。空组 = 不过滤（该维度全通）。
    """

    # 异常类型（勾选的项目用 OR 组合）
    NON_1TO1: Optional[bool] = None
    MIX: Optional[bool] = None
    LOW_SCORE: Optional[bool] = None
    FLAGGED: Optional[bool] = None

    # 处理状态
    none: Optional[bool] = None
    auto: Optional[bool] = None
    agent: Optional[bool] = None
    user: Optional[bool] = None

    # 跨组逻辑
    cross_group_op: str = "AND"  # "AND" | "OR"


# ── 组定义 ──
_ORIGIN_LABELS = {
    "NON_1TO1": "非1:1",
    "MIX": "语言杂糅",
    "LOW_SCORE": "低分",
    "FLAGGED": "标记待查",
}

_STATE_LABELS = {
    "none": "未处理",
    "auto": "自动修复",
    "agent": "AI 审校",
    "user": "用户审校",
}

# ═══════════════════════════════════════════════════════════════
# ScoreGradientBar — 连续渐变分数图例
# ═══════════════════════════════════════════════════════════════


class ScoreGradientBar(QWidget):
    """用 QPainter 绘制从红→黄→绿的连续渐变色带 + 刻度标签。

    高度固定 24px，宽度自适应父容器。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(24)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        padding = 4
        w = self.width()
        bar_h = 10
        bar_y = 2
        label_y = bar_y + bar_h + 2

        bar_rect = QRectF(padding, bar_y, w - padding, bar_h)
        grad = QLinearGradient(padding, 0, w, 0)
        grad.setColorAt(0.0, QColor(220, 20, 50))
        grad.setColorAt(0.5, QColor(220, 220, 50))
        grad.setColorAt(1.0, QColor(30, 200, 50))

        painter.setPen(QPen(QColor(BORDER_DIM), 1))
        painter.setBrush(grad)
        painter.drawRoundedRect(bar_rect, 2, 2)

        font = QFont()
        font.setPixelSize(9)
        painter.setFont(font)
        painter.setPen(QColor(FG_SECONDARY))

        labels = [("0%", 0.0), ("50%", 0.5), ("100%", 1.0)]
        for text, ratio in labels:
            x = int(w * ratio)
            if ratio == 0.0:
                x = 4
            elif ratio == 1.0:
                x = w - painter.fontMetrics().horizontalAdvance(text) - 4
            else:
                x = x - painter.fontMetrics().horizontalAdvance(text) // 2
            painter.drawText(x, label_y + 9, text)

        painter.end()

    def minimumSizeHint(self):
        return QSize(60, 24)


class FilterPanel(QWidget):
    """筛选面板，内嵌于审校面板。

    两组独立筛选，并行展示：
      - 异常类型: 非1:1, 语言杂糅, 低分, 标记待查
      - 处理状态: 未处理, 自动修复, AI 审校, 用户审校

    同组 OR，跨组 AND。未勾选的组=全部通过。
    """

    filter_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._origin_checks: dict[str, QCheckBox] = {}
        self._state_checks: dict[str, QCheckBox] = {}
        self._build_ui()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    # ── 构建 UI ──

    @staticmethod
    def _disabled_cb(text: str) -> QCheckBox:
        """创建带禁用灰显样式的 QCheckBox。"""
        cb = QCheckBox(text)
        cb.setStyleSheet(f"QCheckBox:disabled{{color:{disabled_fg()};}}")
        return cb

    def _build_ui(self):
        # 先初始化 checkbox 字典，再构建 UI
        self._init_origin_checks()
        self._init_state_checks()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # 显示选项
        display_group = QGroupBox("显示选项")
        self._display_group = display_group
        display_layout = QVBoxLayout(display_group)
        display_layout.setContentsMargins(4, 4, 4, 4)
        display_layout.setSpacing(3)
        self._build_display_opts(display_layout)
        layout.addWidget(display_group)

        # ── 筛选组（4 列网格）—— 预览模式禁用
        filter_group = QGroupBox("筛选")
        self._filter_group = filter_group
        filter_layout = QVBoxLayout(filter_group)
        filter_layout.setContentsMargins(4, 4, 2, 2)
        filter_layout.setSpacing(2)

        # ── 异常类型（header 行 4 列等距网格）──
        origin_grid0 = QGridLayout()
        origin_grid0.setSpacing(2)
        origin_label = QLabel("▾ 异常类型")
        origin_label.setStyleSheet("font-weight:600;font-size:11px;")
        origin_grid0.addWidget(origin_label, 0, 0)
        self._sel_origin_toggle = self._disabled_cb("全选")
        self._sel_origin_toggle.clicked.connect(self._on_toggle_origin)
        origin_grid0.addWidget(self._sel_origin_toggle, 0, 2)
        self._origin_invert_btn = QPushButton("反选")
        self._origin_invert_btn.clicked.connect(self._on_invert_origin)
        origin_grid0.addWidget(self._origin_invert_btn, 0, 3)
        for _ci in range(4):
            origin_grid0.setColumnStretch(_ci, 1)
        filter_layout.addLayout(origin_grid0)

        # 异常类型 checkbox（4 列，与 header 等距对齐）
        origin_grid = QGridLayout()
        origin_grid.setSpacing(2)
        origin_list = list(self._origin_checks.items())
        for ci, (k, cb) in enumerate(origin_list):
            origin_grid.addWidget(cb, 0, ci)
        for _ci in range(4):
            origin_grid.setColumnStretch(_ci, 1)
        filter_layout.addLayout(origin_grid)

        # 跨组逻辑 + 检测依据（4 列网格）
        op_grid = QGridLayout()
        op_grid.setSpacing(2)
        op_grid.addWidget(QLabel("组间筛选逻辑："), 0, 0)
        self._cross_group_combo = QComboBox()
        self._cross_group_combo.addItems(["交集", "并集"])
        self._cross_group_combo.setCurrentText("交集")
        self._cross_group_combo.setFixedWidth(80)
        self._cross_group_combo.currentTextChanged.connect(self._on_filter_changed)
        op_grid.addWidget(self._cross_group_combo, 0, 1)
        op_grid.addWidget(QLabel("检测依据："), 0, 2)
        self._ref_mode_combo = QComboBox()
        self._ref_mode_combo.addItems(["初始文本", "当前文本"])
        self._ref_mode_combo.setCurrentText("初始文本")
        self._ref_mode_combo.setFixedWidth(80)
        self._ref_mode_combo.currentTextChanged.connect(self._on_filter_changed)
        op_grid.addWidget(self._ref_mode_combo, 0, 3)
        filter_layout.addLayout(op_grid)

        # ── 处理状态（header 行 4 列等距网格）──
        state_grid0 = QGridLayout()
        state_grid0.setSpacing(2)
        state_label = QLabel("▾ 处理状态")
        state_label.setStyleSheet("font-weight:600;font-size:11px;")
        state_grid0.addWidget(state_label, 0, 0)
        self._sel_state_toggle = self._disabled_cb("全选")
        self._sel_state_toggle.clicked.connect(self._on_toggle_state)
        state_grid0.addWidget(self._sel_state_toggle, 0, 2)
        self._state_invert_btn = QPushButton("反选")
        self._state_invert_btn.clicked.connect(self._on_invert_state)
        state_grid0.addWidget(self._state_invert_btn, 0, 3)
        for _ci in range(4):
            state_grid0.setColumnStretch(_ci, 1)
        filter_layout.addLayout(state_grid0)

        state_grid = QGridLayout()
        state_grid.setSpacing(2)
        state_list = list(self._state_checks.items())
        for ci, (k, cb) in enumerate(state_list):
            state_grid.addWidget(cb, 0, ci)
        filter_layout.addLayout(state_grid)

        layout.addWidget(filter_group)
        self._sync_all_toggles()

    def _build_display_opts(self, parent_layout):
        """显示选项：一行三格等距。
        [评分明细] [仅显示异常文本对] [上下文 N 行]
        """
        dg = QGridLayout()
        dg.setSpacing(2)

        # col 0: 评分明细
        self._show_scores_cb = self._disabled_cb("显示评分明细")
        self._show_scores_cb.setChecked(False)
        self._show_scores_cb.stateChanged.connect(lambda _: self._on_filter_changed())
        dg.addWidget(self._show_scores_cb, 0, 0)

        # col 1: 仅显示异常文本对
        self._anomaly_only_cb = self._disabled_cb("仅显示异常文本对")
        self._anomaly_only_cb.setChecked(False)
        self._anomaly_only_cb.stateChanged.connect(self._on_anomaly_only_changed)
        dg.addWidget(self._anomaly_only_cb, 0, 1)

        # col 2: 上下文控件
        self._context_label = QLabel("上下文")
        self._context_spin = QSpinBox()
        self._context_spin.setRange(0, 10)
        self._context_spin.setValue(1)
        self._context_spin.valueChanged.connect(lambda _: self._on_filter_changed())
        self._context_label_spacer = QLabel("行")
        ctx_row = QHBoxLayout()
        ctx_row.setSpacing(2)
        ctx_row.addWidget(self._context_label)
        ctx_row.addWidget(self._context_spin)
        ctx_row.addWidget(self._context_label_spacer)
        ctx_row.addStretch()
        dg.addLayout(ctx_row, 0, 2)

        for _ci in range(3):
            dg.setColumnStretch(_ci, 1)
        parent_layout.addLayout(dg)

        # 初始同步禁用状态（异常对未勾选时上下文控件灰显）
        self._sync_anomaly_only_controls()

    def _init_origin_checks(self):
        """创建异常类型 checkbox（带对应颜色 + 禁用灰显）。"""
        self._origin_checks = {}
        for key, label in _ORIGIN_LABELS.items():
            cb = QCheckBox(label)
            cb.setChecked(True)
            color = _ANOMALY_COLORS.get(key)
            if color:
                cb.setStyleSheet(
                    f"QCheckBox{{color:{color};}}QCheckBox:disabled{{color:{disabled_fg()};}}"
                )
            else:
                cb.setStyleSheet(f"QCheckBox:disabled{{color:{disabled_fg()};}}")
            cb.stateChanged.connect(lambda _s, k=key: self._on_individual_changed(k))
            self._origin_checks[key] = cb

    def _init_state_checks(self):
        """创建处理状态 checkbox（禁用灰显）。"""
        self._state_checks = {}
        for key, label in _STATE_LABELS.items():
            cb = self._disabled_cb(label)
            cb.setChecked(True)
            cb.stateChanged.connect(lambda _s, k=key: self._on_individual_changed(k))
            self._state_checks[key] = cb

    # ── 公开属性 ──

    @property
    def active_origin_keys(self) -> set[str]:
        """已勾选的异常类型 key 集合。"""
        return {k for k, cb in self._origin_checks.items() if cb.isChecked()}

    @property
    def active_state_keys(self) -> set[str]:
        """已勾选的处理状态 key 集合。"""
        return {k for k, cb in self._state_checks.items() if cb.isChecked()}

    @property
    def snap_filter(self) -> SnapFilter:
        """当前筛选条件的 SnapFilter 对象。"""
        _op_text = self._cross_group_combo.currentText()
        sf = SnapFilter(cross_group_op="AND" if _op_text == "交集" else "OR")
        for k in self.active_origin_keys:
            setattr(sf, k, True)
        for k in self.active_state_keys:
            setattr(sf, k, True)
        return sf

    @property
    def show_all(self) -> bool:
        return not self._anomaly_only_cb.isChecked()

    @property
    def context_lines(self) -> int:
        return self._context_spin.value()

    @property
    def show_scores(self) -> bool:
        return self._show_scores_cb.isChecked()

    # ── 信号 ──

    def _on_filter_changed(self):
        self.filter_changed.emit()

    def _on_anomaly_only_changed(self):
        """仅显示异常文本对勾选/取消时禁用上下文控件。"""
        self._sync_anomaly_only_controls()
        self._on_filter_changed()

    def _sync_anomaly_only_controls(self):
        """异常对未勾选（显示全部）→ 上下文整行控件+标签灰显。"""
        enabled = self._anomaly_only_cb.isChecked()
        for w in (
            self._context_label,
            self._context_spin,
            self._context_label_spacer,
        ):
            w.setEnabled(enabled)

    def _on_individual_changed(self, key: str):
        """单个 checkbox 变化 → 同步三态 + 触发筛选。"""
        self._sync_all_toggles()
        self.filter_changed.emit()

    def _sync_all_toggles(self):
        """同步两组「全选/取消全选」按钮文本。"""
        self._refresh_toggle_text(self._origin_checks, self._sel_origin_toggle)
        self._refresh_toggle_text(self._state_checks, self._sel_state_toggle)

    @staticmethod
    def _refresh_toggle_text(checks: dict, toggle: QCheckBox):
        n_checked = sum(1 for cb in checks.values() if cb.isChecked())
        n_total = len(checks)
        all_checked = n_checked == n_total
        toggle.blockSignals(True)
        toggle.setChecked(all_checked)
        toggle.setText("取消全选" if all_checked else "全选")
        toggle.blockSignals(False)

    def _on_toggle_origin(self):
        """全选/取消全选异常类型。"""
        n_checked = sum(1 for cb in self._origin_checks.values() if cb.isChecked())
        n_total = len(self._origin_checks)
        should_check = n_checked < n_total
        for cb in self._origin_checks.values():
            cb.blockSignals(True)
            cb.setChecked(should_check)
            cb.blockSignals(False)
        self._sync_all_toggles()
        self.filter_changed.emit()

    def _on_invert_origin(self):
        """反选异常类型。"""
        for cb in self._origin_checks.values():
            cb.blockSignals(True)
            cb.setChecked(not cb.isChecked())
            cb.blockSignals(False)
        self._sync_all_toggles()
        self.filter_changed.emit()

    def _on_toggle_state(self):
        """全选/取消全选处理状态。"""
        n_checked = sum(1 for cb in self._state_checks.values() if cb.isChecked())
        n_total = len(self._state_checks)
        should_check = n_checked < n_total
        for cb in self._state_checks.values():
            cb.blockSignals(True)
            cb.setChecked(should_check)
            cb.blockSignals(False)
        self._sync_all_toggles()
        self.filter_changed.emit()

    def _on_invert_state(self):
        """反选处理状态。"""
        for cb in self._state_checks.values():
            cb.blockSignals(True)
            cb.setChecked(not cb.isChecked())
            cb.blockSignals(False)
        self._sync_all_toggles()
        self.filter_changed.emit()

    def set_show_handled(self, checked: bool):
        """设置「显示已处理」状态。

        该 checkbox 在 ReviewController 的 AI 建议操作区，
        此处通过委托调用实现记忆恢复。如果没有绑定外部 checkbox 则跳过。
        """
        if hasattr(self, "_ext_handled_cb") and self._ext_handled_cb is not None:
            self._ext_handled_cb.blockSignals(True)
            self._ext_handled_cb.setChecked(checked)
            self._ext_handled_cb.blockSignals(False)

    def bind_handled_checkbox(self, cb):
        """绑定外部的「显示已处理」checkbox（在 ReviewController 中）。"""
        self._ext_handled_cb = cb

    @property
    def show_handled(self) -> bool:
        if hasattr(self, "_ext_handled_cb") and self._ext_handled_cb is not None:
            return self._ext_handled_cb.isChecked()
        return True

    @property
    def ref_current(self) -> bool:
        """True = 基于当前文本状态（而非初始对齐状态）检测异常。"""
        return self._ref_mode_combo.currentText() == "当前文本"
