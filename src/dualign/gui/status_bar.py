"""
Dualign — StatusBar: 自适应四区布局

┌── ① 分数色轴 (min=180,s=1) ┬ ② 表格模式 (min=130,s=1) ┬ ③ 自动跳转 (min=160,s=1) ┬ ④ 定位 (min=110,s=1) ┬ ⑤ 消息 (stretch,s=3) ┐
│ 评分色轴：0 ██████████ 1   │ 表格模式：[校订|预览]     │ ☑ 操作后自动跳转下一项     │ 异常文本对 3/15          │   对齐完成              │
└──────────────────────────┴──────────────────────────┴───────────────────────────┴─────────────────────────┴────────────────────────┘

设计要点:
  - ① "评分色轴："标签 + 红→黄→绿渐变色条，左右标 0/1 数值
  - 无 emoji 冗余前缀
"""

from __future__ import annotations

from typing import Optional
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QFontMetrics, QLinearGradient, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
    QPushButton,
    QButtonGroup,
    QCheckBox,
)
from dualign.gui.theme import T, BORDER_DIM

# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

_LABEL_STYLE = "font-size:11px;border:none;background:transparent;"
_ACCENT_LBL = "font-size:11px;border:none;background:transparent;color:{color};"

_DOT_RADIUS = 5
_DOT_CX = 10


def _dot_color(ok: Optional[bool]) -> str:
    return {None: T.FG_MUTED, True: T.GREEN, False: T.RED}.get(ok, T.FG_MUTED)


# ═══════════════════════════════════════════════════════════════
# StatusDot — 状态指示灯
# ═══════════════════════════════════════════════════════════════


class StatusDot(QFrame):
    """状态指示灯组件。用于底部 AI 面板标题行。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._text = text
        self._ok: Optional[bool] = None
        self._dot_color: QColor = QColor(T.FG_MUTED)
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_ok(self, ok: Optional[bool]):
        self._ok = ok
        self._dot_color = QColor(_dot_color(ok))
        self.update()

    def set_text(self, text: str):
        self._text = text
        self.update()

    def paintEvent(self, event):
        from PySide6.QtCore import QRectF

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r, cx = _DOT_RADIUS, _DOT_CX
        cy = self.height() // 2

        # 设置字体大小（与 minimumSizeHint 一致）
        f = QFont(p.font())
        if f.pointSize() > 0:
            f.setPointSize(8)
        else:
            f.setPixelSize(11)
        p.setFont(f)

        # 左对齐：圆点固定于 _DOT_CX 处，文本紧随其后
        p.setBrush(self._dot_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        p.setPen(QColor(T.FG_PRIMARY))
        text_x = cx + r + 4
        p.drawText(
            text_x,
            0,
            self.width() - text_x,
            self.height(),
            Qt.AlignVCenter | Qt.AlignLeft,
            self._text,
        )
        p.end()

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize

        fm = QFontMetrics(self.font())
        tw = fm.horizontalAdvance(self._text)
        return QSize(_DOT_CX + _DOT_RADIUS + 4 + tw + 4, 22)


# ═══════════════════════════════════════════════════════════════
# ScoreBar — 渐变色条（带 0/1 数值标签）
# ═══════════════════════════════════════════════════════════════


class ScoreBar(QWidget):
    """红→黄→绿渐变色条，左右标 0/1 数值标签，专用于状态栏。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(22)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()

        # 字体
        font = QFont()
        font.setPixelSize(9)
        p.setFont(font)
        fm = p.fontMetrics()
        lbl0_w = fm.horizontalAdvance("0")
        lbl1_w = fm.horizontalAdvance("1")
        gap = 3

        # 标签 Y（垂直居中）
        lbl_y = (h - fm.height()) // 2 + fm.ascent()

        # 绘制左侧 "0" 标签
        x0 = 2
        p.setPen(QColor(T.FG_SECONDARY))
        p.drawText(x0, lbl_y, "0")

        # 绘制右侧 "1" 标签
        x1 = w - lbl1_w - 2
        p.drawText(x1, lbl_y, "1")

        # 色条区域（从 "0" 标签右侧到 "1" 标签左侧）
        bar_x = x0 + lbl0_w + gap
        bar_w = x1 - gap - bar_x
        if bar_w < 20:
            bar_w = 20
        bar_h = 10
        bar_y = (h - bar_h) // 2
        bar_rect = QRectF(bar_x, bar_y, bar_w, bar_h)

        grad = QLinearGradient(bar_x, 0, bar_x + bar_w, 0)
        grad.setColorAt(0.0, QColor(220, 20, 50))
        grad.setColorAt(0.5, QColor(220, 220, 50))
        grad.setColorAt(1.0, QColor(30, 200, 50))

        p.setPen(QPen(QColor(BORDER_DIM), 1))
        p.setBrush(grad)
        p.drawRoundedRect(bar_rect, 2, 2)
        p.end()

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize

        return QSize(70, 22)


