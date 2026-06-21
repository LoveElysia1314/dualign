"""
Dualign — BatchDiscoveryDialog: 批量文件对发现与导入

一个轻量级 QDialog，让用户选择源/目标目录，通过 FilePairMatcher
自动发现匹配的文件对，勾选后批量导入 WorkspacePanel 的队列。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QMessageBox,
    QCheckBox,
    QGroupBox,
    QLineEdit,
)

from dualign.core.file_pair_matcher import FilePairMatcher, MatchRule, MatchedPair

# ── 规则预设列表 ──
RULE_PRESETS: list[tuple[str, str, list[MatchRule] | None]] = [
    ("自动检测 (推荐)", "自动尝试所有默认规则，匹配成功率最高", None),
    (
        "*.source.md ↔ *.target.md",
        "最常见的命名约定，如 ch01.source.md ↔ ch01.target.md",
        [MatchRule(type="glob", src_pattern="*.source.md", tgt_pattern="*.target.md")],
    ),
    (
        "*.src.md ↔ *.tgt.md",
        "缩写命名，如 ch01.src.md ↔ ch01.tgt.md",
        [MatchRule(type="glob", src_pattern="*.src.md", tgt_pattern="*.tgt.md")],
    ),
    (
        "*.zh.md ↔ *.en.md",
        "语言标记命名，如 ch01.zh.md ↔ ch01.en.md",
        [MatchRule(type="glob", src_pattern="*.zh.md", tgt_pattern="*.en.md")],
    ),
    (
        "后缀配对 (.source.md)",
        "按文件名前缀匹配，去掉后缀后配对",
        [MatchRule(type="prefix", suffix_pair=(".source.md", ".target.md"))],
    ),
    (
        "后缀配对 (.src.md)",
        "按文件名前缀匹配，缩写版",
        [MatchRule(type="prefix", suffix_pair=(".src.md", ".tgt.md"))],
    ),
    (
        "同目录自然排序配对",
        "按文件名自然排序一一对应，适用于等量文件",
        [MatchRule(type="prefix", suffix_pair=(".md", ".md"))],
    ),
]


class BatchDiscoveryDialog(QDialog):
    """批量文件对发现对话框。

    流程: 选择目录 → 选规则 → 发现 → 勾选 → 导入
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量发现文件对")
        self.setMinimumSize(580, 480)
        pass  # Fusion palette handles dialog background
        self._matched_pairs: list[MatchedPair] = []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 14, 16, 14)

        # ── 目录选择 ──
        dir_group = QGroupBox("选择目录")
        dg = QVBoxLayout(dir_group)
        dg.setSpacing(6)

        for label, attr in [
            ("源文件目录:", "_src_dir_edit"),
            ("目标文件目录:", "_tgt_dir_edit"),
        ]:
            row = QHBoxLayout()
            row.setSpacing(6)
            lbl = QLabel(label)
            lbl.setFixedWidth(90)
            row.addWidget(lbl)

            edit = QLineEdit()
            edit.setPlaceholderText("点击右侧按钮选择目录...")
            row.addWidget(edit, 1)
            setattr(self, attr, edit)

            btn = QPushButton("...")
            btn.setFixedSize(28, 24)
            btn.clicked.connect(lambda checked, e=edit: self._browse_dir(e))
            row.addWidget(btn)
            dg.addLayout(row)

        root.addWidget(dir_group)

        # ── 规则选择 ──
        rule_group = QGroupBox("匹配规则")
        rule_group.setStyleSheet(dir_group.styleSheet())
        rl = QVBoxLayout(rule_group)
        rl.setSpacing(6)

        self._rule_combo = QComboBox()
        for preset in RULE_PRESETS:
            self._rule_combo.addItem(preset[0])
        self._rule_combo.currentIndexChanged.connect(self._on_rule_changed)
        rl.addWidget(self._rule_combo)

        self._rule_desc = QLabel(RULE_PRESETS[0][1])
        self._rule_desc.setWordWrap(True)
        rl.addWidget(self._rule_desc)

        root.addWidget(rule_group)

        # ── 发现按钮 ──
        discover_btn = QPushButton("🔍 发现文件对")
        discover_btn.setMinimumHeight(36)
        pass  # Fusion palette handles button style
        discover_btn.clicked.connect(self._on_discover)
        root.addWidget(discover_btn)

        # ── 结果列表 ──
        result_group = QGroupBox("发现结果")
        result_group.setStyleSheet(dir_group.styleSheet())
        rg = QVBoxLayout(result_group)
        rg.setSpacing(4)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._result_count = QLabel("待发现")
        toolbar.addWidget(self._result_count)

        self._select_all_cb = QCheckBox("全选 / 全不选")
        self._select_all_cb.setTristate(False)
        self._select_all_cb.toggled.connect(self._on_select_all)
        self._select_all_cb.setEnabled(False)
        toolbar.addWidget(self._select_all_cb)

        toolbar.addStretch()
        rg.addLayout(toolbar)

        self._list = QListWidget()
        self._list.setMinimumHeight(120)
        rg.addWidget(self._list, 1)
        root.addWidget(result_group, 1)

        # ── 底部按钮 ──
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(cancel_btn)

        self._import_btn = QPushButton("导入选中 →")
        self._import_btn.setMinimumWidth(120)
        pass  # Fusion palette handles button style
        self._import_btn.clicked.connect(self._on_import)
        self._import_btn.setEnabled(False)
        bottom.addWidget(self._import_btn)

        root.addLayout(bottom)

    def _browse_dir(self, edit: QLineEdit):
        """打开目录选择对话框。"""
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            edit.setText(path)

    def _on_rule_changed(self, index: int):
        """规则选择变更时更新描述。"""
        if 0 <= index < len(RULE_PRESETS):
            self._rule_desc.setText(RULE_PRESETS[index][1])

    def _on_discover(self):
        """执行批量发现。"""
        src_dir = self._src_dir_edit.text().strip()
        tgt_dir = self._tgt_dir_edit.text().strip()

        if not src_dir or not tgt_dir:
            QMessageBox.warning(
                self, "目录未设置", "请先选择源文件目录和目标文件目录。"
            )
            return

        if not Path(src_dir).is_dir():
            QMessageBox.warning(self, "目录不存在", f"源目录不存在:\n{src_dir}")
            return
        if not Path(tgt_dir).is_dir():
            QMessageBox.warning(self, "目录不存在", f"目标目录不存在:\n{tgt_dir}")
            return

        # 获取规则预设
        idx = self._rule_combo.currentIndex()
        rules = None
        if 0 <= idx < len(RULE_PRESETS):
            rules = RULE_PRESETS[idx][2]  # None = 自动检测

        matcher = FilePairMatcher()
        try:
            pairs = matcher.match(src_dir, tgt_dir, rules=rules)
        except ValueError as e:
            QMessageBox.warning(self, "匹配失败", str(e))
            return

        self._matched_pairs = pairs
        self._populate_results()

    def _populate_results(self):
        """填充结果列表（带复选框）。"""
        self._list.clear()
        self._select_all_cb.setEnabled(False)

        if not self._matched_pairs:
            self._result_count.setText("未发现匹配的文件对")
            self._import_btn.setEnabled(False)
            return

        self._select_all_cb.setEnabled(True)
        self._select_all_cb.setChecked(True)

        for pair in self._matched_pairs:
            item = QListWidgetItem()
            item.setText(
                f"  {pair.label or pair.entry_id}  —  "
                f"{Path(pair.src_path).name}  ↔  {Path(pair.tgt_path).name}"
            )
            item.setData(Qt.ItemDataRole.UserRole, pair)
            item.setCheckState(Qt.CheckState.Checked)
            item.setSizeHint(item.sizeHint().grownBy(0, 2))
            self._list.addItem(item)

        self._result_count.setText(f"发现 {len(self._matched_pairs)} 个文件对")
        self._import_btn.setEnabled(True)

    def _on_select_all(self, checked: bool):
        """全选/全不选切换。"""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(state)

    def _on_import(self):
        """收集选中项并接受对话框。"""
        checked = self.selected_pairs()
        if not checked:
            QMessageBox.information(self, "未选择", "请至少勾选一个文件对。")
            return
        self.accept()

    def selected_pairs(self) -> list[MatchedPair]:
        """返回用户勾选的文件对列表。"""
        results: list[MatchedPair] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                pair = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(pair, MatchedPair):
                    results.append(pair)
        return results
