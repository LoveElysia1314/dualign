"""
Dualign — WelcomePage: 空状态欢迎引导页

当没有加载任何文件时显示，引导用户完成：
  1. 检查嵌入服务 + AI 审校 Agent 状态
  2. 打开文件对或 Demo 开始使用
"""

from __future__ import annotations

from typing import List
from dataclasses import dataclass

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QFrame,
    QGridLayout,
)
from dualign.gui.workers import EnvCheckThread
from dualign.gui.status_bar import StatusDot
from dualign.gui.theme import T
from dualign.gui.theme import T as _themeT

# ═══════════════════════════════════════════════════════════════
# 环境状态
# ═══════════════════════════════════════════════════════════════


@dataclass
class EnvStatus:
    embed_ok: bool = False
    embed_detail: str = "未检测"
    embed_provider: str = ""
    embed_model: str = ""
    models_available: List[str] = None
    ai_ok: bool = False
    ai_detail: str = "未配置"

    def __post_init__(self):
        if self.models_available is None:
            self.models_available = []


# ═══════════════════════════════════════════════════════════════
# QuickActionBtn — 快速操作按钮（带复制功能）
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# StatusDot — 导入自 status_bar（去重）
# ═══════════════════════════════════════════════════════════════
# WelcomePage
# ═══════════════════════════════════════════════════════════════


