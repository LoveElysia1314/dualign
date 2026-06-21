"""
Dualign — MarkdownViewer: 内嵌 Markdown 文档查看器

用 QTextBrowser.setMarkdown() 渲染文档，无需额外依赖。
颜色完全由 Fusion QPalette 原生管理，不做任何覆盖。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTextBrowser,
    QWidget,
)


class MarkdownViewer(QDialog):
    """内嵌 Markdown 文档查看器对话框。

    读取 .md 文件并用 QTextBrowser.setMarkdown() 渲染。
    窗口记忆位置，支持深色主题。支持最大化，初始尺寸较宽裕。
    """

    def __init__(self, file_path: str, title: str = "文档", parent=None):
        super().__init__(parent, Qt.Window)  # Window 标志使对话框可最大化
        self._file_path = file_path
        self.setWindowTitle(f"Dualign — {title}")
        self.setMinimumSize(1080, 640)
        self.resize(1080, 720)

        self._build_ui()
        self._load_file()

    def _build_ui(self):
        """构建 UI，不设置任何颜色——全部由 Fusion QPalette 原生管理。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 标题栏 ──
        header = QWidget()
        header.setFixedHeight(32)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(10, 0, 10, 0)

        self._title_lbl = QLabel(Path(self._file_path).stem)
        self._title_lbl.setStyleSheet("font-weight:bold;")
        hl.addWidget(self._title_lbl, 1)

        self._page_lbl = QLabel("")
        hl.addWidget(self._page_lbl)

        self._ext_btn = QPushButton("🔗 外部打开")
        self._ext_btn.clicked.connect(self._open_external)
        hl.addWidget(self._ext_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)
        hl.addWidget(close_btn)

        layout.addWidget(header)

        # ── 文档正文 —— QTextBrowser 自动继承 QPalette，无需 setStyleSheet ──
        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.anchorClicked.connect(self._on_anchor_clicked)
        self._browser.document().setDefaultStyleSheet(
            "body{line-height:1.6;}"
            "pre{padding:12px;border-radius:4px;}"
            "code{padding:1px 4px;border-radius:3px;}"
            "pre code{padding:0;}"
            "blockquote{border-left:3px solid palette(link);margin:8px 0;padding:4px 12px;}"
            "table{border-collapse:collapse;width:100%;}"
            "th,td{padding:6px 10px;text-align:left;}"
            "hr{border:none;}"
        )
        layout.addWidget(self._browser, 1)

    def _load_file(self):
        """读取 Markdown 文件并渲染。"""
        if not os.path.isfile(self._file_path):
            self._browser.setPlainText(f"文件未找到: {self._file_path}")
            return
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                md = f.read()
            self._browser.setMarkdown(md)
            # 统计标题数（简化锚点导航）
            headings = re.findall(r"^#{1,3}\s+", md, re.MULTILINE)
            if headings:
                self._page_lbl.setText(f"📄 {len(headings)} 节")
        except Exception as e:
            self._browser.setPlainText(f"读取失败: {e}")

    def _open_external(self):
        """用系统默认打开器在外部打开。"""
        if os.path.isfile(self._file_path):
            os.startfile(self._file_path)

    def _on_anchor_clicked(self, url: QUrl):
        """处理文档内链接点击。

        - http/https → 系统默认浏览器打开
        - 相对路径 → 解析为当前文档所在目录的绝对路径，加载该文件
        - 锚点(#section) → 跳过，让 QTextBrowser 原生处理
        """
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
            return

        # 纯锚点跳转 → 由 QTextBrowser 原生处理
        if not url.path():
            return

        # 相对链接：相对当前文档目录解析
        base_dir = os.path.dirname(self._file_path)
        resolved = os.path.normpath(os.path.join(base_dir, url.path()))
        if os.path.isfile(resolved) and resolved.lower().endswith(".md"):
            self._file_path = resolved
            self.setWindowTitle(f"Dualign — {Path(resolved).stem}")
            if hasattr(self, "_title_lbl"):
                self._title_lbl.setText(Path(resolved).stem)
            self._load_file()
        else:
            self._browser.setPlainText(f"文件未找到: {resolved}")
