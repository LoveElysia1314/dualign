"""
Dualign — 面板管理（ActivityBar + DockPanelHelper）

合并自 activity_bar.py 与 dock_panel.py，两者都负责面板管理基础设施。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QPoint, QMimeData
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QDockWidget,
    QMenu,
    QLabel,
    QComboBox,
    QCheckBox,
    QSpinBox,
    QLineEdit,
    QScrollArea,
    QFrame,
    QSizePolicy,
)
from dualign.gui.theme import FG_SECONDARY, BLUE, BG_HOVER, BG_INPUT

# ═══════════════════════════════════════════════════════════════
# ActivityBar — VS Code 风格活动栏
# ═══════════════════════════════════════════════════════════════


class ActivityButton(QPushButton):
    """活动栏按钮 — 30×30，选中时蓝色高亮，支持拖拽到对面活动栏。"""

    def __init__(self, icon: str, tooltip: str, panel_id: str, parent=None):
        super().__init__(icon, parent)
        self._panel_id = panel_id
        self._drag_start = QPoint()
        self.setToolTip(tooltip)
        self.setFixedSize(30, 30)
        self.setCheckable(True)
        self.setStyleSheet(
            f"QPushButton{{color:{FG_SECONDARY};background:transparent;"
            f"border:none;border-radius:4px;font-size:16px;}}"
            f"QPushButton:hover{{background:{BG_HOVER};}}"
            f"QPushButton:checked{{color:{BLUE};background:{BG_INPUT};}}"
        )

    # ── 拖拽支持 ──

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.position().toPoint()
        super().mousePressEvent(e)

    def contextMenuEvent(self, event):
        """右键菜单 — 面板管理（与 dock 右键菜单一致）。"""
        parent_window = self.window()
        from PySide6.QtWidgets import QMainWindow

        if isinstance(parent_window, QMainWindow):
            dock = parent_window.findChild(QDockWidget, f"dock_{self._panel_id}")
            if dock:
                from dualign.gui.panels import DockPanelHelper

                DockPanelHelper.build_panel_context_menu(
                    dock,
                    self._panel_id,
                    parent_window,
                    getattr(parent_window, "_dock_map", {}),
                    event.globalPos(),
                )
        event.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() != Qt.MouseButton.LeftButton:
            return
        if (e.position().toPoint() - self._drag_start).manhattanLength() < 8:
            return
        drag = QDrag(self)
        drag.setMimeData(QMimeData())
        pix = self.grab()
        drag.setPixmap(
            pix.scaled(
                30,
                30,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        drag.setHotSpot(QPoint(15, 15))
        drag.exec(Qt.DropAction.MoveAction)

    @property
    def panel_id(self) -> str:
        return self._panel_id


class ActivityBar(QWidget):
    """竖直活动栏，分组管理。支持拖拽按钮到另一侧活动栏。"""

    button_toggled = Signal(str, bool)  # panel_id, checked
    button_moved_to_other_bar = Signal(str, object)  # panel_id, target_bar

    def __init__(self, parent=None):
        super().__init__(parent)
        self._btns: List[ActivityButton] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._layout.setAlignment(Qt.AlignTop)
        self.setAcceptDrops(True)
        self._drag_btn: Optional[ActivityButton] = None
        self._drag_start = QPoint()

    # ── 拖拽接收 ──

    def dragEnterEvent(self, event):
        if isinstance(event.source(), ActivityButton):
            event.acceptProposedAction()

    def dropEvent(self, event):
        btn = event.source()
        if not isinstance(btn, ActivityButton):
            return
        self.button_moved_to_other_bar.emit(btn.panel_id, self)
        event.acceptProposedAction()

    # ── 拖拽发起：支持在活动栏空白区域按下拖动（不限于按钮本身上）──

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
            for btn in self._btns:
                if btn.geometry().contains(event.position().toPoint()):
                    self._drag_btn = btn
                    break
            else:
                self._drag_btn = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_btn is not None:
            if (event.position().toPoint() - self._drag_start).manhattanLength() >= 8:
                btn = self._drag_btn
                self._drag_btn = None
                drag = QDrag(self)
                drag.setMimeData(QMimeData())
                pix = btn.grab()
                drag.setPixmap(
                    pix.scaled(
                        30,
                        30,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                drag.setHotSpot(QPoint(15, 15))
                drag.exec(Qt.DropAction.MoveAction)
                return
        super().mouseMoveEvent(event)

    def is_active(self, panel_id: str) -> bool:
        for btn in self._btns:
            if btn.panel_id == panel_id and btn.isChecked():
                return True
        return False

    @property
    def buttons(self) -> List[ActivityButton]:
        return self._btns[:]


# ═══════════════════════════════════════════════════════════════
# DockPanelHelper — 面板管理工具函数
# ═══════════════════════════════════════════════════════════════

_WIDGET_PAD: dict[type, int] = {
    QPushButton: 16,
    QLabel: 4,
    QComboBox: 10,
    QCheckBox: 4,
    QSpinBox: 8,
    QLineEdit: 8,
}
_LEAF_TYPES = (QPushButton, QLabel, QComboBox, QCheckBox, QSpinBox, QLineEdit)


class DockPanelHelper:
    """静态工具函数集合，用于面板管理操作。"""

    @staticmethod
    def _compute_min_width(widget: QWidget, padding: int = 18) -> int:
        """递归计算 widget 子树中叶子控件实际需要的最小视觉宽度。"""
        max_w = 0

        def _walk(w: QWidget):
            nonlocal max_w
            if isinstance(w, QScrollArea):
                inner = w.widget()
                if inner is not None:
                    _walk(inner)
                return
            direct = [c for c in w.children() if isinstance(c, QWidget)]
            if direct and not isinstance(w, _LEAF_TYPES):
                for child in direct:
                    _walk(child)
                return
            hint = w.minimumSizeHint()
            pw = hint.width()
            if isinstance(w, QComboBox):
                pw = max(
                    w.fontMetrics().horizontalAdvance(
                        w.itemText(0) if w.count() > 0 else ""
                    )
                    + 40,
                    80,
                )
            if pw > 0:
                pad = _WIDGET_PAD.get(type(w), 0)
                candidate = pw + pad
                if candidate > max_w:
                    max_w = candidate

        _walk(widget)
        return max(max_w + padding, 60)

    @staticmethod
    def move_to_opposite_side(dock: QDockWidget, main_window):
        """将当前面板移到对侧，与对侧已有面板标签页化。宽度锁定 360px。"""
        area = main_window.dockWidgetArea(dock)
        new_area = (
            Qt.RightDockWidgetArea
            if area == Qt.LeftDockWidgetArea
            else Qt.LeftDockWidgetArea
        )

        # 找对侧的第一个 dock 作为 tab 锚点
        dock_map = getattr(main_window, "_dock_map", {})
        target_tab = None
        for pid, d in dock_map.items():
            if d is dock:
                continue
            if main_window.dockWidgetArea(d) == new_area:
                target_tab = d
                break

        main_window.removeDockWidget(dock)
        main_window.addDockWidget(new_area, dock)
        if target_tab:
            main_window.tabifyDockWidget(target_tab, dock)
        dock.show()
        dock.setFloating(False)
        # 锁定宽度 360px
        main_window.resizeDocks([dock], [360], Qt.Orientation.Horizontal)
        return new_area

    @staticmethod
    def toggle_single_column(main_window):
        """切换标签页/单栏模式。

        单栏模式：文件管理在上、审校面板在下，用 QSplitter 纵向并排放
        入同一个 Dock 中。避免 qt splitDockWidget 的跨平台问题。
        """
        dock_map = getattr(main_window, "_dock_map", {})
        review = dock_map.get("review")
        files = dock_map.get("files")
        if not review or not files:
            return

        from PySide6.QtWidgets import QScrollArea, QSplitter

        from PySide6.QtWidgets import QTabBar

        is_active = getattr(main_window, "_single_column_active", False)
        if is_active:
            # ── 切回标签页 ──
            container = getattr(main_window, "_single_column_container", None)
            if container:
                # 先提取内部控件，防止 container.deleteLater() 级联删除原始 widget
                for i in range(container.count()):
                    scroll = container.widget(i)
                    if scroll and isinstance(scroll, QScrollArea):
                        w = scroll.takeWidget()
                        if w:
                            w.setParent(None)
                container.deleteLater()
            main_window._single_column_active = False
            main_window._single_column_container = None

            # 恢复 QTabBar 显示
            _saved_tab_bar = getattr(main_window, "_single_column_tab_bar", None)
            if _saved_tab_bar:
                try:
                    _saved_tab_bar.show()
                except RuntimeError:
                    pass
                main_window._single_column_tab_bar = None

            # 使用保存的原始引用恢复两个 dock 的 widget
            review.setWidget(main_window._review_orig_widget)
            files.setWidget(main_window._files_orig_widget)
            files.show()

            main_window.tabifyDockWidget(files, review)
            review.raise_()
        else:
            # ── 切到单栏 ──
            # 为两个原始面板包裹 QScrollArea，放入同一 QSplitter
            # 注意：保存的 _orig_widget 在其父级被删除后 Qt 会析构，
            # 所以每次都要从 dock 的 widget() 树中重新提取。
            fil_widget = main_window._files_orig_widget
            rev_widget = main_window._review_orig_widget

            fil_scroll = QScrollArea()
            fil_scroll.setWidgetResizable(True)
            fil_scroll.setFrameShape(QFrame.NoFrame)
            fil_scroll.setWidget(fil_widget)

            rev_scroll = QScrollArea()
            rev_scroll.setWidgetResizable(True)
            rev_scroll.setFrameShape(QFrame.NoFrame)
            rev_scroll.setWidget(rev_widget)

            splitter = QSplitter(Qt.Vertical)
            splitter.setObjectName("_single_column_splitter")
            splitter.addWidget(fil_scroll)
            splitter.addWidget(rev_scroll)
            splitter.setSizes([200, 300])
            # 分隔线样式：加粗、醒目
            splitter.setHandleWidth(4)
            splitter.setStyleSheet(
                "QSplitter::handle{background:palette(mid);}"
                "QSplitter::handle:hover{background:palette(highlight);}"
            )

            # 隐藏 QTabBar（左栏已无多标签需求）
            for tb in main_window.findChildren(QTabBar):
                if tb.isVisible():
                    main_window._single_column_tab_bar = tb
                    tb.hide()
                    break

            # 替换 review dock 内容为 splitter
            review.setWidget(splitter)
            review.show()

            # files dock 隐藏
            files.hide()

            main_window._single_column_container = splitter
            main_window._single_column_active = True

        main_window._debounce_save_history()

    @staticmethod
    def build_panel_context_menu(
        dock: QDockWidget,
        panel_id: str,
        main_window,
        dock_map: dict,
        pos: QPoint,
    ):
        """构建面板右键菜单。"""
        menu = QMenu(main_window)

        menu.addAction("🔄  移动到对侧").triggered.connect(
            lambda: DockPanelHelper.move_to_opposite_side(dock, main_window)
        )

        # 单栏布局开关（使用原生 QAction 的 checkable 状态）
        split_action = menu.addAction("单栏布局")
        split_action.setCheckable(True)
        split_action.setChecked(getattr(main_window, "_single_column_active", False))
        split_action.setToolTip("单栏时文管在上、审校在下，2:3 比例")
        split_action.triggered.connect(
            lambda checked: DockPanelHelper.toggle_single_column(main_window)
        )

        menu.addAction("✖  关闭").triggered.connect(dock.close)
        menu.addSeparator()
        menu.addAction("🔄  重置布局").triggered.connect(
            getattr(main_window, "_on_reset_layout", lambda: None)
        )

        menu.exec(pos)

    # REFACTOR: _safe_dock 已移除（仅 pass 的预留方法，vulture 死代码检测）


# ═══════════════════════════════════════════════════════════════
# SnapIndicator — 导航组（章节 + 文本对）
# ═══════════════════════════════════════════════════════════════


class SnapIndicator(QWidget):
    """四按钮导航组件：◀◀上一章 ◀上一条 下一条▶ 下一章▶▶"""

    # 章节导航
    prev_chapter = Signal()
    next_chapter = Signal()
    # 文本对导航
    go_prev = Signal()
    go_next = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._prev_chapter_btn = QPushButton("◀◀ 上一章")
        self._prev_chapter_btn.clicked.connect(self.prev_chapter.emit)
        layout.addWidget(self._prev_chapter_btn)

        self._prev_btn = QPushButton("◀ 上一条")
        self._prev_btn.clicked.connect(self.go_prev.emit)
        layout.addWidget(self._prev_btn)

        self._next_btn = QPushButton("下一条 ▶")
        self._next_btn.clicked.connect(self.go_next.emit)
        layout.addWidget(self._next_btn)

        self._next_chapter_btn = QPushButton("下一章 ▶▶")
        self._next_chapter_btn.clicked.connect(self.next_chapter.emit)
        layout.addWidget(self._next_chapter_btn)

    def set_enabled(self, has_prev: bool, has_next: bool):
        self._prev_btn.setEnabled(has_prev)
        self._next_btn.setEnabled(has_next)

    def set_preview_mode(self, active: bool):
        """预览模式：仅禁用文本对导航（上一条/下一条），章节导航保持可用。"""
        enabled = not active
        self._prev_btn.setEnabled(enabled)
        self._next_btn.setEnabled(enabled)