class WelcomePage(QWidget):
    """空状态欢迎页 — 引导用户完成配置和开始使用。"""

    open_files_requested = Signal()
    open_demo_requested = Signal()
    open_agent_config_requested = Signal()
    batch_discover_requested = Signal()
    open_guide_requested = Signal(str)  # 帮助/文档页名
    # 信号: (label, src_path, tgt_path)
    recent_file_clicked = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._quick_card: QFrame | None = None
        self._env_card: QFrame | None = None
        self._env_thread: EnvCheckThread | None = None
        self._build_ui()
        _themeT.theme_changed.connect(self._on_theme_changed)
        QTimer.singleShot(500, self.refresh_env)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(0)

        center = QVBoxLayout()
        center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.setSpacing(12)

        # ── 程序图标 + 标题区（图标左侧，三行文本右侧，整体居中）──
        import sys as _sys

        _svg_path: str | None = None
        if getattr(_sys, "frozen", False) and hasattr(_sys, "_MEIPASS"):
            _c = Path(_sys._MEIPASS) / "assets" / "branding" / "dualign-outline.svg"
            if _c.is_file():
                _svg_path = str(_c)
        if _svg_path is None:
            _c = (
                Path(__file__).parents[3]
                / "assets"
                / "branding"
                / "dualign-outline.svg"
            )
            if _c.is_file():
                _svg_path = str(_c)

        hero = QHBoxLayout()
        hero.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero.setSpacing(18)

        if _svg_path:
            # 图标容器：底部对齐，解决图标高于文本的问题
            icon_wrap = QVBoxLayout()
            icon_wrap.setContentsMargins(0, 0, 0, 0)
            icon_wrap.setSpacing(0)
            icon_wrap.addStretch()
            logo = QSvgWidget(_svg_path)
            logo.setFixedSize(100, 100)
            logo.setStyleSheet("background: transparent;")
            icon_wrap.addWidget(logo, 0, Qt.AlignmentFlag.AlignBottom)
            hero.addLayout(icon_wrap)

        # ── 文本（右侧，底部对齐）──
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        text_col.addStretch()

        title = QLabel("Dualign Studio")
        tf = QFont()
        tf.setPointSize(40)
        tf.setBold(True)
        title.setFont(tf)
        text_col.addWidget(title)

        subtitle = QLabel("平行文档对齐与 Agent 辅助审校工作台")
        sf = QFont()
        sf.setPointSize(16)
        subtitle.setFont(sf)
        text_col.addWidget(subtitle)

        hero.addLayout(text_col)
        center.addLayout(hero)

        center.addSpacing(6)

        # ── 双栏主体 ──
        cols = QHBoxLayout()
        cols.setSpacing(14)

        # 左栏：快速开始
        self._quick_card = self._build_quick_start_card()
        cols.addWidget(self._quick_card, 1)

        # 右栏：运行环境
        self._env_card = self._build_env_card()
        cols.addWidget(self._env_card, 1)

        center.addLayout(cols)

        # ── 整体垂直居中布局（root 内上下 stretch + 底部 credit）──
        root.addStretch()
        root.addLayout(center)
        root.addStretch()

        # ── Powered by 署名（右下角，距底边 8px）──
        credit_row = QHBoxLayout()
        credit_row.setContentsMargins(0, 0, 16, 8)
        credit_row.addStretch()
        credit = QLabel(
            'Powered by <a href="https://github.com/LoveElysia1314" '
            'style="text-decoration:none;">LoveElysia1314</a>'
        )
        credit.setOpenExternalLinks(True)
        cf = QFont()
        cf.setPointSize(12)
        credit.setFont(cf)
        credit.setStyleSheet(
            "a {color: palette(link);} a:hover {color: palette(link);}"
        )
        credit_row.addWidget(credit)
        root.addLayout(credit_row)

    # ── 快速启动卡片 ──

    def _on_theme_changed(self, _scheme: str):
        """主题切换时刷新卡片样式。"""
        if self._quick_card is not None:
            self._refresh_card_style(self._quick_card)
        if self._env_card is not None:
            self._refresh_card_style(self._env_card)

    def _refresh_card_style(self, card: QFrame):
        """刷新卡片 QSS（主题切换后重新应用当前主题色）。"""
        card.setStyleSheet(
            f"QFrame#{card.objectName()}{{background:{T.BG_PANEL};border:1px solid {T.BORDER_DIM};border-radius:10px;}}"
        )

    def _build_quick_start_card(self) -> QFrame:
        """构建快速开始卡片。"""
        card = QFrame()
        card.setFixedSize(320, 200)
        card.setObjectName("quick_card")
        self._refresh_card_style(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)

        header = QLabel("🚀 快速开始")
        header.setStyleSheet("font-weight: bold;")
        layout.addWidget(header)

        # ── 3×2 按钮网格 ──
        grid = QGridLayout()
        grid.setSpacing(6)

        buttons = [
            ("🎮 体验 Demo", self.open_demo_requested),
            ("📂 打开文件对", self.open_files_requested),
            ("📂 批量发现文件对", self.batch_discover_requested),
            ("🔧 模型设置", self.open_agent_config_requested),
            ("📖 用户指南", "user-guide"),
            ("📚 算法说明", "algorithm"),
        ]
        for idx, (text, action) in enumerate(buttons):
            btn = QPushButton(text)
            btn.setMinimumHeight(28)
            if isinstance(action, str):
                page = action
                btn.clicked.connect(
                    lambda checked, _p=page: self.open_guide_requested.emit(_p)
                )
            else:
                signal = action
                btn.clicked.connect(signal.emit)
            grid.addWidget(btn, idx // 2, idx % 2)

        layout.addLayout(grid)

        # ── 分隔 ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"border:none;border-top:1px solid {T.BORDER_DIM};")
        layout.addWidget(sep)

        # ── 最近项目 ──
        recent_row = QHBoxLayout()
        recent_row.setSpacing(4)
        recent_row.addWidget(QLabel("最近项目"))
        self._recent_combo = QComboBox()
        self._recent_combo.addItem("📋 选择最近打开的文件对...")
        self._recent_combo.currentIndexChanged.connect(self._on_recent_selected)
        recent_row.addWidget(self._recent_combo, 1)
        layout.addLayout(recent_row)

        return card

    def set_recent_pairs(self, pairs: list):
        """设置最近文件对列表 [(label, src, tgt), ...]"""
        self._recent_pairs = list(pairs)
        self._recent_combo.blockSignals(True)
        self._recent_combo.clear()
        self._recent_combo.addItem("📋 选择最近打开的文件对...")
        for lb, s, t in pairs:
            self._recent_combo.addItem(f"{lb}")
        self._recent_combo.blockSignals(False)

    def _on_recent_selected(self, idx: int):
        if (
            idx <= 0
            or not hasattr(self, "_recent_pairs")
            or idx - 1 >= len(self._recent_pairs)
        ):
            return
        lb, s, t = self._recent_pairs[idx - 1]
        self.recent_file_clicked.emit(lb, s, t)

    # ── 环境卡片 ──

    def _build_env_card(self) -> QFrame:
        card = QFrame()
        card.setFixedSize(320, 200)
        card.setObjectName("env_card")
        self._refresh_card_style(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        header = QLabel("🔧 运行环境")
        header.setStyleSheet("font-weight: bold;")
        layout.addWidget(header)

        self._embed_dot = StatusDot("词嵌入模型")
        layout.addWidget(self._embed_dot)

        self._ai_dot = StatusDot("大语言模型")
        layout.addWidget(self._ai_dot)

        # ── 动态引导消息 ──
        self._action_area = QVBoxLayout()
        self._action_area.setSpacing(4)
        layout.addLayout(self._action_area)

        # ── 分隔 ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"border:none;border-top:1px solid {T.BORDER_DIM};")
        layout.addWidget(sep)

        # ── 底部行：设置 + 检测 + 模型数 ──
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)

        setup_btn = QPushButton("⚙️ 设置")
        setup_btn.setFixedHeight(24)
        setup_btn.clicked.connect(self.open_agent_config_requested.emit)
        bottom_row.addWidget(setup_btn)

        self._refresh_btn = QPushButton("🔄 检测")
        self._refresh_btn.setFixedHeight(24)
        self._refresh_btn.clicked.connect(self.refresh_env)
        bottom_row.addWidget(self._refresh_btn)

        bottom_row.addStretch()
        layout.addLayout(bottom_row)

        return card

    # ── 环境检测 ──

    def refresh_env(self):
        """异步启动环境检测，不阻塞主线程。"""
        # 立即显示"检测中..."状态
        self._embed_dot.set_ok(None)
        self._embed_dot.set_text("词嵌入模型 — 检测中…")
        self._ai_dot.set_ok(None)
        self._ai_dot.set_text("大语言模型 — 检测中…")
        self._show_loading_message("⏳ 正在检测运行环境…")

        # 避免重复启动线程
        if self._env_thread is not None and self._env_thread.isRunning():
            return

        self._env_thread = EnvCheckThread(self)
        self._env_thread.env_checked.connect(self._on_env_check_result)
        self._env_thread.start()

    def _on_env_check_result(self, result: dict):
        """后台检测线程完成后的回调。"""
        status = EnvStatus(
            embed_ok=result.get("embed_ok", False),
            embed_detail=result.get("embed_detail", "未知"),
            embed_provider=result.get("embed_provider", ""),
            embed_model=result.get("embed_model", ""),
            ai_ok=result.get("ai_ok", False),
            ai_detail=result.get("ai_detail", "未知"),
            models_available=result.get("models_available", []),
        )
        self._apply_status(status)

    def _apply_status(self, status: EnvStatus):
        """将检测结果应用到 UI，并生成快速操作按钮。"""
        # ── 词嵌入模型 ──
        self._embed_dot.set_ok(status.embed_ok)
        if status.embed_ok:
            _m = status.embed_model or "就绪"
            self._embed_dot.set_text(f"词嵌入模型: {_m}")
        else:
            first_line = status.embed_detail.split(chr(10))[0]
            self._embed_dot.set_text(f"词嵌入模型 — {first_line}")

        # ── 大语言模型 ──
        self._ai_dot.set_ok(status.ai_ok)
        if status.ai_ok:
            self._ai_dot.set_text(f"大语言模型: {status.ai_detail}")
        else:
            self._ai_dot.set_text("大语言模型 · 未配置（可选）")

        # ── 快速操作按钮 ──
        self._populate_actions(status)

    def set_aligning(self, phase: str = ""):
        """对齐过程中显示进度（替代环境状态引导消息）。"""
        self._clear_layout(self._action_area)
        lbl = QLabel(f"⏳ {phase}")
        lbl.setStyleSheet(f"color: {T.ORANGE}; padding: 2px 0;")
        self._action_area.addWidget(lbl)

    def _show_loading_message(self, msg: str):
        """显示临时加载消息。"""
        self._clear_layout(self._action_area)
        lbl = QLabel(msg)
        lbl.setStyleSheet(f"color: {T.ORANGE}; padding: 2px 0;")
        self._action_area.addWidget(lbl)

    def _populate_actions(self, status: EnvStatus):
        """根据环境状态在卡片内显示动态引导消息。"""
        self._clear_layout(self._action_area)

        if status.embed_ok and status.ai_ok:
            lbl = QLabel("✅ 环境就绪，可以开始使用")
            lbl.setStyleSheet(f"color: {T.GREEN}; padding: 2px 0;")
            self._action_area.addWidget(lbl)
        elif status.embed_ok and not status.ai_ok:
            lbl = QLabel("💡 嵌入已就绪，可配置大语言模型增强审校")
            lbl.setStyleSheet(f"color: {T.FG_SECONDARY}; padding: 2px 0;")
            self._action_area.addWidget(lbl)
        else:
            lbl = QLabel("⚠ 嵌入服务不可用，请检查模型设置")
            lbl.setStyleSheet(f"color: {T.ORANGE}; padding: 2px 0;")
            self._action_area.addWidget(lbl)

    @staticmethod
    def _clear_layout(layout):
        """递归清除布局中的所有子项。"""
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                WelcomePage._clear_layout(item.layout())

    # ── 点击处理（已移除 - StatusDot 不再可点击）──
