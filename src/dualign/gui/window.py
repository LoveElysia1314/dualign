"""
DualignWindow: 主窗口

布局:
  ┌─ QDock(左, 原生 QTabBar) ┬── QSplitter(Vertical) ────┐
  │  [审校面板]               │  QStackedWidget           │
  │   └─ 运行日志(可折叠)     │  (欢迎页/文本对表格)       │
  │  [文件管理]               │  ──────────────────────   │
  │  (原生标签切换)           │  AI 建议列表 (7列)        │
  ├───────────────────────────┴───────────────────────────┤
  │  状态栏 (Ollama/Model/AI 指示灯 + 状态文本)            │
  └───────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from collections import deque
from typing import List, Optional, Dict, Any, Set

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QHeaderView,
    QMessageBox,
    QAbstractItemView,
    QDockWidget,
    QTabWidget,
    QStackedWidget,
    QSplitter,
    QFrame,
    QCheckBox,
    QSizePolicy,
)

from dualign.core import AlignConfig
from dualign.models.state import AlignmentSnapshot
from dualign.services.repair import (
    RepairState,
)
from dualign.gui.base_table import (
    HighlightDelegate,
)
from dualign.gui.focus import FocusManager
from dualign.gui.review import ReviewController
from dualign.gui.workspace import WorkspacePanel
from dualign.gui.panels import DockPanelHelper
from dualign.gui.welcome import WelcomePage
from dualign.gui.status_bar import StatusBar, StatusDot
from dualign.gui.window_actions import WindowActionsMixin
from dualign.gui.window_table import WindowTableMixin
from dualign.gui.settings import (
    DualignConfig,
    KEY_STRATEGY,
    KEY_SHOW_ALL,
    KEY_CONTEXT_LINES,
    KEY_COMPACT_GRID,
    KEY_ANOMALY_TYPES,
    KEY_APPROVAL_STATES,
    KEY_LAST_OPEN_DIR,
    KEY_SHOW_HANDLED,
    KEY_CROSS_GROUP_OP,
    KEY_QUALITY_GATE,
)

# 底部面板：展开最小总高度（含标题栏 24px），低于此值自动折叠


def _vline() -> QFrame:
    """垂直分隔线。"""
    s = QFrame()
    s.setFrameShape(QFrame.Shape.VLine)
    s.setFixedWidth(1)
    s.setStyleSheet("border:none;background:palette(mid);")
    s.setFixedHeight(16)
    return s


def _load_quality_config():
    """从配置中加载 QualityGateConfig。"""
    from dualign.services.quality_gate import QualityGateConfig

    cfg_data = DualignConfig.instance().get(KEY_QUALITY_GATE, None)
    if cfg_data and isinstance(cfg_data, dict):
        return QualityGateConfig(
            anchor_density_min=cfg_data.get("anchor_density_min", 0.60),
            gap_row_ratio_max=cfg_data.get("gap_row_ratio_max", 0.10),
            zscore_k=cfg_data.get("zscore_k", 3.0),
            zscore_min_score=cfg_data.get("zscore_min_score", 0.6),
        )
    return QualityGateConfig()


# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

COLUMN_HEADERS = [
    "Snap",
    "初始类型",
    "初始评分",
    "当前状态",
    "当前评分",
    "原文",
    "译文",
]

# 各列拖拽最小宽度（px），低于此值自动回弹
_COL_MIN_WIDTHS = {0: 40, 1: 60, 2: 60, 3: 60, 4: 60}

# 底部面板吸附比例（折叠后展开到 25%，拖拽可到 25%/30%/35%/40%/45%/50%，5% 粒度）
_BOTTOM_RATIOS = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
_COLLAPSE_RATIO = 0.15  # 展开→折叠: 底栏低于总高此比例则折叠
_EXPAND_RATIO = 0.22  # 折叠→展开: 底栏高于此比例则展开至 25%
_RATIO_HYSTERESIS = 0.02  # 档位切换滞回死区 (±2%)


# ═══════════════════════════════════════════════════════════════
# DualignWindow
# ═══════════════════════════════════════════════════════════════
class DualignWindow(QMainWindow, WindowActionsMixin, WindowTableMixin):
    """Dualign 主窗口。

    数据:
      _repair_state: RepairState          — 唯一数据源
      _alignment_snapshot: AlignmentSnapshot
      _anomalies: list[dict]              — 当前异常列表
      _row_op_map: dict[int, int]         — table_row → snap_index
      _undo_stack: list[RepairState]      — 撤销栈
      _redo_stack: list[RepairState]      — 恢复栈
    """

    # ── 初始化 ──

    def __init__(self, parent=None, file_entries=None):
        super().__init__(parent)
        from dualign import __version__

        self.setWindowTitle(f"Dualign Studio v{__version__}")

        # ── 设置窗口图标（确保任务栏也显示）──
        from dualign.resources import load_app_icon

        _icon = load_app_icon()
        if _icon is not None:
            self.setWindowIcon(_icon)
        # 初始尺寸（_on_first_show 中按 dock 布局调整）
        self.resize(1280, 720)
        # 构建完成前隐藏，避免启动时闪现未就绪窗口
        self.setVisible(False)
        QTimer.singleShot(0, self._on_first_show)

        # ── 数据成员 ──
        self._repair_state: Optional[RepairState] = None
        self._alignment_snapshot: Optional[AlignmentSnapshot] = None
        self._anomalies: List[dict] = []
        self._row_op_map: Dict[int, int] = {}
        self._undo_stack: deque = deque(maxlen=50)
        self._redo_stack: deque = deque(maxlen=50)
        self._undo_snap_save: Optional[int] = None
        self._current_entry: Any = None
        self._current_entry_id: str = ""
        self._entries: Optional[List[Any]] = file_entries
        self._repaired_dir: str = ""
        self._align_config = AlignConfig()
        self._strategy: str = "src"
        self._last_open_dir: str = ""
        self._sel_updating: bool = False
        self._rubber_origin_snap: Optional[int] = None
        self._rubber_active: bool = False
        self._ai_focus_lost: bool = False
        self._auto_select_on_render: Optional[int] = None
        self._preview_active: bool = False
        self._quality_config = _load_quality_config()
        self._last_quality_assessment: Optional[dict] = None
        self._dock_state_restored: bool = False

        # ── 持久化评分缓存 {f"{snap_index}_{sub}": score} ──
        self._score_cache: dict[str, float] = {}

        # ── 统一评分管理器 ──
        from dualign.services.score_manager import ScoreManager

        self._score_mgr = ScoreManager(self)
        self._score_mgr.score_updated.connect(self._on_score_updated)

        self._score_mgr.status_changed.connect(self._on_score_status_changed)

        self._focus = FocusManager()
        self._focus.snap_focused.connect(self._on_snap_focused)
        self._focus.selection_changed.connect(self._on_selection_changed)

        self._review = ReviewController()
        self._review.set_window(self)
        self._review.action_requested.connect(self._apply_ai_action)
        self._saved_dock_widths: Dict[str, int] = {"review": 360, "files": 290}

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(500)
        self._hover_timer.timeout.connect(self._on_hover_show)
        self._hovered_row: int = -1
        self._hovered_col: int = -1
        self._hovered_snap: int = -1
        self._hover_is_ai = False
        self._hovered_pos: Optional[QPoint] = None

        self._bottom_collapsed: bool = False  # 底部面板折叠状态
        self._bottom_locked: bool = False  # 欢迎页时锁定
        self._bottom_snap_idx: int = (
            3  # 当前吸附档位索引 (25%/30%/35%/40%/45%/50%，默认 40%)
        )
        self._preview_saved_bottom: Optional[tuple] = None  # 预览模式底栏状态快照
        self._bottom_bar: Optional[QWidget] = None  # 底部状态栏

        self._welcome: Optional[WelcomePage] = None
        self._stacked: Optional[QStackedWidget] = None
        self._status_bar: Optional[StatusBar] = None
        self._header_dots: dict[str, StatusDot] = {}
        self._status_bar: Optional[StatusBar] = None
        self._has_data: bool = False
        self._single_column_active: bool = False
        self._single_column_container = None
        self._model = None
        self._enc_thread = None
        self._worker = None
        self._load_op_id: int = 0
        self._current_load_op_id: int = 0
        self.src_lines: List[str] = []
        self.tgt_lines: List[str] = []
        self.src_emb = None
        self.tgt_emb = None
        self._src_hash: str = ""
        self._tgt_hash: str = ""
        self._scorer = None
        self._last_table_width: int = 0  # 用于 resizeEvent 检测表格宽度变化

        self._build_ui()
        self._setup_menu()

        # 若提供了 file_entries，在 UI 构建完成后再加载
        if file_entries is not None:
            self.load_from_provider(file_entries)

    # ═══════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════
    # eventFilter — 显式覆盖以规避 MRO 问题
    # QObject.eventFilter 在 MRO 中排在 WindowTableMixin 前面，
    # 导致 installEventFilter(self) 实际调用 QObject 的空实现。
    # ═══════════════════════════════════════════════════════════

    def eventFilter(self, obj, event):
        return WindowTableMixin.eventFilter(self, obj, event)

    # ═══════════════════════════════════════════════════════════
    # resizeEvent — 窗口大小变化时重算表格行高
    # ═══════════════════════════════════════════════════════════

    def resizeEvent(self, event):
        """窗口大小变化时，检测表格宽度变化并重算行高。

        在 QMainWindow.resizeEvent 提供的基本布局基础上，增加
        对表格宽度变化的监测。stretch 列宽变化不一定总能触发
        sectionResized（如最大化/还原），此方法兜底。
        """
        super().resizeEvent(event)
        if not hasattr(self, "table") or self.table is None:
            return
        tw = self.table.width()
        if abs(tw - self._last_table_width) > 20 and self.table.rowCount() > 0:
            self._last_table_width = tw
            # 复用已有的防抖定时器
            if not hasattr(self, "_row_resize_timer"):
                self._row_resize_timer = QTimer(self)
                self._row_resize_timer.setSingleShot(True)
                self._row_resize_timer.setInterval(80)
                self._row_resize_timer.timeout.connect(self._recalc_row_heights)
            self._row_resize_timer.start()

    # ═══════════════════════════════════════════════════════════
    # _on_first_show — 首次显示：复原尺寸 → 居中定位 → 后处理
    # ═══════════════════════════════════════════════════════════

    def _on_first_show(self):
        """首次显示：窗口尺寸 → 居中定位 → 后处理 → 设为可见。"""
        self._update_min_width()
        self._center_window()
        if not getattr(self, "_dock_state_restored", False):
            self._set_initial_dock_sizes()
        self._ensure_on_screen()
        # 首次启动后处理（一次性）
        self._finish_first_show()

    def _finish_first_show(self):
        """一次性后处理：设为可见 + 初始化定时器 + 连接主题信号。"""
        self.setVisible(True)
        self.raise_()
        from dualign.gui.base_table import refresh_theme_colors

        refresh_theme_colors()
        self._setup_dock_tab_bar_context_menu()
        QTimer.singleShot(200, self._update_dock_title_bars)
        QTimer.singleShot(800, self._refresh_status_dots)
        from dualign.gui.theme import T as _themeT

        _themeT.theme_changed.connect(self._on_theme_changed)
        _themeT.theme_changed.connect(self._on_hover_theme_changed)
        _themeT.theme_changed.connect(lambda _: refresh_theme_colors())

    def _update_min_width(self):
        """根据当前 dock 布局同步窗口最小宽度与尺寸。

        单侧 dock = 1280，双侧（右侧有 dock）= 1440。
        扩宽时保持左边界不变；缩小时仅降低下限，不自动回缩。
        """
        _has_right = (
            any(
                self.dockWidgetArea(d) == Qt.RightDockWidgetArea
                for d in self._dock_map.values()
            )
            if hasattr(self, "_dock_map")
            else False
        )
        _w = 1440 if _has_right else 1280
        self.setMinimumSize(_w, 720)
        # 窗口太窄（1440 模式）→ 自动扩宽，保持左边界
        if _has_right and self.width() < _w:
            _geom = self.geometry()
            self.setGeometry(_geom.x(), _geom.y(), _w, _geom.height())
            # 检查是否超出屏幕右边界
            self._ensure_on_screen()

    def _center_window(self):
        """将窗口居中于鼠标所在屏幕。"""
        from PySide6.QtGui import QGuiApplication, QCursor

        cursor_pos = QCursor.pos()
        target = QGuiApplication.primaryScreen()
        for screen in QGuiApplication.screens():
            if screen.geometry().contains(cursor_pos):
                target = screen
                break
        sg = target.availableGeometry()
        w, h = self.width(), self.height()
        w = min(w, sg.width())
        h = min(h, sg.height())
        x = sg.x() + (sg.width() - w) // 2
        y = sg.y() + (sg.height() - h) // 2
        self.move(x, y)

    def _ensure_on_screen(self):
        """确保窗口不超出屏幕右/下边界，必要时左移或上移。"""
        from PySide6.QtGui import QGuiApplication, QCursor

        cursor_pos = QCursor.pos()
        target = QGuiApplication.primaryScreen()
        for screen in QGuiApplication.screens():
            if screen.geometry().contains(cursor_pos):
                target = screen
                break
        sg = target.availableGeometry()
        _geom = self.geometry()
        _x, _y = _geom.x(), _geom.y()
        _x = min(_x, sg.right() - _geom.width())
        _y = min(_y, sg.bottom() - _geom.height())
        _x = max(_x, sg.left())
        _y = max(_y, sg.top())
        if (_x, _y) != (_geom.x(), _geom.y()):
            self.move(_x, _y)

        # ── 延迟恢复旧版拆分布局偏好（无 dock_state 时回退）──
        deferred = getattr(self, "_restore_layout_deferred", None)
        if deferred:
            self._restore_layout(deferred)
            self._restore_layout_deferred = None

    def _build_ui(self):
        from dualign.gui.theme import T

        # ══════════════════════════════════════════════════════
        # 中央：仅表格（面板切换由原生 QTabBar 处理）
        # ══════════════════════════════════════════════════════
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(COLUMN_HEADERS)
        hdr = self.table.horizontalHeader()
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hdr.setMinimumSectionSize(0)
        hdr.setStretchLastSection(False)
        # col 0-4: Fixed（不支持用户拖拽）
        from dualign.gui.base_table import calc_snap_width

        _snap_w = calc_snap_width(0)
        for ci in range(5):
            hdr.setSectionResizeMode(ci, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(0, _snap_w)
        hdr.resizeSection(1, 64)
        hdr.resizeSection(2, 60)
        hdr.resizeSection(3, 64)
        hdr.resizeSection(4, 60)
        # 原文/译文均分剩余空间
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        # 列宽变化时重算行高（word-wrap 换行数可能变化）
        hdr.sectionResized.connect(self._on_text_col_resized)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # 自己管理选中集，只用 Qt 样式渲染（NoSelection + 自定义高亮）
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.setShowGrid(False)
        # 始终显示垂直滚动条，避免列宽因滚动条显隐抖动；横向已关
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        # 启用鼠标跟踪以支持悬停弹出
        self.table.viewport().setMouseTracking(True)
        self.table.verticalHeader().setVisible(False)  # 隐藏原生行号（Snap 列替代）
        from dualign.gui.base_table import THIN_SCROLLBAR_CSS

        self.table.setStyleSheet(
            "QTableWidget { outline: none; }"
            f"QTableWidget::item {{"
            f"  border-right: 1px solid {T.BORDER_DIM};"
            f"  padding: 2px;"
            f"}}"
            "QTableWidget::item:hover {"
            "  background: transparent;"
            "}"
            # QHeaderView 使用与表体相同的 border-right，消除纵向线偏移
            f"QHeaderView::section {{"
            f"  border-right: 1px solid {T.BORDER_DIM};"
            "  border-bottom: none;"
            "  border-top: none;"
            "  border-left: none;"
            "  padding: 2px;"
            "}"
            # 窄滚动条
            + THIN_SCROLLBAR_CSS
        )
        # 行高基于内容自动计算，不提供用户拖拽（减少交互复杂度）
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setMinimumSectionSize(22)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.itemClicked.connect(self._on_row_clicked)
        self.table.itemDoubleClicked.connect(
            lambda it: self._on_row_double_clicked(it.row())
        )
        self.table.viewport().installEventFilter(self)
        self._divider_delegate = HighlightDelegate(self.table)
        self.table.setItemDelegate(self._divider_delegate)

        # ── 安装 event filter 到 AI 建议表格 viewport ──
        QTimer.singleShot(0, self._install_ai_table_filter)

        # ══════════════════════════════════════════════════════
        # QDockWidget 面板
        self._dock_map: Dict[str, QDockWidget] = {}

        # ── 左侧 Dock 区：审校面板（前台）+ 文件管理（后台）──
        self._workspace = WorkspacePanel()
        self._workspace.file_pair_requested.connect(self._on_workspace_load)
        self._workspace.add_queue_requested.connect(self._on_workspace_add_queue)
        self._workspace.doc_remove_requested.connect(self._on_workspace_remove_checked)
        self._workspace.chapter_nav_requested.connect(self._on_workspace_nav)
        self._workspace.entry_selected.connect(self._on_entry_selected)

        # 审校面板
        self._review.doc_align_requested.connect(self._on_workspace_align_checked)
        self._review.doc_auto_repair_requested.connect(self._on_auto_repair)
        self._review.doc_reset_repair_requested.connect(self._on_reset_all)
        self._review.doc_realign_requested.connect(self._on_realign)
        self._review.doc_ai_chapter_requested.connect(self._on_ai_repair_chapter)
        self._review.doc_remove_requested.connect(self._on_workspace_remove_checked)
        self._review.strategy_changed.connect(self._on_strategy_changed)
        self._review.go_to_row.connect(self._on_go_to_row)
        self._review.next_chapter_requested.connect(
            lambda: self._workspace.chapter_nav_requested.emit(1)
        )
        self._review.prev_chapter_requested.connect(
            lambda: self._workspace.chapter_nav_requested.emit(-1)
        )
        self._review.doc_promote_requested.connect(self._on_promote)

        self._create_dock("review", self._review, "🔧 审校面板", Qt.LeftDockWidgetArea)
        self._create_dock(
            "files", self._workspace, "📁 文件管理", Qt.LeftDockWidgetArea
        )
        # 保存原始面板引用（供单栏布局恢复用）
        self._review_orig_widget = self._review
        self._files_orig_widget = self._workspace

        # 筛选面板内嵌于 ReviewController
        self._filter_panel = self._review.filter_panel
        self._filter_panel.filter_changed.connect(self._apply_filter)
        self._filter_panel.filter_changed.connect(lambda: self._debounce_save_history())

        # ══════════════════════════════════════════════════════
        # 底部面板：左 AI 建议表格 + 右运行日志（水平并排）
        # ══════════════════════════════════════════════════════
        from dualign.gui.log_panel import LogPanel

        self._log_panel = LogPanel()
        self._log_panel.setMinimumHeight(40)
        from dualign.gui.log_panel import set_gui_panel, configure_root_logging

        set_gui_panel(self._log_panel)
        configure_root_logging()
        self._log_panel.log("Dualign 启动就绪", "info")

        # ── LogPanel INFO 日志 → StatusBar 消息区 ──
        self._log_panel.info_logged.connect(self._safe_status)

        # AI 建议面板（从 ReviewController 提取的预览表）
        self._ai_panel = self._review.create_ai_panel()
        # AI 建议变更时同步底部面板展开/折叠
        self._review.actions_updated.connect(lambda: self._sync_bottom_panel())

        # ── 底部容器：内容区 + 底部状态栏（始终可见）──
        self._bottom_container = QWidget()
        bc_layout = QVBoxLayout(self._bottom_container)
        bc_layout.setContentsMargins(0, 0, 0, 0)
        bc_layout.setSpacing(0)

        # ── 内容区（可折叠）──
        self._bottom_content = QWidget()
        self._bottom_content.setMinimumHeight(0)
        bc_layout_content = QVBoxLayout(self._bottom_content)
        bc_layout_content.setContentsMargins(0, 0, 0, 0)
        bc_layout_content.setSpacing(0)
        bc_layout_content.addWidget(self._ai_panel, 1)
        bc_layout.addWidget(self._bottom_content, 1)

        # 审校面板日志转发
        self._review.log_message.connect(self._log_panel.log)

        # ── 加入日志面板到文件管理面板底部 ──
        self._workspace.add_log_panel(self._log_panel)

        # ── 审校面板默认在文件管理标签前面 ──
        self.tabifyDockWidget(self._dock_map["review"], self._dock_map["files"])
        self._dock_map["review"].raise_()

        # ── Dock 标签页位置 — 上方（接近 VS Code 风格）──
        self.setTabPosition(Qt.LeftDockWidgetArea, QTabWidget.TabPosition.North)
        # ══════════════════════════════════════════════════════
        # 中央区域：QSplitter（内容区）+ 底部状态栏（始终可见）
        # ══════════════════════════════════════════════════════
        self._stacked = QStackedWidget()

        # 第 0 页：欢迎引导页
        self._welcome = WelcomePage()
        self._welcome.open_files_requested.connect(self._on_open_files)
        self._welcome.open_demo_requested.connect(self._on_open_demo)
        self._welcome.open_agent_config_requested.connect(self._on_open_agent_config)
        self._welcome.batch_discover_requested.connect(self._on_batch_discover)
        self._welcome.recent_file_clicked.connect(self._on_welcome_recent)
        self._welcome.open_guide_requested.connect(self._on_open_welcome_guide)
        # 欢迎页的 minimumSizeHint ≈ 534px（内容驱动），
        # 如果不解除约束会卡死底部面板高度 ≤ 233px。
        # 设为 Ignored 后 QSplitter 可自由分配高度，
        # 欢迎页在空间不足时自动居中裁剪。
        self._welcome.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored
        )
        self._stacked.setMinimumHeight(80)
        self._stacked.addWidget(self._welcome)

        # 第 1 页：表格（稍后 _ensure_table_in_stacked 中构建后插入）
        self._table_page = QWidget()
        self._table_page_layout = QVBoxLayout(self._table_page)
        self._table_page_layout.setContentsMargins(0, 0, 0, 0)
        self._table_page_layout.setSpacing(0)

        # ── StatusBar：视图模式切换 + 导航状态
        self._status_bar = StatusBar()
        self._status_bar.view_mode_toggled.connect(self._on_view_mode_toggled)
        self._table_page_layout.addWidget(self._status_bar)

        # ── 竖直 QSplitter ──
        self._main_splitter = QSplitter(Qt.Vertical)
        self._main_splitter.addWidget(self._stacked)
        self._main_splitter.addWidget(self._bottom_container)
        self._main_splitter.setStretchFactor(0, 3)
        self._main_splitter.setStretchFactor(1, 2)
        self._main_splitter.setSizes([600, 360])
        self._main_splitter.setHandleWidth(3)
        self._main_splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {T.BORDER_MUTED}; }}"
            f"QSplitter::handle:hover {{ background: {T.FG_ACCENT}; }}"
        )
        self._main_splitter.splitterMoved.connect(self._on_splitter_moved)

        # 中央区域外的细边框
        central = QFrame()
        central.setFrameShape(QFrame.Shape.StyledPanel)
        central.setStyleSheet("QFrame#central_frame{border:1px solid palette(mid);}")
        central.setObjectName("central_frame")
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self._main_splitter, 1)

        # ── 底部状态栏（始终可见，不参与 QSplitter）──
        self._build_bottom_bar()
        cl.addWidget(self._bottom_bar)

        # 绑定「显示已处理」checkbox（在底部 AI 状态栏，_build_bottom_bar 后可用）
        self._filter_panel.bind_handled_checkbox(self._inc_handled_cb)

        self.setCentralWidget(central)

        # 默认显示欢迎页
        self._stacked.setCurrentIndex(0)
        self._show_welcome()

        # 加载历史
        h = self._load_history()
        idx = h.get("strategy", 1)
        strategies = ["minimal", "src", "tgt"]
        self._strategy = strategies[idx] if 0 <= idx < 3 else "src"
        self._restore_filter_state(h)
        self._review.set_strategy_index(idx)
        if not h:
            # 首次启动时不触发布局恢复
            self._dock_state_restored = False
        else:
            # 恢复 Dock 布局（停靠区域、浮动状态）
            _dock_state_b64 = h.get("dock_state")
            if _dock_state_b64:
                from PySide6.QtCore import QByteArray

                try:
                    _ba = QByteArray.fromBase64(_dock_state_b64.encode("ascii"))
                    self.restoreState(_ba)
                    self._dock_state_restored = True
                except Exception:
                    self._dock_state_restored = False
            else:
                self._dock_state_restored = False
            # 无论是否有 dock_state，延迟恢复单栏布局偏好
            # （restoreState 会恢复 files dock 的隐藏状态，但不会建立 QSplitter 容器）
            self._restore_layout_deferred = h
        # 恢复 AI 审校偏好
        backend = h.get("ai_backend", "deepseek")
        self._review.set_backend(backend)
        if hasattr(self, "_ai_auto_cb") and self._ai_auto_cb is not None:
            self._ai_auto_cb.setChecked(h.get("ai_auto_approve", False))
        # 恢复上次打开目录
        if h.get("last_open_dir"):
            self._last_open_dir = h["last_open_dir"]
        # 恢复底部面板档位
        saved_ratio = h.get("bottom_ratio", 0.40)
        if isinstance(saved_ratio, (int, float)):
            self._bottom_snap_idx = self._nearest_bottom_ratio(saved_ratio)
        self._update_bottom_title()

    # ── 窗口边界保护（仅位置钳制，不触发 resize）──

    def _setup_dock_tab_bar_context_menu(self):
        """为 Dock QTabBar 添加右键菜单（多栏时可用，单栏时 auto-hide）。"""
        from PySide6.QtWidgets import QTabBar

        for tb in self.findChildren(QTabBar):
            tb.setContextMenuPolicy(Qt.CustomContextMenu)
            tb.customContextMenuRequested.connect(
                lambda pos, t=tb: self._on_dock_tab_bar_context_menu(t, pos)
            )

    def _update_dock_title_bars(self):
        """同步所有 dock 标题栏：单栏显示原生标题栏，多栏(tabified)时隐藏。"""
        from PySide6.QtWidgets import QWidget

        for dock in self._dock_map.values():
            if dock.isFloating():
                # 浮动窗口 → 原生标题栏（窗口管理器提供）
                if dock.titleBarWidget() is not None:
                    dock.setTitleBarWidget(None)
            else:
                tabs = self.tabifiedDockWidgets(dock)
                if tabs:
                    # 与其它 dock tabified → 隐藏原生标题栏，避免与 QTabBar 重复
                    if not isinstance(dock.titleBarWidget(), QWidget):
                        dock.setTitleBarWidget(QWidget())
                else:
                    # 单栏 → 恢复原生标题栏
                    if dock.titleBarWidget() is not None:
                        dock.setTitleBarWidget(None)

    def _on_dock_tab_bar_context_menu(self, tab_bar, pos):
        """QTabBar 右键菜单 — 根据 tab 的 dock objectName 匹配。"""
        tab_idx = tab_bar.tabAt(pos)
        if tab_idx < 0:
            return
        # QTabBar 的 tab 数据关联 UuidRole，但 dock 未设。
        # 换用更可靠的方式：遍历 dock_map 匹配标题
        tab_text = tab_bar.tabText(tab_idx)
        for pid, dock in self._dock_map.items():
            # 匹配：dock 标题 == tab 文本，或 dock 正位于此 tab bar 中
            if dock.windowTitle() == tab_text:
                DockPanelHelper.build_panel_context_menu(
                    dock, pid, self, self._dock_map, tab_bar.mapToGlobal(pos)
                )
                return

    def _set_initial_dock_sizes(self):
        """设置左栏初始大小。

        审校与文件面板统一宽度 360px。
        resizeDocks 是 Qt 停靠状态下唯一正确设置尺寸的 API。
        """
        h = self._load_history() if not self._has_data else {}
        lw = h.get("dock_review_width", 360)
        l_dock = self._dock_map.get("review")
        f_dock = self._dock_map.get("files")
        if l_dock and not l_dock.isFloating():
            self.resizeDocks([l_dock], [lw], Qt.Orientation.Horizontal)
        # 纵向比例默认 60%/40%
        if l_dock and f_dock and not l_dock.isFloating() and not f_dock.isFloating():
            self.resizeDocks([l_dock, f_dock], [300, 200], Qt.Orientation.Vertical)

    def _sync_undo_redo(self):
        """根据栈状态同步撤销/恢复按钮可用性。"""
        has_undo = len(getattr(self, "_undo_stack", [])) > 0
        has_redo = len(getattr(self, "_redo_stack", [])) > 0
        has_data = self._repair_state is not None
        if hasattr(self, "_review") and self._review is not None:
            for key, ok in [
                ("undo", has_undo and has_data),
                ("redo", has_redo and has_data),
            ]:
                for d in (self._review._doc_btns, self._review._btn_refs):
                    btn = d.get(key)
                    if btn:
                        btn.setEnabled(ok)

    def _sync_bottom_panel(self):
        """根据当前数据状态自动展开或折叠底部建议面板。

        - 有待处理 AI 建议 → 展开
        - 无建议 + AI 不可用 → 折叠
        - 无数据 → 折叠
        - 预览模式下不干预（由预览模式入口/退出管理）
        """
        if getattr(self, "_bottom_locked", False):
            return
        if getattr(self, "_preview_active", False):
            return
        has_pending = False
        if hasattr(self, "_review") and self._review is not None:
            try:
                has_pending = len(self._review._pending_action_list) > 0
            except Exception:
                pass
        from dualign.providers import active_repair_agent

        ai_ok = bool(active_repair_agent() and active_repair_agent().key_plain)
        has_data = self._repair_state is not None
        should_expand = has_data and (has_pending or ai_ok)

        if should_expand and self._bottom_collapsed:
            self._toggle_bottom_panel()
        elif not should_expand and not self._bottom_collapsed:
            self._toggle_bottom_panel()

    def _toggle_bottom_panel(self):
        """折叠/展开 AI 建议内容区（欢迎页时锁定）。

        展开到当前吸附档位 (_bottom_snap_idx)，折叠为 0。
        """
        if getattr(self, "_bottom_locked", False):
            return
        total = sum(self._main_splitter.sizes())

        if self._bottom_collapsed:
            self._bottom_collapsed = False
            self._bottom_content.show()
            self._bottom_toggle_btn.setText("▼")
            self._bottom_toggle_btn.setToolTip("折叠底部面板")
            self._bottom_title.setStyleSheet(
                "font-size:11px;background:transparent;border:none;"
            )
            ratio = _BOTTOM_RATIOS[self._bottom_snap_idx]
            bot_h = int(total * ratio)
            top_h = total - bot_h
            self._main_splitter.setSizes([top_h, bot_h])
            self._update_bottom_title()
        else:
            self._bottom_collapsed = True
            self._bottom_content.hide()
            self._bottom_toggle_btn.setText("▲")
            self._bottom_toggle_btn.setToolTip("展开底部面板")
            self._bottom_title.setStyleSheet(
                "font-size:11px;background:transparent;border:none;color:#666;"
            )
            self._bottom_title.setText("AI 建议列表")
            self._main_splitter.setSizes([total, 0])

        self._debounce_save_history()

    def _on_splitter_moved(self, pos: int, index: int):
        """拖拽手柄 → 四态吸附（折叠/30%/40%/50%），带滞回区防止抖动。

        吸附规则:
          - 展开→折叠: 底栏比例 < COLLAPSE_RATIO (15%) → 折叠
          - 折叠→展开: 底栏比例 > EXPAND_RATIO (22%) → 展开至 30%
          - 展开态拖拽: 吸附到最近的档位 (30%/40%/50%)
          - 15%-22% 为死区，不触发切换
        """
        if getattr(self, "_bottom_locked", False):
            # 欢迎页锁定 → 强制压回折叠
            sizes = self._main_splitter.sizes()
            total = sum(sizes)
            if len(sizes) >= 2 and sizes[1] > 0:
                self._main_splitter.setSizes([total, 0])
            return

        sizes = self._main_splitter.sizes()
        if len(sizes) < 2:
            return
        bot_h = sizes[1]
        total = sum(sizes)
        ratio = bot_h / total if total > 0 else 0

        if self._bottom_collapsed:
            # 折叠态：拖过 EXPAND_RATIO 则展开至 30%
            if ratio >= _EXPAND_RATIO:
                self._bottom_collapsed = False
                self._bottom_content.show()
                self._bottom_toggle_btn.setText("▼")
                self._bottom_toggle_btn.setToolTip("折叠底部面板")
                self._bottom_title.setStyleSheet(
                    "font-size:11px;background:transparent;border:none;"
                )
                self._bottom_snap_idx = 0
                r = _BOTTOM_RATIOS[0]
                bot_h2 = int(total * r)
                top_h2 = total - bot_h2
                self._main_splitter.setSizes([top_h2, bot_h2])
                self._update_bottom_title()
                self._debounce_save_history()
            else:
                # 死区内 → 压回 0
                if bot_h > 0:
                    self._main_splitter.setSizes([total, 0])
        else:
            # 展开态：底栏 < COLLAPSE_RATIO → 折叠
            if ratio < _COLLAPSE_RATIO:
                self._bottom_collapsed = True
                self._bottom_content.hide()
                self._bottom_toggle_btn.setText("▲")
                self._bottom_toggle_btn.setToolTip("展开底部面板")
                self._bottom_title.setStyleSheet(
                    "font-size:11px;background:transparent;border:none;color:#666;"
                )
                self._bottom_title.setText("AI 建议列表")
                self._main_splitter.setSizes([total, 0])
                self._debounce_save_history()
            else:
                # 展开态下任何拖拽 → 吸附到最近的档位
                new_idx = self._nearest_bottom_ratio(ratio)
                self._bottom_snap_idx = new_idx
                r = _BOTTOM_RATIOS[new_idx]
                bot_h2 = int(total * r)
                top_h2 = total - bot_h2
                self._main_splitter.setSizes([top_h2, bot_h2])
                self._update_bottom_title()
                self._debounce_save_history()

    # ── 底部面板档位辅助 ──

    def _nearest_bottom_ratio(self, ratio: float) -> int:
        """找到最近的吸附档位索引。"""
        best_idx = 0
        best_dist = abs(ratio - _BOTTOM_RATIOS[0])
        for i, r in enumerate(_BOTTOM_RATIOS):
            d = abs(r - ratio)
            if d < best_dist:
                best_dist = d
                best_idx = i
        return best_idx

    def _update_bottom_title(self):
        """更新底部面板标题，显示当前档位百分比。"""
        if not hasattr(self, "_bottom_title") or self._bottom_title is None:
            return
        if self._bottom_collapsed:
            self._bottom_title.setText("AI 建议列表")
        else:
            pct = int(_BOTTOM_RATIOS[self._bottom_snap_idx] * 100)
            self._bottom_title.setText(f"AI 建议列表 · {pct}%")

    def _show_welcome(self):
        """切换到欢迎引导页 → 折叠并锁定底部面板。"""
        if self._stacked:
            self._stacked.setCurrentIndex(0)
        self._has_data = False
        # 折叠 + 锁定底部面板
        self._bottom_locked = True
        if not self._bottom_collapsed:
            self._bottom_collapsed = True
            self._bottom_content.hide()
            self._bottom_toggle_btn.setText("▲")
            self._bottom_toggle_btn.setToolTip("展开底部面板")
            self._bottom_title.setText("AI 建议列表")
            self._bottom_title.setStyleSheet(
                "font-size:11px;background:transparent;border:none;color:#666;"
            )
            sizes = self._main_splitter.sizes()
            total = sum(sizes)
            self._main_splitter.setSizes([total, 0])
        # 刷新欢迎页最近文件对列表
        if hasattr(self, "_workspace") and hasattr(self, "_welcome"):
            self._welcome.set_recent_pairs(self._workspace.get_recent_pairs())
        # 若对齐正在进行，在欢迎页显示状态
        if getattr(self, "_worker", None) and self._worker.isRunning():
            self._welcome.set_aligning("正在对齐…")

    # ── FocusManager 信号处理 ──

    def _on_view_mode_toggled(self, preview: bool):
        """StatusBar 视图模式切换：校订 ↔ 预览。"""
        if preview == self._preview_active:
            return
        self._preview_active = preview
        self._switch_table_mode(preview)
        # 预览模式无 snap 概念，禁用所有 snap 依赖控件
        if hasattr(self, "_review"):
            self._review.set_preview_mode(preview)
        # 更新 StatusBar 预览标签
        if hasattr(self, "_status_bar"):
            _qa = getattr(self, "_last_quality_assessment", None)
            rejected = bool(_qa and _qa.get("quality") == "unreliable")
            self._status_bar.set_preview_active(preview, rejected)
        # 底部 AI 面板折叠/恢复
        if preview:
            self._preview_saved_bottom = not self._bottom_collapsed
            if not self._bottom_collapsed:
                self._toggle_bottom_panel()
        else:
            saved = getattr(self, "_preview_saved_bottom", None)
            if saved is not None:
                if saved and self._bottom_collapsed:
                    self._toggle_bottom_panel()
            self._preview_saved_bottom = None
        # 刷新视图
        self._apply_filter()

    def _on_preview_row_clicked(self, row: int, col: int):
        """预览表点击行 — 预览模式无 snap 概念，仅选中行。"""
        pass  # 只保留行选中高亮（由 SelectionBehavior.SelectRows 自动处理）

    def _on_preview_context_menu(self, pos):
        """预览表右键菜单：复制选中行。"""
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        copy_action = menu.addAction("复制")
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_action.triggered.connect(self._copy_preview_selection)
        menu.exec(self._preview_table.viewport().mapToGlobal(pos))

    def _copy_preview_selection(self):
        """复制预览表选中行到剪贴板。"""
        sel = self._preview_table.selectedItems()
        if not sel:
            return
        rows = {}
        for item in sel:
            r, c = item.row(), item.column()
            rows.setdefault(r, {})[c] = item.text()
        lines = []
        for r in sorted(rows):
            cells = [rows[r].get(c, "") for c in range(4)]
            lines.append("\t".join(cells))
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText("\n".join(lines))
        self._safe_status("📋 已复制预览行")

    def _on_snap_focused(self, snap_i: int):
        """FocusManager.snap_focused 信号处理。

        不触发对齐表滚动（由 _on_go_to_row 的调用方按需触发），
        只更新 HighlightDelegate 选中状态 + AI 预览表高亮。
        """
        # 更新 HighlightDelegate 焦点行 + 选中行
        self._update_table_highlight()
        # AI 预览表高亮（含滚动到对应行）
        if hasattr(self, "_review"):
            self._review.focus_snap_ai(snap_i)

    def _on_selection_changed(self, snaps: Set[int]):
        """FocusManager.selection_changed 信号处理。

        更新 HighlightDelegate + 定位器 + 高亮视觉。
        """
        self._update_table_highlight()
        self._emit_indicator(snaps)

    def _on_text_col_resized(self, logical_index: int, _old: int, _new: int):
        """原文/译文列宽变化时，带防抖重算行高（word-wrap 换行数可能变化）。"""
        if logical_index not in (5, 6):
            return
        if not hasattr(self, "_row_resize_timer"):
            self._row_resize_timer = QTimer(self)
            self._row_resize_timer.setSingleShot(True)
            self._row_resize_timer.setInterval(80)
            self._row_resize_timer.timeout.connect(self._recalc_row_heights)
        self._row_resize_timer.start()

    def _recalc_row_heights(self):
        """列宽变化后触发 _refresh 以重算行高。"""
        if self._repair_state is not None and self.table.rowCount() > 0:
            self._refresh()

    def _update_table_highlight(self):
        """同步 HighlightDelegate 的选中行/焦点行状态。"""
        if hasattr(self, "_divider_delegate"):
            self._divider_delegate.set_selected_rows(
                {
                    row
                    for row, si in self._row_op_map.items()
                    if si in self._focus.selected_snaps
                }
            )
            if self._focus.focused_snap is not None:
                focused_row = next(
                    (
                        row
                        for row, si in self._row_op_map.items()
                        if si == self._focus.focused_snap
                    ),
                    None,
                )
                self._divider_delegate.set_focused_row(focused_row)
            self.table.viewport().update()

    def _show_table(self):
        """切换到对齐表格页 → 解锁底部面板。"""
        if self._stacked and self._stacked.count() > 1:
            self._stacked.setCurrentIndex(1)
        self._has_data = True
        self._bottom_locked = False

    def _ensure_table_in_stacked(self):
        """确保表格和预览表已添加到 stacked widget 的第 1 页。

        用 QStackedWidget 切换 7 列主表和 4 列预览表，
        避免切换模式时反复修改列数/标题/标题导致视觉闪烁。"""
        if self._stacked and self._stacked.count() < 2:
            # ── 主表（7 列）──
            g_table = QWidget()
            gl = QVBoxLayout(g_table)
            gl.setContentsMargins(0, 0, 0, 0)
            gl.setSpacing(0)
            gl.addWidget(self.table, 1)

            # ── 预览表（4 列：行号 | 相似度 | 原文 | 译文）──
            self._preview_table = QTableWidget()
            self._preview_table.setColumnCount(4)
            self._preview_table.setHorizontalHeaderLabels(
                ["行", "当前评分", "原文", "译文"]
            )
            hdr = self._preview_table.horizontalHeader()
            hdr.setMinimumSectionSize(0)
            hdr.setStretchLastSection(False)
            from dualign.gui.base_table import calc_snap_width as _csw

            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            hdr.resizeSection(0, _csw(0))
            hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            hdr.resizeSection(1, 60 if self._filter_panel.show_scores else 6)
            hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            self._preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self._preview_table.setSelectionBehavior(
                QAbstractItemView.SelectionBehavior.SelectRows
            )
            self._preview_table.setSelectionMode(
                QAbstractItemView.SelectionMode.SingleSelection
            )
            self._preview_table.setShowGrid(False)
            self._preview_table.verticalHeader().setVisible(False)
            self._preview_table.setWordWrap(True)
            self._preview_table.setTextElideMode(Qt.TextElideMode.ElideNone)
            self._preview_table.setContextMenuPolicy(
                Qt.ContextMenuPolicy.CustomContextMenu
            )
            self._preview_table.customContextMenuRequested.connect(
                lambda pos: self._on_preview_context_menu(pos)
            )
            self._preview_table.cellClicked.connect(self._on_preview_row_clicked)
            # 始终显示垂直滚动条，避免列宽因滚动条显隐抖动
            self._preview_table.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOn
            )
            # ── 网格线（与主表一致）──
            from dualign.gui.theme import T as _T2
            from dualign.gui.base_table import THIN_SCROLLBAR_CSS

            self._preview_table.setStyleSheet(
                "QTableWidget { outline: none; }"
                f"QTableWidget::item {{"
                f"  border-bottom: 1px solid {_T2.BORDER_MUTED};"
                f"  border-right: 1px solid {_T2.BORDER_MUTED};"
                f"  padding: 2px;"
                f"}}"
                # 窄滚动条
                + THIN_SCROLLBAR_CSS
            )
            from dualign.gui.base_table import HighlightDelegate

            self._preview_table.setItemDelegate(HighlightDelegate(self._preview_table))

            g_preview = QWidget()
            gl2 = QVBoxLayout(g_preview)
            gl2.setContentsMargins(0, 0, 0, 0)
            gl2.setSpacing(0)
            gl2.addWidget(self._preview_table, 1)

            # ── 空状态页（index 2）：无异常时提示用户 ──
            self._empty_state_widget = QWidget()
            empty_layout = QVBoxLayout(self._empty_state_widget)
            empty_layout.setContentsMargins(0, 0, 0, 0)
            empty_layout.setSpacing(0)

            empty_center = QWidget()
            ec_layout = QVBoxLayout(empty_center)
            ec_layout.setContentsMargins(40, 40, 40, 40)
            ec_layout.setSpacing(12)
            ec_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            icon_lbl = QLabel("✅")
            icon_lbl.setStyleSheet("font-size: 48px;")
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ec_layout.addWidget(icon_lbl)

            title_lbl = QLabel("全部对齐无误")
            title_lbl.setStyleSheet(
                "font-size: 22px; font-weight: bold;"
                "background: transparent; border: none;"
            )
            title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ec_layout.addWidget(title_lbl)

            self._empty_subtitle = QLabel("当前异常类型筛选条件下，未发现异常文本对。")
            self._empty_subtitle.setStyleSheet(
                "font-size: 13px;" "background: transparent; border: none;"
            )
            self._empty_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_subtitle.setWordWrap(True)
            ec_layout.addWidget(self._empty_subtitle)

            show_all_btn = QPushButton("📋 显示全部文本对")
            show_all_btn.setFixedSize(200, 36)
            show_all_btn.setStyleSheet(
                "QPushButton {"
                "  border: 1px solid palette(link); border-radius: 4px;"
                "  font-size: 13px;"
                "}"
                "QPushButton:hover {"
                "  border-color: palette(highlight);"
                "}"
            )
            show_all_btn.clicked.connect(self._on_show_all_snaps)
            btn_container = QWidget()
            # btn_container 背景由 Fusion QPalette 管理
            btn_layout = QHBoxLayout(btn_container)
            btn_layout.setContentsMargins(0, 0, 0, 0)
            btn_layout.addStretch()
            btn_layout.addWidget(show_all_btn)
            btn_layout.addStretch()
            ec_layout.addWidget(btn_container)

            ec_layout.addStretch(1)
            empty_layout.addWidget(empty_center, 1)

            # 用 QStackedWidget 包含主表、预览表和空状态页
            self._table_stack = QStackedWidget()
            self._table_stack.addWidget(g_table)  # index 0
            self._table_stack.addWidget(g_preview)  # index 1
            self._table_stack.addWidget(self._empty_state_widget)  # index 2

            self._table_page_layout.addWidget(self._table_stack, 1)
            self._stacked.addWidget(self._table_page)

    def _on_quality_gate_config(self):
        """打开质量门控参数配置对话框。"""
        from PySide6.QtWidgets import (
            QDialog,
            QFormLayout,
            QDoubleSpinBox,
            QDialogButtonBox,
            QGroupBox,
            QVBoxLayout,
            QPushButton,
            QHBoxLayout,
            QLabel,
        )
        from dualign.services.quality_gate import QualityGateConfig
        from dualign.gui.settings import KEY_QUALITY_GATE

        dlg = QDialog(self)
        dlg.setWindowTitle("质量门控参数")
        dlg.setMinimumWidth(320)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        cfg = self._quality_config
        defaults = QualityGateConfig()

        info = QLabel("调整对齐质量评估阈值。\n" "修改后仅在下次对齐时生效。")
        info.setWordWrap(True)
        layout.addWidget(info)

        # ── 文档级门控 ──
        g0 = QGroupBox("文档级")
        fl0 = QFormLayout(g0)
        fl0.setSpacing(4)

        sb_ad = QDoubleSpinBox()
        sb_ad.setRange(0.05, 0.95)
        sb_ad.setSingleStep(0.05)
        sb_ad.setDecimals(2)
        sb_ad.setValue(cfg.anchor_density_min)
        fl0.addRow("锚点密度下限", sb_ad)

        sb_gr = QDoubleSpinBox()
        sb_gr.setRange(0.01, 0.50)
        sb_gr.setSingleStep(0.01)
        sb_gr.setDecimals(2)
        sb_gr.setValue(cfg.gap_row_ratio_max)
        fl0.addRow("间隙行比例上限", sb_gr)

        layout.addWidget(g0)

        # ── 离群低分 ──
        g1 = QGroupBox("离群低分")
        fl1 = QFormLayout(g1)
        fl1.setSpacing(4)

        sb_k = QDoubleSpinBox()
        sb_k.setRange(1.0, 6.0)
        sb_k.setSingleStep(0.5)
        sb_k.setDecimals(1)
        sb_k.setValue(cfg.zscore_k)
        fl1.addRow("Z-score 阈值 k", sb_k)

        sb_ms = QDoubleSpinBox()
        sb_ms.setRange(0.3, 0.9)
        sb_ms.setSingleStep(0.05)
        sb_ms.setDecimals(2)
        sb_ms.setValue(cfg.zscore_min_score)
        fl1.addRow("最低绝对得分", sb_ms)

        layout.addWidget(g1)

        # ── 按钮行 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        restore_defaults_btn = QPushButton("恢复默认")
        restore_defaults_btn.clicked.connect(
            lambda: (
                sb_ad.setValue(defaults.anchor_density_min),
                sb_gr.setValue(defaults.gap_row_ratio_max),
                sb_k.setValue(defaults.zscore_k),
                sb_ms.setValue(defaults.zscore_min_score),
            )
        )
        btn_row.addWidget(restore_defaults_btn)
        btn_row.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        btn_row.addWidget(buttons)

        layout.addLayout(btn_row)

        if dlg.exec() == QDialog.Accepted:
            self._quality_config = QualityGateConfig(
                anchor_density_min=sb_ad.value(),
                gap_row_ratio_max=sb_gr.value(),
                zscore_k=sb_k.value(),
                zscore_min_score=sb_ms.value(),
            )
            DualignConfig.instance().set(
                KEY_QUALITY_GATE,
                {
                    "anchor_density_min": sb_ad.value(),
                    "gap_row_ratio_max": sb_gr.value(),
                    "zscore_k": sb_k.value(),
                    "zscore_min_score": sb_ms.value(),
                },
            )
            DualignConfig.instance().save()
            self._safe_status(
                f"✅ 门控参数已更新：锚点≥{sb_ad.value():.0%} "
                f"间隙≤{sb_gr.value():.0%} k={sb_k.value():.1f}"
            )

    def _build_bottom_bar(self):
        """构建底部状态栏（始终显示在 QSplitter 下方）。"""
        bar = QWidget()
        bar.setFixedHeight(24)
        bar.setObjectName("bottomBar")
        bar.setStyleSheet(
            "QWidget#bottomBar{"
            "  border-top:1px solid palette(mid);"
            "  background:palette(window);"
            "}"
        )
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(6, 0, 6, 0)
        bl.setSpacing(4)

        self._bottom_toggle_btn = QPushButton("▼")
        self._bottom_toggle_btn.setFixedSize(20, 20)
        self._bottom_toggle_btn.setStyleSheet(
            "QPushButton{background:transparent;border:none;}"
            "QPushButton:hover{color:palette(link);}"
        )
        bl.addWidget(self._bottom_toggle_btn)
        self._bottom_toggle_btn.clicked.connect(self._toggle_bottom_panel)
        self._bottom_title = QLabel("AI 建议列表")
        self._bottom_title.setStyleSheet(
            "font-size:11px;background:transparent;border:none;"
        )
        bl.addWidget(self._bottom_title, 0)

        # 自动应用建议 / 显示已处理（紧跟标题，原生样式）
        self._ai_auto_cb = QCheckBox("自动应用建议")
        self._ai_auto_cb.setChecked(False)
        self._ai_auto_cb.toggled.connect(
            lambda checked: setattr(self._review, "_auto_approve_enabled", checked)
        )
        bl.addWidget(self._ai_auto_cb, 0)

        self._inc_handled_cb = QCheckBox("显示已处理")
        self._inc_handled_cb.setChecked(True)
        self._inc_handled_cb.toggled.connect(
            lambda: self._review._rebuild_ai_suggestions()
        )
        bl.addWidget(self._inc_handled_cb, 0)
        bl.addWidget(_vline())

        # 左侧：词嵌入模型状态
        self._embed_dot_frame = QFrame()
        _el = QHBoxLayout(self._embed_dot_frame)
        _el.setContentsMargins(0, 0, 0, 0)
        _el.setSpacing(4)
        self._embed_dot = StatusDot("词嵌入模型")
        self._header_dots = {"embed": self._embed_dot}
        _el.addWidget(self._embed_dot, 1)
        bl.addWidget(self._embed_dot_frame, 1)
        bl.addWidget(_vline())

        # 右侧：大语言模型状态
        self._ai_dot_frame = QFrame()
        _al = QHBoxLayout(self._ai_dot_frame)
        _al.setContentsMargins(0, 0, 0, 0)
        _al.setSpacing(4)
        self._ai_dot = StatusDot("大语言模型")
        self._header_dots["ai"] = self._ai_dot
        _al.addWidget(self._ai_dot, 1)
        bl.addWidget(self._ai_dot_frame, 1)
        bl.addWidget(_vline())

        # 建议统计
        self._ai_suggest_count = QLabel("")
        self._ai_suggest_count.setStyleSheet(
            "font-size:11px;background:transparent;border:none;padding:0 4px;"
        )
        self._ai_suggest_count.setAlignment(Qt.AlignCenter)
        bl.addWidget(self._ai_suggest_count, 1)
        self._ai_suggest_count.setFixedWidth(60)

        self._bottom_bar = bar

    def _status(self, text: str, role: str = "info"):
        """同时更新状态栏和日志面板。"""
        self._safe_status(text)
        if hasattr(self, "_log_panel") and self._log_panel is not None:
            self._log_panel.log(text, role)

    def _refresh_status_dots(self):
        """异步刷新底部 AI 标题栏的状态指示灯，不阻塞主线程。"""
        from dualign.gui.workers import EnvCheckThread

        dots = getattr(self, "_header_dots", None)
        if not dots:
            return

        # 立即显示加载状态
        self._set_dot("embed", None, "词嵌入模型 — 检测中…")
        self._set_dot("ai", None, "大语言模型 — 检测中…")

        # 避免重复启动
        _existing = getattr(self, "_env_dot_thread", None)
        if _existing is not None and _existing.isRunning():
            return

        thread = EnvCheckThread(self)
        thread.env_checked.connect(self._on_dot_check_result)
        self._env_dot_thread = thread
        thread.start()

    def _on_dot_check_result(self, result: dict):
        """后台环境检测完成后更新状态指示灯。"""
        dots = getattr(self, "_header_dots", None)
        if not dots:
            return

        # ── 词嵌入模型 ──
        embed_ok = result.get("embed_ok", False)
        embed_model = result.get("embed_model", "")
        if embed_ok:
            self._set_dot("embed", True, f"词嵌入模型: {embed_model or '就绪'}")
        else:
            self._set_dot("embed", False, "词嵌入模型")

        # ── 大语言模型 ──
        ai_ok = result.get("ai_ok", False)
        ai_detail = result.get("ai_detail", "")
        if ai_ok:
            self._set_dot("ai", True, f"大语言模型: {ai_detail}")
        else:
            self._set_dot("ai", None, "大语言模型")

        # ── 更新功能门控，复用检测结果避免二次 HTTP 请求 ──
        self._update_feature_gating(embed_ok=embed_ok, ai_ok=ai_ok)

        # 刷新功能阶梯
        self._update_feature_gating()

    def _set_dot(self, pid: str, ok: Optional[bool], text: str):
        """设置底部标题栏状态指示灯。"""
        d = self._header_dots.get(pid)
        if d:
            d.set_ok(ok)
            d.set_text(text)

    def _update_feature_gating(
        self, embed_ok: bool | None = None, ai_ok: bool | None = None
    ):
        """根据当前环境能力启用/禁用各功能按钮。

        接受可选的环境状态参数以避免重复 HTTP 请求。
        不传参时从内存读取提供方配置（不做健康检测）。

        阶梯：
          Tier 0: 嵌入不可用 → 仅欢迎页、文件浏览
          Tier 1: 嵌入就绪 → 可对齐、自动修复
          Tier 2: 对齐数据已加载 → 可手动校订、重新对齐
          Tier 3: AI Agent 就绪 → 可 AI 审校
        """
        from dualign.providers import ProviderManager, active_repair_agent

        ProviderManager.load()
        has_data = self._repair_state is not None

        # 检测环境（仅从内存/缓存判断，不做 HTTP 请求）
        if embed_ok is None:
            embed_ok = ProviderManager.active() is not None
        if ai_ok is None:
            agent = active_repair_agent()
            ai_ok = (
                bool(
                    agent
                    and agent.base_url
                    and agent.base_url.strip()
                    and agent.api_key
                )
                if agent
                else False
            )

        # ── 审校面板控权（set_gating 参数名保持内部兼容）──
        self._review.set_gating(
            ollama_ok=embed_ok,
            model_ok=embed_ok,
            ai_ok=ai_ok,
            data_loaded=has_data,
        )
        self._review.set_data_loaded(has_data)
        self._review.set_ai_enabled(ai_ok and has_data)
        self._sync_undo_redo()

        # ── StatusBar 不再推送环境消息 —— 由 LogPanel 日志自然显示 ──

    def _create_dock(
        self, panel_id: str, widget: QWidget, title: str, area
    ) -> QDockWidget:
        """创建并注册一个 QDockWidget 面板。"""
        dock = QDockWidget(title, self)
        dock.setObjectName(f"dock_{panel_id}")
        dock.setAllowedAreas(
            Qt.LeftDockWidgetArea
            | Qt.RightDockWidgetArea
            | Qt.TopDockWidgetArea
            | Qt.BottomDockWidgetArea
        )
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        dock.setFloating(False)

        dock.setWidget(widget)
        # 面板宽度锁定 360px（不可拖拽调整）
        dock.setFixedWidth(360)

        # ── Dock 样式 ──
        from dualign.gui.theme import T

        dock.setStyleSheet(f"""
            QDockWidget > QWidget {{
                border: 1px solid {T.BORDER_DIM};
                border-radius: 0px;
            }}
            QDockWidget::float-button {{
                background: transparent;
                border: none;
                width: 14px; height: 14px;
            }}
            QDockWidget::close-button {{
                background: transparent;
                border: none;
                width: 14px; height: 14px;
            }}
        """)

        # 动态管理：单栏显示原生标题栏，多栏(tabified)时隐藏避免与 QTabBar 重复
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)

        dock.visibilityChanged.connect(
            lambda v, pid=panel_id: (
                self._sync_panel_menu_checks(),
                self._update_dock_title_bars(),
            )
        )
        dock.topLevelChanged.connect(lambda: self._update_dock_title_bars())
        dock.dockLocationChanged.connect(lambda _area: self._update_min_width())

        # ── 右键上下文菜单 ──
        dock.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        dock.customContextMenuRequested.connect(
            lambda pos, d=dock, pid=panel_id: self._on_dock_context_menu(d, pid, pos)
        )
        self.addDockWidget(area, dock)
        self._dock_map[panel_id] = dock
        # 初始同步标题栏状态（单栏/多栏）
        self._update_dock_title_bars()

        return dock

    def _install_ai_table_filter(self):
        """延迟安装 AI 建议表格的 event filter（确保 _preview_table 已创建）。"""
        ai_tbl = getattr(getattr(self, "_review", None), "_preview_table", None)
        if ai_tbl is not None:
            try:
                ai_tbl.table.viewport().installEventFilter(self)
            except Exception:
                import traceback as _tb

                _tb.print_exc()

    # ── 辅助侧边栏创建（延迟到第一次 _toggle_aux_dock 时执行）──

    def _on_dock_context_menu(self, dock: QDockWidget, panel_id: str, pos):
        """面板右键菜单。"""
        DockPanelHelper.build_panel_context_menu(
            dock, panel_id, self, self._dock_map, dock.mapToGlobal(pos)
        )
        self._debounce_save_history()

    def _setup_menu(self):
        """菜单栏 — 按功能域分类，遵循标准桌面应用惯例。"""
        mb = self.menuBar()

        # ═══════════════════════════════════════════════════
        # 文件(&F) — 打开、导入、导出、退出
        # ═══════════════════════════════════════════════════
        fm = mb.addMenu("文件(&F)")
        fm.addAction("打开文件对...", self._on_open_files, QKeySequence("Ctrl+O"))
        fm.addAction(
            "批量发现文件对...",
            self._on_batch_discover,
            QKeySequence("Ctrl+Shift+O"),
        )
        fm.addAction("打开 Demo", self._on_open_demo, QKeySequence("Ctrl+D"))
        fm.addSeparator()
        fm.addAction("导出修复结果...", self._on_export, QKeySequence.StandardKey.Save)
        fm.addAction("固化修复...", self._on_promote, QKeySequence("Ctrl+Shift+P"))
        fm.addSeparator()

        # ── 查看文件子菜单：快速打开源文件/修复文件/报告 ──
        vm = fm.addMenu("查看文件")
        vm.addAction("源文件（原文）", self._on_view_source)
        vm.addAction("源文件（译文）", self._on_view_target)
        vm.addSeparator()
        vm.addAction("修复报告", self._on_view_report)
        vm.addAction("修复后原文", self._on_view_repaired_source)
        vm.addAction("修复后译文", self._on_view_repaired_target)

        fm.addSeparator()
        fm.addAction("退出", self.close, QKeySequence.StandardKey.Quit)

        # ═══════════════════════════════════════════════════
        # 编辑(&E) — 撤销、重做、重置
        # ═══════════════════════════════════════════════════
        em = mb.addMenu("编辑(&E)")
        em.addAction("撤销", self._on_undo, QKeySequence.StandardKey.Undo)
        em.addAction(
            "恢复",
            self._on_redo,
            QKeySequence("Ctrl+Y"),
        )
        em.addSeparator()
        em.addAction(
            "重置当前修复",
            self._on_reset_current_snap,
            QKeySequence("Ctrl+R"),
        )
        em.addAction(
            "重置全部修复",
            self._on_reset_all,
            QKeySequence("Ctrl+Shift+R"),
        )

        # ═══════════════════════════════════════════════════
        # 视图(&V) — 面板显隐、布局
        # ═══════════════════════════════════════════════════
        vm = mb.addMenu("视图(&V)")
        self._menu_toggle_review = vm.addAction("审校面板")
        self._menu_toggle_review.setCheckable(True)
        self._menu_toggle_review.setChecked(True)
        self._menu_toggle_review.triggered.connect(
            lambda checked: self._on_toggle_panel("review", checked)
        )
        self._menu_toggle_files = vm.addAction("文件管理")
        self._menu_toggle_files.setCheckable(True)
        self._menu_toggle_files.setChecked(True)
        self._menu_toggle_files.triggered.connect(
            lambda checked: self._on_toggle_panel("files", checked)
        )

        vm.addSeparator()
        vm.addAction(
            "显示/隐藏侧边栏", self._toggle_left_dock_area, QKeySequence("Ctrl+B")
        )
        vm.addAction(
            "显示/隐藏底部面板", self._toggle_bottom_panel, QKeySequence("Ctrl+J")
        )
        vm.addAction(
            "显示/隐藏辅助侧边栏", self._toggle_aux_dock, QKeySequence("Ctrl+Alt+B")
        )
        vm.addSeparator()
        vm.addAction("重置窗口布局", self._on_reset_layout)

        # ═══════════════════════════════════════════════════
        # 对齐(&A) — 对齐、修复、AI 审校
        # ═══════════════════════════════════════════════════
        am = mb.addMenu("对齐(&A)")
        am.addAction(
            "重新对齐",
            self._on_realign,
            QKeySequence("Ctrl+Shift+A"),
        )
        am.addAction(
            "一键修复异常",
            self._on_auto_repair,
            QKeySequence("Ctrl+Shift+F"),
        )
        am.addSeparator()
        am.addAction(
            "AI 校订当前章节",
            self._on_ai_repair_chapter,
            QKeySequence("Ctrl+Shift+I"),
        )

        # ═══════════════════════════════════════════════════
        # 设置(&S) — 配置、环境
        # ═══════════════════════════════════════════════════
        sm = mb.addMenu("设置(&S)")
        sm.addAction("模型与 Agent 配置...", self._on_open_agent_config)
        sm.addSeparator()
        sm.addAction("质量门控参数...", self._on_quality_gate_config)
        sm.addAction(
            "刷新环境检测",
            self._refresh_status_dots,
            QKeySequence("F5"),
        )
        sm.addSeparator()
        sm.addAction("恢复默认设置", self._on_reset_settings)

        # ═══════════════════════════════════════════════════
        # 帮助(&H) — 使用指南、文档、关于
        # ═══════════════════════════════════════════════════
        hm = mb.addMenu("帮助(&H)")
        hm.addAction(
            "GUI 使用指南",
            self._on_open_gui_guide,
            QKeySequence("F1"),
        )
        hm.addAction("打开文档文件夹", self._on_open_docs_folder)
        hm.addSeparator()
        hm.addAction("关于 Dualign", self._on_about)

    # ── 文件菜单回调 ──

    def _on_promote(self):
        """固化修复当前章节（菜单项回调）。"""
        from pathlib import Path

        if not self._repaired_dir or not self._current_entry_id:
            # 无当前章节信息 → 提示用户
            QMessageBox.information(
                self,
                "固化修复",
                "请先在 Reader GUI 中操作：\n\n"
                "1. 在文件名树中勾选要固化的章节\n"
                "2. 点击操作面板中的「⬆ 固化修复」按钮\n\n"
                "固化会用 repaired/ 中的修复结果覆盖 raw/ 文件，"
                "并备份原文件为 .bak。",
            )
            return

        from dualign.common import promote_repaired

        entry_id = self._current_entry_id
        repaired_dir = self._repaired_dir

        # 从 repaired_dir 推导 src/tgt 路径
        src_path = str(Path(repaired_dir) / f"{entry_id}.source.md")
        tgt_path = str(Path(repaired_dir) / f"{entry_id}.target.md")

        reply = QMessageBox.question(
            self,
            "确认固化",
            f"将用修复后的文件置换原始文件：\n"
            f"  章节: {entry_id}\n"
            f"  策略: {self._strategy}\n"
            f"  原始文件将备份为 .bak\n\n"
            f"⚠ 破坏性操作 — 固化后需重新对齐/校订。\n\n"
            f"确认执行？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        result = promote_repaired(
            entry_id=entry_id,
            src_path=src_path,
            tgt_path=tgt_path,
            repaired_dir=repaired_dir,
            strategy=self._strategy,
        )

        if result["success"]:
            QMessageBox.information(
                self,
                "固化完成",
                "✓ 文件已替换\n" "✓ 原始文件已备份\n" "✓ 缓存已清除",
            )
        else:
            QMessageBox.warning(self, "固化失败", result["message"])

    # ── 欢迎页帮助链接回调 ──

    def _on_open_welcome_guide(self, page: str):
        """欢迎页「快速开始」卡片的帮助链接。"""
        if page == "user-guide":
            self._on_open_gui_guide()
        elif page == "model-setup":
            self._on_open_agent_config()
        elif page == "algorithm":
            alg_path = self._resolve_doc_path("docs/algorithm.md")
            if alg_path and os.path.isfile(alg_path):
                from dualign.gui.markdown_viewer import MarkdownViewer

                viewer = MarkdownViewer(alg_path, "对齐算法说明", self)
                viewer.exec()
            else:
                QMessageBox.information(
                    self,
                    "算法说明",
                    "算法说明文档未找到。",
                )

    # ── 帮助菜单回调 ──

    def _on_open_gui_guide(self):
        """打开内嵌 GUI 使用指南文档查看器。"""
        guide_path = self._resolve_doc_path("docs/user-guide.md")
        if guide_path and os.path.isfile(guide_path):
            from dualign.gui.markdown_viewer import MarkdownViewer

            viewer = MarkdownViewer(guide_path, "GUI 使用指南", self)
            viewer.exec()
        else:
            QMessageBox.information(
                self,
                "使用指南",
                "GUI 使用指南未找到。\n\n请访问 GitHub 仓库查看在线文档。",
            )

    def _on_open_docs_folder(self):
        """在文件管理器中打开文档文件夹。"""
        docs_dir = self._resolve_doc_path("docs")
        if docs_dir and os.path.isdir(docs_dir):
            os.startfile(docs_dir)
        else:
            QMessageBox.information(
                self,
                "文档文件夹",
                "文档文件夹未找到。",
            )

    def _resolve_doc_path(self, relative_path: str) -> Optional[str]:
        """解析文档路径。

        优先级：
          1. 项目根目录下的相对路径（开发模式）
          2. PyInstaller 打包后的 _MEIPASS 目录
          3. 可执行文件所在目录
        """
        # PyInstaller 打包后资源在 sys._MEIPASS
        base = getattr(sys, "_MEIPASS", None)
        if base:
            candidate = os.path.join(base, relative_path)
            if os.path.exists(candidate):
                return candidate
            # 打包后文档可能在 _MEIPASS/docs 或 _MEIPASS/../docs
            alt = os.path.join(os.path.dirname(base), relative_path)
            if os.path.exists(alt):
                return alt

        # 开发模式：项目根目录 (__file__ = .../src/dualign/gui/window.py)
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        candidate = os.path.join(str(project_root), relative_path)
        if os.path.exists(candidate):
            return candidate

        return None

    def _on_about(self):
        """显示关于对话框。"""
        from dualign import __version__
        from dualign.gui.dialogs import AboutDialog

        dlg = AboutDialog(__version__, self)
        dlg.exec()

    def _on_reset_settings(self):
        """恢复所有 GUI 选项到出厂默认值（不影响模型/Agent 配置）。"""
        ret = QMessageBox.question(
            self,
            "恢复默认设置",
            "确定要将所有选项恢复到出厂默认值吗？\n（模型与 Agent 配置不受影响）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        from dualign.gui.settings import DualignConfig

        cfg = DualignConfig.instance()
        cfg.load()

        # ── 清除除模型/Agent 配置外的所有选项 ──
        cfg.clear_all()
        # 写入出厂默认值
        for k, v in DualignConfig.default_values().items():
            cfg.set(k, v)
        cfg.save()

        # ── 实时应用默认值到 UI 控件 ──
        fp = self._filter_panel
        defaults = DualignConfig.default_values()

        # 筛选面板
        fp._anomaly_only_cb.setChecked(not defaults[KEY_SHOW_ALL])
        fp._context_spin.setValue(defaults[KEY_CONTEXT_LINES])
        fp._show_scores_cb.setChecked(
            defaults[KEY_COMPACT_GRID]
        )  # False = 不显示评分列
        # 原始特征：全选
        for cb in fp._origin_checks.values():
            cb.setChecked(True)
        # 处理状态：全选
        for cb in fp._state_checks.values():
            cb.setChecked(True)

        # 修复策略
        self._review.set_strategy_index(defaults[KEY_STRATEGY])

        # 表格刷新
        fp.filter_changed.emit()
        self._ensure_table_in_stacked()
        self._refresh()

        # 重置布局
        self._on_reset_layout()

        self._safe_status("✅ 已恢复默认设置")

    def _on_toggle_panel(self, panel_id: str, checked: bool):
        """菜单 → 面板显隐切换。"""
        dock = self._dock_map.get(panel_id)
        if dock:
            dock.setVisible(checked)
            if checked:
                dock.raise_()
        # 同步菜单勾选状态
        self._sync_panel_menu_checks()
        self._debounce_save_history()

    def _toggle_left_dock_area(self):
        """切换左侧 Dock 区域显隐。

        仅操作 dockWidgetArea 为 Left 的面板（审校/文件移到对侧后不受 Ctrl+B 影响）。
        收纳时保存宽度，展开时恢复。
        """
        left_ids = [
            pid
            for pid in ["review", "files"]
            if self._dock_map.get(pid)
            and self.dockWidgetArea(self._dock_map[pid]) == Qt.LeftDockWidgetArea
        ]
        any_visible = any(self._dock_map[pid].isVisible() for pid in left_ids)
        if any_visible:
            for pid in left_ids:
                dock = self._dock_map.get(pid)
                if dock and dock.isVisible() and not dock.isFloating():
                    w = dock.width()
                    if w > 40:
                        self._saved_dock_widths[pid] = w
                    dock.hide()
        else:
            # 展开：先全部显示
            for pid in left_ids:
                dock = self._dock_map.get(pid)
                if dock:
                    dock.setVisible(True)
                    dock.raise_()
            # 恢复宽度
            saved = self._saved_dock_widths.get("review", 360)
            QTimer.singleShot(50, lambda: self._restore_dock_width("review", saved))
        self._sync_panel_menu_checks()
        self._debounce_save_history()

    def _toggle_aux_dock(self):
        """切换右侧 Dock 区域显隐（Ctrl+Alt+B）。

        右侧无 dock → 无操作；有 dock → 一揽子切换显隐。
        """
        right_ids = [
            pid
            for pid, dock in self._dock_map.items()
            if self.dockWidgetArea(dock) == Qt.RightDockWidgetArea
        ]
        if not right_ids:
            return
        any_visible = any(self._dock_map[pid].isVisible() for pid in right_ids)
        if any_visible:
            for pid in right_ids:
                self._dock_map[pid].hide()
        else:
            for pid in right_ids:
                dock = self._dock_map[pid]
                dock.setVisible(True)
                dock.raise_()
        self._update_min_width()
        self._debounce_save_history()

    def _restore_dock_width(self, panel_id: str, width: int):
        """恢复 dock 宽度（在 show + layout settle 后调用）。"""
        dock = self._dock_map.get(panel_id)
        if dock and not dock.isFloating():
            self.resizeDocks([dock], [width], Qt.Orientation.Horizontal)

    def _sync_panel_menu_checks(self):
        """同步视图菜单中面板勾选状态与实际可见性。"""
        for pid, menu in [
            ("review", getattr(self, "_menu_toggle_review", None)),
            ("files", getattr(self, "_menu_toggle_files", None)),
        ]:
            if menu is None:
                continue
            dock = self._dock_map.get(pid)
            if dock is None:
                continue
            try:
                menu.blockSignals(True)
                menu.setChecked(dock.isVisible())
                menu.blockSignals(False)
            except RuntimeError:
                # QAction 已被删除（菜单栏重建后），忽略
                pass

    def _cfg(self) -> DualignConfig:
        cfg = DualignConfig.instance()
        cfg.load()
        return cfg

    def _debounce_save_history(self):
        if hasattr(self, "_history_timer"):
            self._history_timer.stop()
        self._history_timer = QTimer(self)
        self._history_timer.setSingleShot(True)
        self._history_timer.timeout.connect(self._save_history)
        self._history_timer.start(500)

    def _save_history(self):
        try:
            cfg = DualignConfig.instance()
            cfg.load()
            fp = self._filter_panel
            cfg.set(KEY_STRATEGY, self._review.get_strategy_index())
            cfg.set(KEY_SHOW_ALL, fp.show_all)
            cfg.set(KEY_CONTEXT_LINES, fp.context_lines)

            cfg.set(KEY_COMPACT_GRID, fp.show_scores)
            cfg.set(KEY_SHOW_HANDLED, fp.show_handled)
            cfg.set(
                KEY_CROSS_GROUP_OP,
                "AND" if fp._cross_group_combo.currentText() == "交集" else "OR",
            )
            cfg.set(KEY_ANOMALY_TYPES, sorted(fp.active_origin_keys))
            cfg.set(KEY_APPROVAL_STATES, sorted(fp.active_state_keys))
            # AI 审校偏好
            cfg.set("ai_backend", self._review._backend)
            cfg.set("ai_auto_approve", self._review._auto_approve_enabled)
            # Dock 布局持久化（停靠区域、浮动状态、宽度）
            # 使用 QMainWindow.saveState 二进制序列化，base64 编码存于 JSON
            _state = self.saveState()
            if _state:
                cfg.set("dock_state", _state.toBase64().data().decode("ascii"))
            # 最近打开目录
            cfg.set(KEY_LAST_OPEN_DIR, self._last_open_dir)
            # 拆分布局偏好（按全屏/非全屏分别保存）
            import PySide6.QtCore as _QtCore

            is_full = self.windowState() & _QtCore.Qt.WindowState.WindowFullScreen
            key = "split_layout_fullscreen" if is_full else "split_layout_normal"
            cfg.set(key, self._single_column_active)
            # 底部面板档位
            cfg.set("bottom_ratio", _BOTTOM_RATIOS[self._bottom_snap_idx])
            cfg.save()
        except Exception:
            import traceback as _tb

            _tb.print_exc()

    def _load_history(self) -> dict:
        """兼容旧接口：返回 DualignConfig 的全部数据。"""
        return self._cfg()._data

    def _restore_filter_state(self, history: dict):
        """从配置恢复筛选面板/显示选项状态。缺省键用出厂默认值。"""
        fp = self._filter_panel
        defaults = DualignConfig.default_values()

        def _v(key: str):
            return history[key] if key in history else defaults.get(key)

        fp._anomaly_only_cb.setChecked(not _v(KEY_SHOW_ALL))
        # 初始同步上下文控件的灰显状态
        fp._sync_anomaly_only_controls()
        fp._context_spin.setValue(_v(KEY_CONTEXT_LINES))

        fp._show_scores_cb.setChecked(_v(KEY_COMPACT_GRID))
        cross = _v(KEY_CROSS_GROUP_OP)
        # 兼容存储格式：配置中存 "AND"/"OR"，combo 用 "交集"/"并集"
        _cross_map = {"AND": "交集", "OR": "并集", "交集": "交集", "并集": "并集"}
        idx = fp._cross_group_combo.findText(_cross_map.get(cross, "交集"))
        if idx >= 0:
            fp._cross_group_combo.setCurrentIndex(idx)
        fp.set_show_handled(bool(_v(KEY_SHOW_HANDLED)))

        saved_origin = set(history.get(KEY_ANOMALY_TYPES, [])) or set(
            defaults.get(KEY_ANOMALY_TYPES, [])
        )
        for key, cb in fp._origin_checks.items():
            cb.setChecked(key in saved_origin)
        saved_state = set(history.get(KEY_APPROVAL_STATES, [])) or set(
            defaults.get(KEY_APPROVAL_STATES, [])
        )
        for key, cb in fp._state_checks.items():
            cb.setChecked(key in saved_state)

    def _restore_layout(self, history: dict):
        """从配置恢复布局，含拆分布局偏好。"""
        import PySide6.QtCore as _QtCore

        is_full = self.windowState() & _QtCore.Qt.WindowState.WindowFullScreen
        key = "split_layout_fullscreen" if is_full else "split_layout_normal"
        split_on = history.get(key, False)
        if split_on:
            from dualign.gui.panels import DockPanelHelper

            DockPanelHelper.toggle_single_column(self)

    def _on_reset_layout(self):
        """重置布局 + 全部筛选/显示选项到出厂默认值。"""
        # 先退出单栏模式（拆除 QSplitter 容器、恢复控件树、恢复 QTabBar）
        if self._single_column_active:
            from dualign.gui.panels import DockPanelHelper

            DockPanelHelper.toggle_single_column(self)

        for pid in ("files", "review"):
            d = self._dock_map.get(pid)
            if d is not None:
                d.setFloating(False)

        # 先移除再重新添加，确保 Qt 停靠状态完全刷新
        for pid in ("files", "review"):
            d = self._dock_map.get(pid)
            if d is not None:
                self.removeDockWidget(d)
        self._dock_map["review"].setVisible(True)
        self._dock_map["files"].setVisible(True)
        self.addDockWidget(Qt.LeftDockWidgetArea, self._dock_map["files"])
        self.addDockWidget(Qt.LeftDockWidgetArea, self._dock_map["review"])
        self.tabifyDockWidget(self._dock_map["review"], self._dock_map["files"])
        self._dock_map["review"].raise_()

        # ── 恢复面板宽度 ──
        l_dock = self._dock_map.get("review")
        if l_dock and not l_dock.isFloating():
            self.resizeDocks([l_dock], [300], Qt.Orientation.Horizontal)

        # ── 展开底部面板 ──
        if self._bottom_content.isHidden():
            self._toggle_bottom_panel()

        # ── 恢复表格列宽默认 ──
        hdr = self.table.horizontalHeader()
        from dualign.gui.base_table import calc_snap_width as _csw3

        hdr.resizeSection(0, _csw3(0))
        for ci, w in enumerate([64, 60, 64, 60], 1):
            hdr.resizeSection(ci, w)

        # ── 将所有配置键重置为出厂默认值 ──
        defaults = DualignConfig.default_values()
        cfg = DualignConfig.instance()
        cfg.load()
        for key in defaults:
            cfg.set(key, defaults[key])
        # AI 审校偏好也有默认值
        cfg.set("ai_backend", "deepseek")
        cfg.set("ai_auto_approve", False)
        cfg.save()
        # 刷新筛选面板 UI 状态
        self._restore_filter_state(cfg._data)
        self._review.set_strategy_index(defaults.get(KEY_STRATEGY, 1))
        self._review.set_backend("deepseek")
        self._apply_filter()

        # ── 未最大化时根据当前 dock 布局调整窗口尺寸 ──
        if not self.isMaximized():
            self._update_min_width()

        self._safe_status("布局与显示设置已重置")

    def closeEvent(self, event):
        # 停止后台线程
        try:
            self._cancel_current_load()
        except Exception:
            import traceback as _tb

            _tb.print_exc()
        # ScoreManager 清理（停止 worker 线程）
        try:
            if hasattr(self, "_score_mgr") and self._score_mgr is not None:
                self._score_mgr.cleanup()
        except Exception:
            import traceback as _tb

            _tb.print_exc()
        # 解除 eventFilter 防止 C++ 对象已析构后仍被 atexit 调用
        try:
            self.table.viewport().removeEventFilter(self)
        except Exception:
            import traceback as _tb

            _tb.print_exc()
        self._save_history()
        self._save_session()
        super().closeEvent(event)

    def _update_doc_summary(self):
        """更新文档摘要。

        row0: 原文：完整文件名（超链接）
        row1: 译文：完整文件名（超链接）
        row2: 原文行数 | 译文行数 | Snap均分
        row3: 真锚点率 | 间隙行率 | 合并触顶
        末尾：章节进度
        """
        if self._repair_state is None:
            self._review.set_summary_paths("", "")
            self._review.set_summary_filename("—")
            self._review.set_summary_cells()
            return

        state = self._repair_state
        snap = state.snapshot
        n_src = len(snap.original_src_lines)
        n_tgt = len(snap.original_tgt_lines)

        # ── 原文/译文路径 + 章节进度 ──
        src_path = getattr(self, "_src_path", "")
        tgt_path = getattr(self, "_tgt_path", "")
        self._review.set_summary_paths(src_path, tgt_path)
        chapter_text = ""
        entry = getattr(self, "_current_entry", None)
        entries = getattr(self, "_entries", None)
        if isinstance(entries, list) and entry and entry in entries:
            try:
                idx = entries.index(entry)
                chapter_text = f"{idx+1}/{len(entries)}章"
            except ValueError:
                pass
        self._review.set_summary_filename("", chapter_text)

        # 指标
        stats = getattr(self, "_align_stats", None) or {}
        qa = getattr(self, "_last_quality_assessment", None)
        indicators = qa.get("indicators", {}) if qa else {}
        # Snap均分
        avg_sim = stats.get("avg_similarity", 0)
        avg_pct = f"{avg_sim:.1%}" if avg_sim else "—"

        # 真锚点率 = 参与锚点的去重行数 / 总行数
        n_true_anchors = stats.get("n_true_anchors", 0)
        n_denom = n_src + n_tgt
        anchor_ratio = n_true_anchors / n_denom if n_denom > 0 else 0
        anchor_pct = f"{anchor_ratio:.1%}"

        # 间隙行率
        gap_ratio = indicators.get("gap_row_ratio", 0)
        gap_pct = f"{gap_ratio:.1%}" if gap_ratio else "—"

        # 合并触顶（0 显示 0，不隐藏）
        n_overflow = indicators.get("n_overflow_rows", 0) or stats.get(
            "n_overflow_rows", 0
        )

        self._review.set_summary_cells(
            f"原文行数：{n_src}",
            f"译文行数：{n_tgt}",
            f"Snap均分：{avg_pct}",
            f"真锚点率：{anchor_pct}",
            f"间隙行率：{gap_pct}",
            f"合并触顶：{n_overflow}",
        )

    def _update_status_bar(self):
        """更新状态栏（当前无持久摘要 — 最后一条日志自动显示）。"""
