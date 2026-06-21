"""
Dualign — CollapsibleSection: 通用可折叠面板节

替代 QGroupBox，点击标题栏切换内容可见性。
统一所有审校面板节的折叠行为。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QSizePolicy,
)


class CollapsibleSection(QFrame):
    """通用可折叠面板节。

    点击标题栏切换箭头 ▼/▶ 并隐藏/显示内容区。
    可选的 extra_widget 放在标题栏右侧（如按钮）。
    配色完全由 Fusion QPalette 原生管理，双主题自动适配。
    """

    def __init__(
        self,
        title: str,
        parent=None,
        *,
        collapsed: bool = False,
        extra_widget: QWidget | None = None,
    ):
        super().__init__(parent)
        self._collapsed = collapsed
        self.setObjectName("CollapsibleSection")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # ── 标题栏（始终可见，点击切换）──
        self._header = QFrame()
        self._header.setObjectName("cs_header")
        self._header.setFixedHeight(22)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        # 使用 QPalette 原生配色，hover 高亮由 palette(highlight) 驱动
        self._header.setStyleSheet(
            "QFrame#cs_header{"
            "  background: palette(button);"
            "  border: 1px solid palette(mid);"
            "  border-radius: 4px;"
            "}"
            "QFrame#cs_header:hover{"
            "  border-color: palette(highlight);"
            "}"
        )

        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(6, 0, 4, 0)
        hl.setSpacing(4)

        self._arrow = QLabel("▼" if not collapsed else "▶")
        self._arrow.setFixedWidth(14)
        self._arrow.setStyleSheet("font-size:10px;background:transparent;border:none;")
        hl.addWidget(self._arrow)

        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet(
            "font-weight:600;font-size:11px;background:transparent;border:none;"
        )
        hl.addWidget(self._title_lbl, 1)

        if extra_widget:
            hl.addWidget(extra_widget)

        self._main_layout.addWidget(self._header)

        # ── 内容区 ──
        self._content = QFrame()
        self._content.setObjectName("cs_content")
        self._content.setStyleSheet(
            "QFrame#cs_content{"
            "  border-left: 1px solid palette(mid);"
            "  border-right: 1px solid palette(mid);"
            "  border-bottom: 1px solid palette(mid);"
            "  border-radius: 0 0 4px 4px;"
            "}"
        )
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(4, 2, 4, 2)
        self._content_layout.setSpacing(1)
        self._content.setVisible(not collapsed)
        self._main_layout.addWidget(self._content)

        # ── 点击事件绑定 ──
        self._header.mousePressEvent = lambda e: self._toggle()

    @property
    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    def sizeHint(self):
        """折叠时仅报告标题栏高度，让父布局回收空间。"""
        if self._collapsed:
            h = self._header.sizeHint().height()
            m = self._main_layout.contentsMargins()
            return QSize(super().sizeHint().width(), h + m.top() + m.bottom())
        return super().sizeHint()

    def minimumSizeHint(self):
        """折叠时最小高度 = 标题栏高度。"""
        if self._collapsed:
            h = self._header.minimumSizeHint().height()
            m = self._main_layout.contentsMargins()
            return QSize(super().minimumSizeHint().width(), h + m.top() + m.bottom())
        return super().minimumSizeHint()

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._arrow.setText("▶" if self._collapsed else "▼")
        self._content.setVisible(not self._collapsed)

        # 递归 invalidate 所有祖先布局，强制空间重新分配
        self._main_layout.invalidate()
        w = self.parentWidget()
        while w:
            w.updateGeometry()
            ly = w.layout()
            if ly is not None:
                ly.invalidate()
                ly.activate()
            w = w.parentWidget()

    def set_collapsed(self, collapsed: bool):
        if collapsed != self._collapsed:
            self._toggle()

    def set_title(self, title: str):
        self._title_lbl.setText(title)

    def add_content(self, widget: QWidget, stretch: int = 0):
        self._content_layout.addWidget(widget, stretch)
