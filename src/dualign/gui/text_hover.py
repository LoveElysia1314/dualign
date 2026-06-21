"""
Dualign — TextHoverPopup: 仅对星标单元格显示初始文本

不再显示"当前文本"段。"省略长文本"功能已移除，表格始终全文显示。
悬浮窗仅在单元格有 CHANGED_FLAG_ROLE（文本与初始对齐输出不同）时
弹出，仅展示初始文本，方便用户对比。

特性:
  - 无宽度/高度上限 — 始终容纳全部内容（QLabel + WordWrap + adjustSize）
  - 原文单元格左对齐，译文单元格右对齐
  - 宽度 = 文本最大行宽 + 内边距
  - 纯文本 QLabel，无 Markdown/CSS 渲染
  - 双主题适配
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QGuiApplication, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QLabel,
)

_COL_SOURCE = 5
_COL_TARGET = 6
_OFFSET_Y = 4
_MIN_WIDTH = 140
_CONTENT_MARGIN = 6


class TextHoverPopup(QFrame):
    """仅对星标单元格显示初始文本的悬浮窗。"""

    _instance: Optional["TextHoverPopup"] = None

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("TextHoverPopup")
        self._alignment_col: int = -1
        self._build_ui()
        self._apply_theme()

    @classmethod
    def _get_instance(cls, parent=None) -> "TextHoverPopup":
        if cls._instance is None:
            cls._instance = cls(parent)
        return cls._instance

    @classmethod
    def show_initial(
        cls,
        parent_widget,
        initial_text: str,
        cell_rect,
        viewport,
        *,
        column: int = 5,
    ):
        """在单元格下方弹出仅含初始文本的悬浮窗。"""
        popup = cls._get_instance(parent_widget)
        popup._alignment_col = column
        popup._set_content(initial_text, cell_width=cell_rect.width())

        cell_left = cell_rect.left()
        cell_right = cell_rect.right()
        cell_bottom = cell_rect.bottom()
        global_left = viewport.mapToGlobal(QPoint(int(cell_left), int(cell_bottom)))
        global_right = viewport.mapToGlobal(QPoint(int(cell_right), int(cell_bottom)))
        popup._position(global_left, global_right)
        popup.show()
        popup.raise_()

    @classmethod
    def hide_text(cls):
        if cls._instance is not None:
            cls._instance.hide()

    @classmethod
    def adjust_theme(cls):
        if cls._instance is not None:
            cls._instance._apply_theme()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(_CONTENT_MARGIN, 1, _CONTENT_MARGIN, 1)
        layout.setSpacing(0)

        self._initial_header = QLabel(
            "\U0001f4cb \u521d\u59cb\u6587\u672c\uff08\u539f\u59cb\u5bf9\u9f50\u8f93\u51fa\uff09"
        )
        self._initial_header.setStyleSheet(
            "font-weight:600;font-size:10px;padding:0;margin:0;"
        )
        layout.addWidget(self._initial_header)

        self._initial_label = QLabel("")
        self._initial_label.setWordWrap(True)
        self._initial_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self._initial_label)

    def _set_content(self, initial: str, cell_width: int = 0):
        self._initial_label.setText(initial)

        # 用 QTextDocument 精确计算宽度和高度（与表格 delegate 一致）
        doc = QTextDocument()
        doc.setDefaultFont(self._initial_label.font())
        doc.setPlainText(initial)

        ideal_w = int(doc.idealWidth()) + _CONTENT_MARGIN * 2
        cell_ratio_w = int(cell_width * 1.2) if cell_width > 0 else 0
        if cell_ratio_w > 0 and ideal_w > cell_ratio_w:
            # 宽度受限于单元格宽度：启用换行
            doc.setTextWidth(cell_ratio_w - _CONTENT_MARGIN * 2)
            desired_w = cell_ratio_w
        else:
            desired_w = max(ideal_w, _MIN_WIDTH)

        h = int(doc.size().height()) + 24  # header(约20px) + margins
        self.setFixedSize(desired_w, h)

    def _position(self, global_left, global_right):
        pw = self.width()
        ph = self.height()
        col = self._alignment_col

        x = global_right.x() - pw if col == _COL_TARGET else global_left.x()
        y = global_left.y() + _OFFSET_Y

        cursor_pos = global_left
        target_screen = None
        for screen in QGuiApplication.screens():
            if screen.geometry().contains(cursor_pos):
                target_screen = screen
                break
        if target_screen is None:
            target_screen = QGuiApplication.primaryScreen()

        if target_screen:
            sg = target_screen.availableGeometry()
            if x + pw > sg.right():
                x = sg.right() - pw
            if x < sg.left():
                x = sg.left()
            if y + ph > sg.bottom():
                y = cursor_pos.y() - ph - _OFFSET_Y * 3
            if y < sg.top():
                y = sg.top()

        self.move(int(x), int(y))

    def _apply_theme(self):
        self.setStyleSheet(
            "QFrame#TextHoverPopup {"
            "  background: palette(window);"
            "  border: 1px solid palette(mid);"
            "  border-radius: 4px;"
            "  padding: 0px;"
            "}"
            "QFrame#TextHoverPopup QLabel {"
            "  padding: 0px;"
            "  margin: 0px;"
            "  background: transparent;"
            "}"
        )
        self._initial_header.setStyleSheet(
            "font-weight:600;font-size:10px;padding:0;margin:0;"
        )
