"""
Dualign — 自适应主题管理器

双主题（Dark/Light），跟随系统自动切换。
控件颜色通过 Fusion QPalette 管理，自定义颜色通过 ThemeManager 动态访问。

用法:
    from dualign.gui.theme import T
    print(T.FG_PRIMARY)     # 当前主题的文字色
    print(T.BTN_DARK)       # 当前主题的按钮 QSS
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QPalette, QColor

# ═══════════════════════════════════════════════════════════════
# 单主题数据容器
# ═══════════════════════════════════════════════════════════════


@dataclass
class ThemeData:
    """一套主题的全部自定义颜色值。"""

    name: str = "dark"
    # 前景（文字）
    FG_PRIMARY: str = "#d4d4d4"
    FG_SECONDARY: str = "#888"
    FG_MUTED: str = "#555"
    FG_ACCENT: str = "#4fc3f7"
    # 状态色
    GREEN: str = "#4CAF50"
    RED: str = "#e53935"
    ORANGE: str = "#FFA726"
    YELLOW: str = "#F39C12"
    BLUE: str = "#4fc3f7"
    ACCENT: str = "#0e639c"
    # 结构色（用于必须手动 QSS 的场景）
    BG_DARK: str = "#1e1e1e"
    BG_PANEL: str = "#252526"
    BG_INPUT: str = "#2a2a2a"
    BG_HOVER: str = "#3a3a3a"
    BG_ACTIVE: str = "#4a4a4a"
    BORDER_DIM: str = "#555"
    BORDER_MUTED: str = "#666"
    BORDER_HOVER: str = "#777"
    BORDER_FOCUS: str = "#888"
    # 欢迎页专用
    WELCOME_TITLE: str = "#e0e0e0"
    WELCOME_SUBTITLE: str = "#888"
    WELCOME_VERSION: str = "#555"


DARK = ThemeData(
    name="dark",
    FG_PRIMARY="#d4d4d4",
    FG_SECONDARY="#888",
    FG_MUTED="#555",
    FG_ACCENT="#4fc3f7",
    GREEN="#4CAF50",
    RED="#e53935",
    ORANGE="#FFA726",
    YELLOW="#F39C12",
    BLUE="#4fc3f7",
    ACCENT="#0e639c",
    BG_DARK="#1e1e1e",
    BG_PANEL="#252526",
    BG_INPUT="#2a2a2a",
    BG_HOVER="#3a3a3a",
    BG_ACTIVE="#4a4a4a",
    BORDER_DIM="#555",
    BORDER_MUTED="#666",
    BORDER_HOVER="#777",
    BORDER_FOCUS="#888",
    WELCOME_TITLE="#e0e0e0",
    WELCOME_SUBTITLE="#888",
    WELCOME_VERSION="#555",
)

LIGHT = ThemeData(
    name="light",
    FG_PRIMARY="#2c2c2c",
    FG_SECONDARY="#666",
    FG_MUTED="#999",
    FG_ACCENT="#007acc",
    GREEN="#388E3C",
    RED="#D32F2F",
    ORANGE="#F57C00",
    YELLOW="#F9A825",
    BLUE="#007acc",
    ACCENT="#007acc",
    BG_DARK="#f3f3f3",
    BG_PANEL="#ffffff",
    BG_INPUT="#e8e8e8",
    BG_HOVER="#dcdcdc",
    BG_ACTIVE="#cccccc",
    BORDER_DIM="#a0a0a0",
    BORDER_MUTED="#808080",
    BORDER_HOVER="#666",
    BORDER_FOCUS="#007acc",
    WELCOME_TITLE="#2c2c2c",
    WELCOME_SUBTITLE="#666",
    WELCOME_VERSION="#999",
)


# ═══════════════════════════════════════════════════════════════
# 调色板工厂
# ═══════════════════════════════════════════════════════════════


def make_palette(is_dark: bool) -> QPalette:
    """创建 Fusion 暗色/亮色调色板。"""
    p = QPalette()
    if is_dark:
        p.setColor(QPalette.Window, QColor(30, 30, 30))
        p.setColor(QPalette.WindowText, QColor(212, 212, 212))
        p.setColor(QPalette.Base, QColor(42, 42, 42))
        p.setColor(QPalette.AlternateBase, QColor(37, 37, 38))
        p.setColor(QPalette.ToolTipBase, QColor(37, 37, 38))
        p.setColor(QPalette.ToolTipText, QColor(212, 212, 212))
        p.setColor(QPalette.Text, QColor(212, 212, 212))
        p.setColor(QPalette.Button, QColor(58, 58, 58))
        p.setColor(QPalette.ButtonText, QColor(212, 212, 212))
        p.setColor(QPalette.BrightText, QColor(255, 255, 255))
        p.setColor(QPalette.Highlight, QColor(14, 99, 156))
        p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        p.setColor(QPalette.Disabled, QPalette.Text, QColor(128, 128, 128))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(128, 128, 128))
        p.setColor(QPalette.Link, QColor(79, 195, 247))
    else:
        p.setColor(QPalette.Window, QColor(240, 240, 240))
        p.setColor(QPalette.WindowText, QColor(44, 44, 44))
        p.setColor(QPalette.Base, QColor(255, 255, 255))
        p.setColor(QPalette.AlternateBase, QColor(245, 245, 245))
        p.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
        p.setColor(QPalette.ToolTipText, QColor(44, 44, 44))
        p.setColor(QPalette.Text, QColor(44, 44, 44))
        p.setColor(QPalette.Button, QColor(232, 232, 232))
        p.setColor(QPalette.ButtonText, QColor(44, 44, 44))
        p.setColor(QPalette.BrightText, QColor(255, 255, 255))
        p.setColor(QPalette.Highlight, QColor(0, 122, 204))
        p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        p.setColor(QPalette.Disabled, QPalette.Text, QColor(153, 153, 153))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(153, 153, 153))
        p.setColor(QPalette.Link, QColor(0, 122, 204))
    return p


# ═══════════════════════════════════════════════════════════════
# 主题管理器
# ═══════════════════════════════════════════════════════════════


class ThemeManager(QObject):
    """主题管理器。所有颜色通过显式 @property 暴露。"""

    theme_changed = Signal(str)  # "dark" | "light"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: ThemeData = DARK

    # ── 颜色属性 ──
    @property
    def FG_PRIMARY(self) -> str:
        return self._data.FG_PRIMARY

    @property
    def FG_SECONDARY(self) -> str:
        return self._data.FG_SECONDARY

    @property
    def FG_MUTED(self) -> str:
        return self._data.FG_MUTED

    @property
    def FG_ACCENT(self) -> str:
        return self._data.FG_ACCENT

    @property
    def GREEN(self) -> str:
        return self._data.GREEN

    @property
    def RED(self) -> str:
        return self._data.RED

    @property
    def ORANGE(self) -> str:
        return self._data.ORANGE

    @property
    def YELLOW(self) -> str:
        return self._data.YELLOW

    @property
    def BLUE(self) -> str:
        return self._data.BLUE

    @property
    def ACCENT(self) -> str:
        return self._data.ACCENT

    @property
    def BG_DARK(self) -> str:
        return self._data.BG_DARK

    @property
    def BG_PANEL(self) -> str:
        return self._data.BG_PANEL

    @property
    def BG_INPUT(self) -> str:
        return self._data.BG_INPUT

    @property
    def BG_HOVER(self) -> str:
        return self._data.BG_HOVER

    @property
    def BG_ACTIVE(self) -> str:
        return self._data.BG_ACTIVE

    @property
    def BORDER_DIM(self) -> str:
        return self._data.BORDER_DIM

    @property
    def BORDER_MUTED(self) -> str:
        return self._data.BORDER_MUTED

    @property
    def BORDER_HOVER(self) -> str:
        return self._data.BORDER_HOVER

    @property
    def BORDER_FOCUS(self) -> str:
        return self._data.BORDER_FOCUS

    @property
    def WELCOME_TITLE(self) -> str:
        return self._data.WELCOME_TITLE

    @property
    def WELCOME_SUBTITLE(self) -> str:
        return self._data.WELCOME_SUBTITLE

    @property
    def WELCOME_VERSION(self) -> str:
        return self._data.WELCOME_VERSION

    @property
    def name(self) -> str:
        return self._data.name

    @property
    def is_dark(self) -> bool:
        return self._data.name == "dark"

    @property
    def is_light(self) -> bool:
        return not self.is_dark

    # ── 切换 ──

    def set_dark(self):
        self._data = DARK
        self.theme_changed.emit("dark")

    def set_light(self):
        self._data = LIGHT
        self.theme_changed.emit("light")

    def apply_to_app(self, app):
        """设置调色板并跟随系统切换。"""
        hints = app.styleHints()
        self._apply_scheme(hints.colorScheme())
        try:
            hints.colorSchemeChanged.connect(self._on_scheme_changed)
        except Exception:
            pass

    def _on_scheme_changed(self):
        from PySide6.QtWidgets import QApplication

        hints = QApplication.styleHints()
        self._apply_scheme(hints.colorScheme())

    def _apply_scheme(self, scheme):
        from PySide6.QtWidgets import QApplication

        is_dark = scheme == Qt.ColorScheme.Dark
        if is_dark:
            self._data = DARK
        else:
            self._data = LIGHT
        palette = make_palette(is_dark)
        QApplication.setPalette(palette)
        self.theme_changed.emit("dark" if is_dark else "light")


# ═══════════════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════════════

T = ThemeManager()

# 兼容旧式直接导入：from dualign.gui.theme import FG_PRIMARY
# 注意：导入时会拷贝值，不会随主题切换更新。
# 新代码请使用: from dualign.gui.theme import T  →  T.FG_PRIMARY
FG_PRIMARY = T.FG_PRIMARY
FG_SECONDARY = T.FG_SECONDARY
FG_MUTED = T.FG_MUTED
FG_ACCENT = T.FG_ACCENT
GREEN = T.GREEN
RED = T.RED
ORANGE = T.ORANGE
YELLOW = T.YELLOW
BLUE = T.BLUE
ACCENT = T.ACCENT


# ═══════════════════════════════════════════════════════════════
# 禁用色工具 — 获取 Fusion 主题的禁用控件文字色
# ═══════════════════════════════════════════════════════════════

_DISABLED_FG: str | None = None


def disabled_fg() -> str:
    """返回当前 Fusion 主题的禁用文字色。

    创建临时 QPushButton 查询 QPalette.Disabled.ButtonText。
    结果全局缓存，仅在首次调用时查询。
    """
    global _DISABLED_FG
    if _DISABLED_FG is None:
        from PySide6.QtWidgets import QPushButton as _QPushButton
        from PySide6.QtGui import QPalette as _QPalette

        _btn = _QPushButton()
        _btn.setEnabled(False)
        _DISABLED_FG = (
            _btn.palette()
            .color(_QPalette.ColorGroup.Disabled, _QPalette.ColorRole.ButtonText)
            .name()
        )
    return _DISABLED_FG


BG_DARK = T.BG_DARK
BG_PANEL = T.BG_PANEL
BG_INPUT = T.BG_INPUT
BG_HOVER = T.BG_HOVER
BG_ACTIVE = T.BG_ACTIVE
BORDER_DIM = T.BORDER_DIM
BORDER_MUTED = T.BORDER_MUTED
BORDER_HOVER = T.BORDER_HOVER
BORDER_FOCUS = T.BORDER_FOCUS

# 欢迎页专用（旧代码也可能直接导入）
WELCOME_TITLE = T.WELCOME_TITLE
WELCOME_SUBTITLE = T.WELCOME_SUBTITLE
WELCOME_VERSION = T.WELCOME_VERSION
