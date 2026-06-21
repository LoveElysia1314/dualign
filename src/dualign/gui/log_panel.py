"""
Dualign — LogPanel: 全局运行日志面板

可折叠的滚动日志面板，支持日志级别过滤。
置于左侧面板底部，与审校操作解耦。
"""

from __future__ import annotations

import time
import logging
from typing import List, Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QSizePolicy,
    QLabel,
    QComboBox,
)

from dualign.gui.theme import T

# ═══════════════════════════════════════════════════════════════
# 日志级别定义
# ═══════════════════════════════════════════════════════════════

LOG_LEVELS = {
    "ALL": 0,
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

LEVEL_COLORS = {
    logging.DEBUG: "#78909C",  # 灰蓝
    logging.INFO: "#B0BEC5",  # 浅灰
    logging.WARNING: "#FFA726",  # 橙色
    logging.ERROR: "#e53935",  # 红色
    logging.CRITICAL: "#FF1744",  # 深红
}

LEVEL_SHORT = {
    logging.DEBUG: "DBG",
    logging.INFO: "INF",
    logging.WARNING: "WRN",
    logging.ERROR: "ERR",
    logging.CRITICAL: "CRT",
}

DEFAULT_LEVEL = "INFO"  # 默认显示 INFO 及以上


class LogPanel(QWidget):
    """可折叠的滚动日志面板，支持日志级别过滤。

    用法:
        log_panel.log("编码中...")
        log_panel.log("AI 分析完成", role="success")
        log_panel.log("连接失败", role="error")
        log_panel.log_structured(level=logging.INFO, module="dualign.core", message="对齐完成")
    """

    message_logged = Signal(str, str)  # (html_message, role)
    plain_text_logged = Signal(str)  # 纯文本消息（无时间戳），供 StatusBar 使用
    info_logged = Signal(str)  # INFO 级别纯文本消息，供 StatusBar 使用
    # 跨线程安全日志信号：_queued_log(level, module, message)
    _queued_log = Signal(int, str, str)

    _ROLE_COLORS = {
        "info": "#B0BEC5",
        "success": "#4CAF50",
        "error": "#e53935",
        "warning": "#FFA726",
        "system": "#64B5F6",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines: List[str] = []  # 全部日志 (HTML)
        self._line_levels: List[int] = []  # 每条日志的级别
        self._max_lines = 500
        self._filter_level = LOG_LEVELS[DEFAULT_LEVEL]

        # 跨线程安全：_queued_log 信号在队列中延迟处理
        self._queued_log.connect(
            self._on_queued_log, Qt.ConnectionType.QueuedConnection
        )

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(0)
        self.setMinimumHeight(40)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 标题栏（隐藏标题文本，仅保留控件，避免与 QGroupBox 标题重复）──
        header = QHBoxLayout()
        header.setContentsMargins(4, 2, 4, 2)
        header.setSpacing(4)

        self._title_lbl = QLabel("")
        self._title_lbl.setStyleSheet("font-weight:bold;")
        header.addWidget(self._title_lbl)

        header.addStretch()

        # ── 日志级别下拉框 ──
        self._level_combo = QComboBox()
        self._level_combo.addItems(list(LOG_LEVELS.keys()))
        self._level_combo.setCurrentText(DEFAULT_LEVEL)
        self._level_combo.setFixedWidth(80)
        self._level_combo.currentTextChanged.connect(self._on_level_changed)
        header.addWidget(self._level_combo)

        self._clear_btn = QPushButton("清空")
        self._clear_btn.setFixedSize(40, 18)
        # Fusion palette handles clear button style
        self._clear_btn.clicked.connect(self.clear)
        header.addWidget(self._clear_btn)

        layout.addLayout(header)

        # ── 日志浏览器 ──
        self._browser = QTextBrowser()
        self._browser.setReadOnly(True)
        self._browser.setOpenExternalLinks(False)
        self._browser.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._browser.setMinimumHeight(36)
        pass  # Fusion palette handles border
        layout.addWidget(self._browser, 1)

    # ── 公开接口 ──

    def log(self, message: str, role: str = "info"):
        """追加一条旧版日志（兼容现有调用）。

        Args:
            message: 纯文本消息（自动 HTML 转义）
            role: 'info' | 'success' | 'error' | 'warning' | 'system'
        """
        ts = time.strftime("%H:%M:%S")
        color = self._ROLE_COLORS.get(role, "#B0BEC5")
        safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = f'<span style="color:{color}">[{ts}]</span> {safe}'
        self._append(html, level=logging.INFO if role == "info" else logging.WARNING)
        self.message_logged.emit(html, role)
        self.plain_text_logged.emit(message)
        if role == "info":
            self.info_logged.emit(message)

    def log_structured(self, level: int, module: str = "", message: str = ""):
        """追加一条结构化日志（供主线程直接调用）。

        Args:
            level: logging.DEBUG / INFO / WARNING / ERROR / CRITICAL
            module: 模块名（如 dualign.core）
            message: 日志消息
        """
        ts = time.strftime("%H:%M:%S")
        color = LEVEL_COLORS.get(level, "#B0BEC5")
        short = LEVEL_SHORT.get(level, "???")
        safe_msg = (
            str(message).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        html = (
            f'<span style="color:{T.FG_MUTED}">[{ts}]</span> '
            f'<span style="color:{color};font-weight:bold">[{short}]</span> '
            f'<span style="color:{T.FG_SECONDARY}">{module:.<28}</span> '
            f'<span style="color:{T.FG_PRIMARY}">{safe_msg}</span>'
        )
        self._append(html, level=level)
        self.plain_text_logged.emit(safe_msg)
        if level == logging.INFO:
            self.info_logged.emit(safe_msg)

    def queue_log(self, level: int, module: str, message: str):
        """线程安全入口：从任意线程排队日志到主线程处理。

        DualignLogHandler.emit() 可能在 worker 线程中运行，
        不能直接调用 QTextBrowser.setHtml()。此方法通过
        _queued_log 信号将日志排队到主线程的事件循环。
        """
        self._queued_log.emit(level, module, message)

    @Slot(int, str, str)
    def _on_queued_log(self, level: int, module: str, message: str):
        """_queued_log 信号的实际处理（主线程）。"""
        self.log_structured(level, module, message)

    def _append(self, html: str, level: int = logging.INFO):
        self._lines.append(html)
        self._line_levels.append(level)
        if len(self._lines) > self._max_lines:
            self._lines = self._lines[-self._max_lines :]
            self._line_levels = self._line_levels[-self._max_lines :]
        self._render()
        self._title_lbl.setText(f"📋 运行日志 ({len(self._lines)})")

    def _render(self):
        """渲染日志：根据过滤级别显示行。"""
        filtered = [
            line
            for line, lv in zip(self._lines, self._line_levels)
            if lv >= self._filter_level
        ]
        content = (
            "<br>".join(filtered)
            if filtered
            else (f'<span style="color:{T.FG_SECONDARY}">[清空] 等待新日志…</span>')
        )
        self._browser.setHtml(
            "<body style='font-family:Consolas,monospace;font-size:12px;"
            "line-height:1.3'>" + content + "</body>"
        )
        sb = self._browser.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def clear(self):
        """清空所有日志。"""
        self._lines.clear()
        self._line_levels.clear()
        self._render()
        self._title_lbl.setText("📋 运行日志 (0)")

    def _on_level_changed(self, text: str):
        self._filter_level = LOG_LEVELS.get(text, LOG_LEVELS[DEFAULT_LEVEL])
        self._render()
        n = sum(1 for lv in self._line_levels if lv >= self._filter_level)
        self._title_lbl.setText(f"📋 运行日志 ({n}/{len(self._lines)})")

    def sizeHint(self):
        from PySide6.QtCore import QSize

        return QSize(100, 160)


# ═══════════════════════════════════════════════════════════════
# DualignLogHandler — logging → GUI LogPanel 桥接
# ═══════════════════════════════════════════════════════════════

_gui_panel: Optional[object] = None  # LogPanel 实例


def set_gui_panel(panel):
    """注入 GUI LogPanel 实例。由 DualignWindow._build_ui 调用。"""
    global _gui_panel
    _gui_panel = panel


class DualignLogHandler(logging.Handler):
    """将 Python logging 消息重定向到 GUI LogPanel + 终端。

    线程安全：GUI 操作通过 QMetaObject.invokeMethod 排队到主线程，
    避免从 worker 线程直接调用 QTextBrowser.setHtml()。
    """

    def __init__(self, level=logging.DEBUG):
        super().__init__(level)
        self.setFormatter(logging.Formatter("%(levelname)-8s %(name)-28s %(message)s"))

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)

            # ── 终端输出 ──
            if record.levelno >= logging.WARNING:
                import sys

                print(msg, file=sys.stderr)
            else:
                print(msg)

            # ── GUI LogPanel（线程安全）──
            if _gui_panel is not None:
                try:
                    _gui_panel.queue_log(
                        record.levelno,
                        record.name,
                        record.getMessage(),
                    )
                except Exception:
                    pass
        except Exception:
            self.handleError(record)


def configure_root_logging():
    """初始化根日志系统。

    应在 QApplication 创建后、LogPanel 注入后调用。
    设置根 logger 使用 DualignLogHandler，清空默认 handler。
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # 移除已有的 handler（避免重复）
    for h in list(root.handlers):
        root.removeHandler(h)

    # 移除已有 StreamHandler（basicConfig 添加的）
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = DualignLogHandler(level=logging.DEBUG)
    root.addHandler(handler)

    # 静音第三方库的 DEBUG 日志
    for lib in ("urllib3", "requests", "httpx", "openai", "httpcore", "markdown_it"):
        logging.getLogger(lib).setLevel(logging.WARNING)
