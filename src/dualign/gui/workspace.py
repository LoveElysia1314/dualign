"""
Dualign — WorkspacePanel: 统一工作区面板
"""

from __future__ import annotations

import os
import json
from typing import List, Tuple, Optional, Set
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QGroupBox,
    QFileDialog,
    QComboBox,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QSizePolicy,
)


class DragDropLineEdit(QLineEdit):
    file_dropped = Signal(str)

    def __init__(self, ph="", parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setPlaceholderText(ph)
        self.setStyleSheet("")

    def dragEnterEvent(self, e):
        e.acceptProposedAction() if e.mimeData().hasUrls() else None

    def dropEvent(self, e):
        if e.mimeData().urls():
            p = e.mimeData().urls()[0].toLocalFile()
            if p.lower().endswith((".md", ".txt", ".markdown")):
                self.setText(p)
                self.file_dropped.emit(p)
        e.acceptProposedAction()


class FileQueueItem:
    def __init__(self, label="", src_path="", tgt_path="", entry=None):
        self.label = label
        self.src_path = src_path
        self.tgt_path = tgt_path
        self.entry = entry
        self.aligned = False

    @property
    def display_title(self):
        return (
            Path(self.src_path).name if self.src_path else (self.label or "（未命名）")
        )


class WorkspacePanel(QWidget):
    file_pair_requested = Signal(str, str, str)
    add_queue_requested = Signal()
    doc_remove_requested = Signal()
    entry_selected = Signal(object)  # 导航时携带 ChapterEntry 更新 _current_entry
    chapter_nav_requested = Signal(int)

    _RF = os.path.join(os.path.expanduser("~"), ".dualign", "recent_pairs.json")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: List[FileQueueItem] = []
        self._selected: Optional[FileQueueItem] = None
        self._selected_set: Set[FileQueueItem] = set()
        self._recent_pairs: List[Tuple[str, str, str]] = self._load_recent()
        self._build_ui()
        self._rrc()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(200)

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize

        return QSize(0, super().minimumSizeHint().height())

    def _build_ui(self):
        r = QVBoxLayout(self)
        r.setContentsMargins(2, 2, 2, 2)
        r.setSpacing(4)

        # ── 添加文件对（补上原文/译文标签）──
        pg = QGroupBox("添加文件对")
        pl = QVBoxLayout(pg)
        pl.setContentsMargins(6, 8, 6, 4)
        pl.setSpacing(4)
        self._se = DragDropLineEdit("拖拽或浏览 .md/.txt")
        self._te = DragDropLineEdit("拖拽或浏览 .md/.txt")
        for ic, label, ed, slt in [
            ("📄", "原文:", self._se, self._on_browse_src),
            ("📄", "译文:", self._te, self._on_browse_tgt),
        ]:
            rr = QHBoxLayout()
            rr.setSpacing(4)
            lbl = QLabel(label)
            lbl.setFixedWidth(36)
            lbl.setStyleSheet("")
            rr.addWidget(lbl)
            ed.setMinimumWidth(0)
            rr.addWidget(ed, 1)
            b = QPushButton("...")
            b.clicked.connect(slt)
            rr.addWidget(b)
            pl.addLayout(rr)
        ar = QHBoxLayout()
        ar.setSpacing(4)
        ab = QPushButton("＋ 添加到列表")
        # Fusion palette handles button style
        ab.clicked.connect(self._on_add)
        ar.addWidget(ab)
        self._rc = QComboBox()
        self._rc.addItem("📋 最近文件对")
        self._rc.currentIndexChanged.connect(self._on_recent)
        ar.addWidget(self._rc, 1)
        pl.addLayout(ar)
        r.addWidget(pg)

        # ── 文件对列表 ──
        qg = QGroupBox("文件对列表")
        ql = QVBoxLayout(qg)
        ql.setContentsMargins(6, 8, 6, 4)
        ql.setSpacing(3)

        # 标题栏 + 操作按钮行
        h = QHBoxLayout()
        h.setSpacing(4)
        self._qc = QLabel("文件 (0)")
        h.addWidget(self._qc)
        h.addStretch()
        for tx, sig, tip in [
            ("◀ 上一章", "prev", "切换到上一章"),
            ("▶ 下一章", "next", "切换到下一章"),
            ("删除", "delete", "删除选中"),
        ]:
            b = QPushButton(tx)
            b.setFixedHeight(22)
            b.setToolTip(tip)
            b.clicked.connect(lambda v=sig: self._on_list_action(v))
            h.addWidget(b)
        ql.addLayout(h)

        # 列表控件（多选 + 多行）
        self._qlw = QListWidget()
        self._qlw.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._qlw.itemClicked.connect(self._on_item_clicked)
        self._qlw.itemDoubleClicked.connect(self._on_item_double)
        self._qlw.setMinimumHeight(28)
        ql.addWidget(self._qlw, 1)
        qg.setMinimumHeight(160)
        r.addWidget(qg, 1)

        # 文档操作已移至 ReviewController

    def add_log_panel(self, log_panel):
        """将运行日志面板添加到文件管理面板底部。"""
        g = QGroupBox("📋 运行日志")
        g.setMinimumHeight(160)
        gl = QVBoxLayout(g)
        gl.setContentsMargins(4, 2, 4, 4)
        gl.setSpacing(1)
        gl.addWidget(log_panel, 1)
        r = self.layout()
        if r is not None:
            r.addWidget(g, 1)

    def set_gating(self, **kwargs):
        pass

    def _on_browse_src(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "选择原文", "", "Markdown (*.md);;Text (*.txt);;All (*)"
        )
        if p:
            self._se.setText(p)

    def _on_browse_tgt(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "选择译文", "", "Markdown (*.md);;Text (*.txt);;All (*)"
        )
        if p:
            self._te.setText(p)

    def _on_add(self):
        s = self._se.text().strip()
        t = self._te.text().strip()
        if not s or not t:
            return
        if not os.path.exists(s):
            print(f"⚠ 原文不存在: {s}")
            return
        if not os.path.exists(t):
            print(f"⚠ 译文不存在: {t}")
            return
        lb = Path(s).stem.split(".")[0]
        self._add_to_recent(lb, s, t)
        for it in self._queue:
            if it.src_path == s and it.tgt_path == t:
                self._select(it)
                self._se.clear()
                self._te.clear()
                return
        ni = FileQueueItem(label=lb, src_path=s, tgt_path=t)
        self._queue.append(ni)
        self._rebuild()
        self._select(ni)
        self._se.clear()
        self._te.clear()

    def _select(self, item: FileQueueItem):
        self._selected = item
        for i in range(self._qlw.count()):
            if self._qlw.item(i).data(Qt.ItemDataRole.UserRole) is item:
                self._qlw.setCurrentRow(i)
                break

    def selected_item(self):
        return self._selected

    def _rebuild(self):
        """重建文件列表，每项显示两行：标题 + 路径概要。

        重建后自动恢复 _selected 的高亮。
        """
        self._qlw.blockSignals(True)
        self._qlw.clear()
        for it in self._queue:
            lines = [it.display_title]
            if it.src_path or it.tgt_path:
                paths = []
                if it.src_path:
                    paths.append(f"源: {Path(it.src_path).name}")
                if it.tgt_path:
                    paths.append(f"译: {Path(it.tgt_path).name}")
                lines.append("  " + "  ".join(paths))
            text = "\n".join(lines)
            if it.aligned:
                text += "  ✓"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, it)
            item.setSizeHint(QSize(0, 36))
            self._qlw.addItem(item)
        self._qlw.blockSignals(False)
        self._qc.setText(f"文件 ({len(self._queue)})")
        # 重建后恢复选中高亮
        if self._selected:
            for i in range(self._qlw.count()):
                if self._qlw.item(i).data(Qt.ItemDataRole.UserRole) is self._selected:
                    self._qlw.setCurrentRow(i)
                    break

    def _on_list_action(self, action: str):
        """文件列表操作按钮回调：prev/next/delete。"""
        if action in ("prev", "next"):
            self.chapter_nav_requested.emit(-1 if action == "prev" else 1)
            return
        if action == "delete":
            # 优先用当前选中项（_selected），若焦点丢失则用 QListWidget 选中项
            target = self._selected
            if target is None:
                sel = self._qlw.selectedItems()
                if sel:
                    target = sel[0].data(Qt.ItemDataRole.UserRole)
            if target is not None and target in self._queue:
                self._queue.remove(target)
                self._selected = None
                self._rebuild()

    def _on_item_clicked(self, item):
        it = item.data(Qt.ItemDataRole.UserRole)
        if it is not None and item.isSelected():
            self._selected = it
            if it.entry is not None:
                self.entry_selected.emit(it.entry)
            self.file_pair_requested.emit(it.src_path, it.tgt_path, it.label)

    def _on_item_double(self, item):
        self._on_item_clicked(item)

    def _add_to_recent(self, lb, s, t):
        self._recent_pairs = [
            p for p in self._recent_pairs if not (p[1] == s and p[2] == t)
        ]
        self._recent_pairs.insert(0, (lb, s, t))
        if len(self._recent_pairs) > 20:
            self._recent_pairs = self._recent_pairs[:20]
        self._rrc()
        self._save_recent()

    def _load_recent(self):
        try:
            if os.path.isfile(self._RF):
                with open(self._RF, encoding="utf-8") as f:
                    return [
                        tuple(e)
                        for e in json.load(f)
                        if isinstance(e, list) and len(e) >= 3
                    ][:20]
        except Exception:
            pass
        return []

    def _save_recent(self):
        try:
            os.makedirs(os.path.dirname(self._RF), exist_ok=True)
            with open(self._RF, "w", encoding="utf-8") as f:
                json.dump(
                    self._recent_pairs, f, ensure_ascii=False, separators=(",", ":")
                )
        except Exception:
            import traceback

            traceback.print_exc()

    def _rrc(self):
        self._rc.blockSignals(True)
        self._rc.clear()
        self._rc.addItem("📋 最近文件对")
        for label, s, t in self._recent_pairs:
            self._rc.addItem(f"{label}  ({Path(s).name} ↔ {Path(t).name})")
        self._rc.blockSignals(False)

    def _on_recent(self, idx):
        if idx <= 0 or idx - 1 >= len(self._recent_pairs):
            return
        _, s, t = self._recent_pairs[idx - 1]
        self._se.setText(s)
        self._te.setText(t)

    # ── 外部接口 ──
    def get_recent_pairs(self):
        """返回最近文件对列表 [(label, src, tgt), ...]"""
        return list(self._recent_pairs)

    def remove_recent_pair(self, src_path: str, tgt_path: str):
        """从最近列表中移除指定文件对（文件不存在时自动清理用）。"""
        old_len = len(self._recent_pairs)
        self._recent_pairs = [
            p for p in self._recent_pairs if not (p[1] == src_path and p[2] == tgt_path)
        ]
        if len(self._recent_pairs) < old_len:
            self._rrc()
            self._save_recent()

    def set_queue(self, items):
        self._queue = list(items)
        self._selected = None
        self._rebuild()

    def add_to_queue(self, item):
        for e in self._queue:
            if e.src_path == item.src_path and e.tgt_path == item.tgt_path:
                self._select(e)
                return
        self._queue.append(item)
        self._rebuild()
        self._select(item)
        self._add_to_recent(item.label, item.src_path, item.tgt_path)

    def remove_selected(self):
        if self._selected and self._selected in self._queue:
            self._queue.remove(self._selected)
            self._selected = None
            self._rebuild()

    def set_file_paths(self, s, t, label=""):
        lb = label or Path(s).stem.split(".")[0]
        fd = None
        for it in self._queue:
            if it.src_path == s and it.tgt_path == t:
                fd = it
                break
        if fd is None:
            fd = FileQueueItem(label=lb, src_path=s, tgt_path=t)
            self._queue.append(fd)
        fd.aligned = True
        fd.label = lb
        self._select(fd)
        self._rebuild()
        self._add_to_recent(lb, s, t)

    def _nav_prev(self):
        if not self._queue or not self._selected:
            return
        idx = next((i for i, q in enumerate(self._queue) if q is self._selected), -1)
        if idx > 0:
            nxt = self._queue[idx - 1]
        elif idx == 0:
            nxt = self._queue[-1]
        else:
            return
        self._select(nxt)
        if nxt.entry is not None:
            self.entry_selected.emit(nxt.entry)
        self.file_pair_requested.emit(nxt.src_path, nxt.tgt_path, nxt.label)

    def _nav_next(self):
        if not self._queue:
            return
        if self._selected is None:
            self._select(self._queue[0])
            if self._queue[0].entry is not None:
                self.entry_selected.emit(self._queue[0].entry)
            self.file_pair_requested.emit(
                self._queue[0].src_path, self._queue[0].tgt_path, self._queue[0].label
            )
            return
        idx = next((i for i, q in enumerate(self._queue) if q is self._selected), -1)
        nxt = idx + 1 if idx + 1 < len(self._queue) else 0
        self._select(self._queue[nxt])
        if self._queue[nxt].entry is not None:
            self.entry_selected.emit(self._queue[nxt].entry)
        self.file_pair_requested.emit(
            self._queue[nxt].src_path, self._queue[nxt].tgt_path, self._queue[nxt].label
        )