# ═══════════════════════════════════════════════════════════════
# StatusBar — 自适应四区（min-width + stretch 权重）
# ═══════════════════════════════════════════════════════════════


# 四区权重: 需要更多空间的区 stretch 更大
_S_MODE = 1  # 视图模式 (紧凑, min=110)
_S_SCORE = 1  # 分数色轴 (紧凑, min=180)
_S_AUTO = 1  # 自动跳转 (紧凑, min=160)
_S_POS = 1  # 定位 (紧凑, min=110)
_S_MSG = 3  # 消息 (弹性)


class StatusBar(QFrame):
    """导航状态栏 — 四区布局（分数色轴 + 模式 + 自动跳转 + 定位 + 消息）。"""

    view_mode_toggled = Signal(bool)  # True=预览模式, False=校订模式
    auto_advance_toggled = Signal(bool)  # 操作后是否自动跳转下一项

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setObjectName("StatusBar")
        self.setStyleSheet(
            "QFrame#StatusBar{"
            "  border-bottom:1px solid palette(mid);"
            "  background:palette(window);"
            "}"
        )
        self._build_ui()

    def _build_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(6)

        # ══════════════════════════════════════════════════════
        # ① 分数色轴: 左对齐, min-width 自适应
        # ══════════════════════════════════════════════════════
        sw_score = QFrame()
        sw_score.setMinimumWidth(180)
        sl_score = QHBoxLayout(sw_score)
        sl_score.setContentsMargins(0, 0, 0, 0)
        sl_score.setSpacing(4)
        self._score_label = QLabel("评分色轴：")
        self._score_label.setStyleSheet(
            "font-size:11px;border:none;background:transparent;"
        )
        self._score_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        sl_score.addWidget(self._score_label)
        self._score_bar = ScoreBar()
        sl_score.addWidget(self._score_bar)
        lay.addWidget(sw_score, _S_SCORE)

        lay.addWidget(self._vsep())

        # ══════════════════════════════════════════════════════
        # ② 视图模式: 校订 / 预览 互斥切换（标签+按钮同行）
        # ══════════════════════════════════════════════════════
        self._mode_wrapper = QFrame()
        self._mode_wrapper.setMinimumWidth(130)
        mlay = QHBoxLayout(self._mode_wrapper)
        mlay.setContentsMargins(0, 0, 0, 0)
        mlay.setSpacing(4)
        self._mode_label = QLabel("表格模式：")
        self._mode_label.setStyleSheet(
            "font-size:11px;border:none;background:transparent;"
        )
        self._mode_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        mlay.addWidget(self._mode_label)
        self._edit_btn = QPushButton("校订")
        self._edit_btn.setCheckable(True)
        self._edit_btn.setChecked(True)
        self._edit_btn.setFixedSize(32, 20)
        self._edit_btn.setStyleSheet(
            "QPushButton{font-size:11px;padding:0;border:1px solid palette(mid);"
            "border-radius:2px 0 0 2px;background:transparent;color:palette(text);}"
            "QPushButton:checked{background:palette(highlight);color:palette(highlighted-text);"
            "border-color:palette(highlight);}"
            "QPushButton:hover:!checked{background:palette(button);}"
        )
        mlay.addWidget(self._edit_btn)
        self._preview_btn = QPushButton("预览")
        self._preview_btn.setCheckable(True)
        self._preview_btn.setFixedSize(32, 20)
        self._preview_btn.setStyleSheet(
            "QPushButton{font-size:11px;padding:0;border:1px solid palette(mid);"
            "border-left:none;border-radius:0 2px 2px 0;background:transparent;color:palette(text);}"
            "QPushButton:checked{background:#E74C3C;color:white;border-color:#E74C3C;}"
            "QPushButton:hover:!checked{background:palette(button);}"
        )
        mlay.addWidget(self._preview_btn)
        mlay.addStretch()
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._edit_btn, 0)
        self._mode_group.addButton(self._preview_btn, 1)
        self._mode_group.idToggled.connect(self._on_mode_toggled)
        lay.addWidget(self._mode_wrapper, _S_MODE)

        lay.addWidget(self._vsep())

        # ══════════════════════════════════════════════════════
        # ③ 自动跳转复选框
        # ══════════════════════════════════════════════════════
        self._auto_advance_cb = QCheckBox("操作后自动跳转下一项")
        self._auto_advance_cb.setChecked(True)
        self._auto_advance_cb.toggled.connect(self.auto_advance_toggled.emit)
        lay.addWidget(self._auto_advance_cb, _S_AUTO)

        lay.addWidget(self._vsep())

        # ══════════════════════════════════════════════════════
        # ④ 定位区 + 预览标签
        # ══════════════════════════════════════════════════════
        pw = QFrame()
        pw.setMinimumWidth(110)
        pl = QHBoxLayout(pw)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)
        self._pos_label = QLabel("异常文本对")
        self._pos_label.setStyleSheet(_ACCENT_LBL.format(color=T.FG_SECONDARY))
        self._pos_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        pl.addWidget(self._pos_label)
        self._pos = QLabel("—")
        self._pos.setAlignment(Qt.AlignCenter)
        self._pos.setFixedWidth(44)
        self._refresh_pos_style()
        pl.addWidget(self._pos)
        self._preview_lbl = QLabel("")
        self._preview_lbl.setStyleSheet(
            "font-size:11px;font-weight:600;border:none;background:transparent;"
            "color:#E67E22;padding:0 4px;"
        )
        self._preview_lbl.setAlignment(Qt.AlignCenter)
        self._preview_lbl.setVisible(False)
        pl.addWidget(self._preview_lbl)
        pl.addStretch()
        lay.addWidget(pw, _S_POS)

        lay.addWidget(self._vsep())

        # ══════════════════════════════════════════════════════
        # ⑤ 消息区: 右对齐
        # ══════════════════════════════════════════════════════
        mw = QFrame()
        mw.setMinimumWidth(160)
        ml = QHBoxLayout(mw)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addStretch()
        self._msg = QLabel("")
        self._msg.setStyleSheet(_LABEL_STYLE)
        self._msg.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ml.addWidget(self._msg)
        lay.addWidget(mw, _S_MSG)

    @staticmethod
    def _vsep() -> QFrame:
        s = QFrame()
        s.setFrameShape(QFrame.Shape.VLine)
        s.setFixedWidth(1)
        s.setStyleSheet("border:none;background:palette(mid);")
        s.setFixedHeight(18)
        return s

    def _refresh_pos_style(self):
        self._pos.setStyleSheet(
            "font-weight:bold;font-size:11px;border:none;"
            f"border:1px solid {T.BORDER_MUTED};border-radius:3px;"
            "padding:0 4px;background:transparent;"
        )

    # ── 公开接口 ──

    def set_pos(self, pos_text: str):
        """设置异常定位（如 "3/15"）。"""
        self._pos.setText(pos_text if pos_text else "—")

    def set_message(self, text: str):
        self._msg.setText(text)

    def is_auto_advance(self) -> bool:
        return self._auto_advance_cb.isChecked()

    # ── 预览模式 ──

    def set_preview_active(self, active: bool, rejected: bool = False, phase: str = ""):
        self._preview_lbl.setVisible(active)
        if active:
            self._pos.setVisible(False)
            self._pos_label.setVisible(False)
            if phase:
                self._preview_lbl.setText(phase)
                self._preview_lbl.setStyleSheet(
                    "font-size:11px;font-weight:700;border:none;background:transparent;"
                    "color:#F7A600;padding:0 8px;"
                )
            elif rejected:
                self._preview_lbl.setText("🔒 拒绝对齐 · 预览模式")
                self._preview_lbl.setStyleSheet(
                    "font-size:11px;font-weight:600;border:none;background:transparent;"
                    "color:#E040FB;padding:0 4px;"
                )
            else:
                self._preview_lbl.setText("预览模式")
                self._preview_lbl.setStyleSheet(
                    "font-size:11px;font-weight:600;border:none;background:transparent;"
                    "color:#E67E22;padding:0 4px;"
                )
        else:
            self._pos.setVisible(True)
            self._pos_label.setVisible(True)

    # ── 视图模式切换 ──

    def _on_mode_toggled(self, btn_id: int, checked: bool):
        if checked:
            self.view_mode_toggled.emit(btn_id == 1)

    def set_view_mode(self, preview: bool):
        """程序化设置视图模式（不触发信号）。"""
        self._mode_group.blockSignals(True)
        if preview:
            self._preview_btn.setChecked(True)
        else:
            self._edit_btn.setChecked(True)
        self._mode_group.blockSignals(False)
