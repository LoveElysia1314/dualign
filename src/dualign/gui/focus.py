"""
Dualign — FocusManager: 统一焦点与选中管理

集中管理所有组件的焦点状态，通过 3 个信号驱动 UI 同步。
消除分散的 _selected_snaps_set / _focus_snap / _current_idx / _focused_action 等状态。
"""

from __future__ import annotations

from typing import Optional, Set

from PySide6.QtCore import QObject, Signal

from dualign.models.action import RepairAction


class FocusManager(QObject):
    """统一焦点管理器。

    集中管理：
      - focused_snap:        对齐表的焦点 Snap（同步到预览表和定位器）
      - selected_snaps:      对齐表的选中 Snap 集合（Ctrl/Shift 选择）
      - focused_action:      AI 建议的焦点操作
      - anomaly_index:       异常导航索引（◀▶）
      - force_show_snaps:    跨筛选强制显示的 snap 集合（AI 跨区建议）
      - source:              最后一次焦点来源 ("table"|"review"|"ai")

    信号（3 个，替代当前所有分散信号）:
      snap_focused        → 对齐表滚动+高亮 + preview表高亮 + 定位器更新
      selection_changed   → _emit_indicator 更新定位器
      action_focused      → AI 按钮状态变更
    """

    snap_focused = Signal(int)  # snap_i
    selection_changed = Signal(set)  # Set[int]
    action_focused = Signal(object)  # RepairAction | None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.focused_snap: Optional[int] = None
        self.selected_snaps: Set[int] = set()
        self.focused_action: Optional[RepairAction] = None
        self.anomaly_index: int = -1
        self.force_show_snaps: Set[int] = set()
        self.source: str = "table"  # "table" | "review" | "ai"
        self._sync_lock: bool = False

    # ══════════════════════════════════════════════════════════
    # 统一入口
    # ══════════════════════════════════════════════════════════

    def go_to_snap(self, snap_i: int, source: str = "table"):
        """聚焦一个 snap（3 组件同步入口）。

        等同于旧的 _on_go_to_row，但纯状态 + 信号，
        由各组件的 slot 响应信号做 UI 操作。
        """
        if self._sync_lock:
            return
        self._sync_lock = True
        try:
            self.focused_snap = snap_i
            self.source = source
            self.selected_snaps = {snap_i}
            self.snap_focused.emit(snap_i)
            self.selection_changed.emit(self.selected_snaps)
        finally:
            self._sync_lock = False

    def select_snaps(self, snaps: Set[int], source: str = "table"):
        """批量选中 snaps。"仅焦点"模式下自动重定向到最近异常。"""
        if self._sync_lock:
            return
        self._sync_lock = True
        try:
            self.selected_snaps = snaps
            if snaps:
                # 自动设置 focused_snap 为最小选中行
                self.focused_snap = min(snaps)
            self.source = source
            self.selection_changed.emit(snaps)
        finally:
            self._sync_lock = False

    def focus_action(self, action: Optional[RepairAction]):
        """聚焦一条 AI 建议。

        设置 focused_action + force_show_snaps，
        以便 _apply_filter 能强制显示涉及的所有 snap。
        """
        if self._sync_lock:
            return
        self._sync_lock = True
        try:
            self.focused_action = action
            if action is not None:
                self.source = "ai"
                # 跨 snap 建议：强制显示所有相关 snap
                if action.data and action.data.get("orig_snaps"):
                    self.force_show_snaps = set(action.data["orig_snaps"])
                else:
                    self.force_show_snaps = {action.op_index}
            else:
                self.force_show_snaps = set()
            self.action_focused.emit(action)
        finally:
            self._sync_lock = False

    def navigate_anomaly(self, idx: int):
        """异常导航：设置 anomaly_index 并聚焦对应 snap。"""
        self.anomaly_index = idx
        # go_to_snap 由外部调用（需从 anomalies 列表取 snap_index）

    def clear_force_show(self):
        """清除跨筛选强制显示标记。"""
        self.force_show_snaps = set()

    def clear(self):
        """清除所有焦点状态。"""
        self.focused_snap = None
        self.selected_snaps = set()
        self.anomaly_index = -1
        self.clear_force_show()
        self.focus_action(None)
