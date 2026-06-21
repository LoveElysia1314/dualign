"""
WindowTableMixin — 表格渲染与交互

通过多重继承为 DualignWindow 提供：
  1. 展示数据 (_render_table / _render_preview)
  2. 响应用户操作 → _apply_action → RepairService
  3. 管理 UI 状态 (筛选/导航/历史)
"""

from __future__ import annotations

from typing import List, Optional, Dict, Set, Tuple
import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextDocument
from PySide6.QtWidgets import (
    QMainWindow,
    QTableWidgetItem,
    QAbstractItemView,
    QMenu,
    QApplication,
)

from dualign.services.repair import (
    RepairService,
    TableRow,
    make_table_view,
    compute_spans,
)
from dualign.models.marker import (
    is_deleted,
    is_merge,
    format_anomaly_line,
)
from dualign.gui.base_table import (
    CHANGED_FLAG_ROLE,
    type_cl,
    marker_cl,
    anomaly_cl,
    priority_anomaly_type,
    compute_text_colors,
    has_snap_text_changed,
    text_color_for_side,
    calc_snap_width,
    make_score_cell,
)
import dualign.gui.base_table as _color_table  # 主题感知颜色，通过模块访问避免 import 时固化

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


# ═══════════════════════════════════════════════════════════════
# DualignWindow
# ═══════════════════════════════════════════════════════════════


class WindowTableMixin:
    """WindowTableMixin — 通过多重继承为 DualignWindow 提供方法。"""

    @property
    def selected_snaps(self) -> Set[int]:
        """当前选中的 snap 集合（只读）。委托给 FocusManager。"""
        return frozenset(self._focus.selected_snaps)

    def _set_selected_snaps(self, snaps: Set[int], emit_indicator: bool = True):
        """选中写入口，委托给 FocusManager。"""
        snaps = snaps or set()
        # 标准选中
        if snaps == self._focus.selected_snaps:
            return
        if emit_indicator:
            self._focus.select_snaps(snaps, source="table")
        else:
            # 静默更新（框选拖拽时由 mouse_release 触发 emit）
            self._focus.selected_snaps = snaps
            self._update_table_highlight()

    def _emit_indicator(self, snaps: Set[int]):
        """根据选中集更新审校面板定位器。

        多选时始终用浏览模式。
        单选异常时用 go()（带进度信息）。
        非异常时切换到浏览模式（不清除 AI 建议焦点）。
        """
        sorted_snaps = sorted(snaps) if snaps else sorted(self._focus.selected_snaps)
        if not sorted_snaps:
            return
        if not self._anomalies:
            self._review.show_browsing(sorted_snaps)
            return
        if len(sorted_snaps) == 1:
            for i, a in enumerate(self._anomalies):
                a_list = a.get("snap_indices", [a.get("snap_index")])
                if {s for s in a_list if s is not None} == set(sorted_snaps):
                    self._review.go(i, scroll_to=False)
                    self._focus.navigate_anomaly(i)
                    return
        self._review.show_browsing(sorted_snaps)

    def _on_go_to_row(self, snap_i: int):
        """选中属于 snap_i 的全部行（含跨行合并的 sub-rows）。

        由 ◀▶ 定位器 / AI 建议表格点击触发，走 FocusManager 同时
        滚动对齐表到目标行。直接点击对齐表行不走此路径。
        """
        self._focus.go_to_snap(snap_i, source="table")
        # 滚动对齐表到目标行（直接点击时不滚动，由 _on_snap_focused 决定不滚）
        # 此路径为 ◀▶ 定位 / AI 点击，需要对齐表跟随
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == snap_i:
                self.table.scrollToItem(
                    item, QAbstractItemView.ScrollHint.PositionAtCenter
                )
                break

    def _on_row_clicked(self, item):
        """itemClicked → 同步所有焦点组件。委托给 FocusManager。

        Ctrl/Shift 多选时不移动焦点（避免覆盖用户选中的起始 snap）。
        """
        if item is None:
            return
        snap_i = item.data(Qt.ItemDataRole.UserRole)
        if snap_i is None:
            return
        # 框选拖拽结束时 _table_mouse_release 设置此旗标，跳过 go_to_snap
        if getattr(self, "_suppress_next_click", False):
            self._suppress_next_click = False
            return
        if not self._rubber_active:
            # Ctrl/Shift 多选时不移动焦点
            from PySide6.QtWidgets import QApplication

            mods = QApplication.keyboardModifiers()
            if (
                mods == Qt.KeyboardModifier.ControlModifier
                or mods == Qt.KeyboardModifier.ShiftModifier
            ):
                return
            self._focus.go_to_snap(snap_i, source="table")

    def _get_selected_snaps_sorted(self) -> List[int]:
        return sorted(self._focus.selected_snaps) if self._focus.selected_snaps else []

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        import shiboken6

        # 窗口关闭时 C++ 对象可能已析构，直接返回
        try:
            if shiboken6.isValid(obj) is False:
                return False
        except Exception:
            return False

        # self.table 可能已被 Qt 析构，此时 shiboken 也无法挽救
        if shiboken6.isValid(self.table) is False:
            return False

        # ── 主对齐表 viewport ──
        if obj is self.table.viewport():
            tp = event.type()
            if tp == QEvent.Type.MouseButtonPress:
                self._hover_cancel()
                return self._table_mouse_press(event)
            elif tp == QEvent.Type.MouseMove:
                self._table_hover_track(event)
                return self._table_mouse_move(event)
            elif tp == QEvent.Type.MouseButtonRelease:
                self._hover_cancel()
                return self._table_mouse_release(event)
            elif tp == QEvent.Type.Leave:
                self._hover_cancel()
                return False
            elif tp == QEvent.Type.FocusOut:
                self._hover_cancel()
                self._review.show_browsing([])
                self._ai_focus_lost = True
                return False
            elif tp == QEvent.Type.FocusIn:
                self._ai_focus_lost = False
                if self._focus.selected_snaps:
                    self._emit_indicator(self._focus.selected_snaps)
                return False

        # ── AI 建议表格 viewport ──
        ai_tbl = getattr(getattr(self, "_review", None), "_preview_table", None)
        if ai_tbl is not None and obj is ai_tbl.table.viewport():
            tp = event.type()
            if tp in (QEvent.Type.MouseMove, QEvent.Type.Enter):
                self._hover_cancel()
                return False
            elif tp == QEvent.Type.Leave:
                self._hover_cancel()
                return False

        return QMainWindow.eventFilter(self, obj, event)

    def _table_mouse_press(self, event):
        from PySide6.QtWidgets import QApplication

        # ── 右键：仅保存快照，不修改选择 ──
        if event.button() == Qt.MouseButton.RightButton:
            self._context_saved_snaps = self._get_selected_snaps_sorted()
            return False

        modifiers = QApplication.keyboardModifiers()
        row = self.table.rowAt(int(event.position().toPoint().y()))
        snap_i = self._row_op_map.get(row)
        if snap_i is None:
            return False

        if modifiers == Qt.KeyboardModifier.NoModifier:
            # 纯点击：选 snap，鼠标拖拽才启动框选
            self._rubber_origin_snap = snap_i
            self._rubber_active = False
            self._set_selected_snaps({snap_i}, emit_indicator=False)
        elif modifiers == Qt.KeyboardModifier.ControlModifier:
            self._rubber_active = False
            self._rubber_origin_snap = None
            new = set(self._focus.selected_snaps)
            if snap_i in new:
                new.discard(snap_i)
            else:
                new.add(snap_i)
            self._set_selected_snaps(new)
        elif modifiers == Qt.KeyboardModifier.ShiftModifier:
            self._rubber_active = False
            self._rubber_origin_snap = None
            if self._focus.selected_snaps:
                lo = min(min(self._focus.selected_snaps), snap_i)
                hi = max(max(self._focus.selected_snaps), snap_i)
                new = set()
                for r in range(self.table.rowCount()):
                    si = self._row_op_map.get(r)
                    if si is not None and lo <= si <= hi:
                        new.add(si)
                self._set_selected_snaps(new)
            else:
                self._set_selected_snaps({snap_i})
        return False

    def _table_hover_track(self, event):
        """跟踪鼠标移动：在原文/译文列悬停时启动延迟定时器。

        框选模式下不跟踪（_rubber_active 时跳过）。
        """
        from dualign.gui.text_hover import TextHoverPopup

        # 框选拖拽中不跟踪悬停
        if getattr(self, "_rubber_active", False):
            self._hover_cancel()
            return

        pos = event.position().toPoint()
        item = self.table.itemAt(pos)
        if item is None:
            self._hover_cancel()
            return

        col = item.column()
        if col not in (5, 6):
            self._hover_cancel()
            return

        # itemAt 对合并单元格返回 span anchor（左上格），
        # 记录 anchor 行号以确保后续 item(row, col) 有值
        row = item.row()
        snap_i = self._row_op_map.get(row)
        if snap_i is None:
            self._hover_cancel()
            return

        # 同一单元格持续悬停 → 不重置定时器
        if row == self._hovered_row and col == self._hovered_col:
            return

        # 切换了单元格 → 重置延迟
        self._hovered_row = row
        self._hovered_col = col
        self._hovered_snap = snap_i
        self._hover_is_ai = False
        self._hovered_pos = pos
        TextHoverPopup.hide_text()
        self._hover_timer.start()

    def _hover_cancel(self):
        """取消悬停弹窗。"""
        self._hover_timer.stop()
        self._hovered_row = -1
        self._hovered_col = -1
        self._hovered_snap = -1
        self._hover_is_ai = False
        self._hovered_pos = None
        from dualign.gui.text_hover import TextHoverPopup

        TextHoverPopup.hide_text()

    def _on_hover_show(self):
        """悬停延迟到期：仅对星标单元格弹出初始文本悬浮窗。

        以 snap 侧为单位聚合所有子行的初始文本。无星标不弹窗。
        仅主对齐表有悬浮窗，AI 建议表依赖原生 tooltip。
        """
        from dualign.gui.text_hover import TextHoverPopup
        from dualign.gui.base_table import CHANGED_FLAG_ROLE

        snap_i = getattr(self, "_hovered_snap", -1)
        if snap_i < 0:
            return
        col = getattr(self, "_hovered_col", -1)

        cell = self.table.item(getattr(self, "_hovered_row", -1), col)
        if cell is None or not cell.data(CHANGED_FLAG_ROLE):
            self._hover_cancel()
            return

        # 聚合该 snap 该侧所有子行的初始文本
        initial_text = ""
        cell_rect = None
        if self._repair_state is not None:
            snapshot = self._repair_state.snapshot
            if 0 <= snap_i < len(snapshot.original_ops):
                s_idx, t_idx, _ = snapshot.original_ops[snap_i]
                if col == 5:
                    if s_idx:
                        initial_text = "\n".join(snapshot.src_text(i) for i in s_idx)
                    else:
                        initial_text = "（初始为空）"
                elif col == 6:
                    if t_idx:
                        initial_text = "\n".join(snapshot.tgt_text(j) for j in t_idx)
                    else:
                        initial_text = "（初始为空）"

        if not initial_text:
            self._hover_cancel()
            return

        # 用已确认存在的 item 直接获取 visualRect
        # cell 已在上面通过 table.item(_hovered_row, col) 确认有效，
        # 无需遍历 _render_cache_rows 避免合并单元格错位。
        cell_rect = self.table.visualItemRect(cell)

        TextHoverPopup.show_initial(
            self,
            initial_text,
            cell_rect,
            self.table.viewport(),
            column=col,
        )

    def _on_hover_theme_changed(self):
        """主题切换时刷新悬浮窗样式。"""
        from dualign.gui.text_hover import TextHoverPopup

        TextHoverPopup.adjust_theme()

    def _table_mouse_move(self, event):
        if self._rubber_active is not True and self._rubber_origin_snap is not None:
            # 首次移动：启动框选
            self._rubber_active = True
            self._hover_cancel()
        if not self._rubber_active or self._rubber_origin_snap is None:
            return False
        row = self.table.rowAt(int(event.position().toPoint().y()))
        snap_i = self._row_op_map.get(row)
        if snap_i is None:
            return False
        lo = min(self._rubber_origin_snap, snap_i)
        hi = max(self._rubber_origin_snap, snap_i)
        new = set()
        for r in range(self.table.rowCount()):
            si = self._row_op_map.get(r)
            if si is not None and lo <= si <= hi:
                new.add(si)
        self._set_selected_snaps(new, emit_indicator=False)
        return False

    def _table_mouse_release(self, event):
        if self._rubber_active:
            self._rubber_active = False
            self._emit_indicator(self._focus.selected_snaps)
            # 阻止后续 itemClicked → _on_row_clicked 覆盖框选结果
            self._suppress_next_click = True
        self._rubber_origin_snap = None
        return False

    def _on_row_double_clicked(self, row: int):
        """双击行 → 打开编辑对话框。"""
        snap_i = self._row_op_map.get(row)
        if snap_i is not None:
            self.do_edit_single(snap_i)

    def _on_context_menu(self, pos):
        """右键菜单 — 根据当前选中文本对动态显示可用操作。"""
        menu = QMenu(self)

        # 从右键保存的快照恢复选择（防止 Qt 内建右键行为重置多选）
        selected_snaps: List[int] = getattr(self, "_context_saved_snaps", []) or []
        if not selected_snaps:
            selected_snaps = self._get_selected_snaps_sorted()
        if not selected_snaps:
            return

        # 如果多选被 Qt 重置，从真理源恢复表格视觉选中
        if len(selected_snaps) > 1 and len(self._get_selected_snaps_sorted()) == 1:
            self._set_selected_snaps(set(selected_snaps), emit_indicator=False)

        snap_i = selected_snaps[0]

        # ── 复制格式 ──
        copy_menu = menu.addMenu("📋 复制格式")
        copy_md = copy_menu.addAction("Markdown 表格")
        copy_md.triggered.connect(lambda: self._copy_snap_as_markdown(selected_snaps))
        copy_tsv = copy_menu.addAction("TSV（制表符分隔）")
        copy_tsv.triggered.connect(lambda: self._copy_snap_as_tsv(selected_snaps))
        menu.addSeparator()

        ops = RepairService.valid_operations(self._repair_state, snap_i)

        if ops.get("merge"):
            a = menu.addAction("合并 [M]")
            a.triggered.connect(lambda: self.do_merge(snap_i))
        if ops.get("split_tgt") or ops.get("split_src"):
            a = menu.addAction("拆分 [S]")
            a.triggered.connect(lambda: self.do_split(snap_i))

        # 跨 snap 合并 — 多选时可用
        if len(selected_snaps) > 1:
            menu.addSeparator()
            a = menu.addAction(f"⤓ 合并选中 ({len(selected_snaps)} → 1) [M]")
            a.setToolTip("将多个 snap 捆绑合并为一个文本对，原文和译文均合并")
            a.triggered.connect(lambda: self.do_bundle_snaps(selected_snaps))

        menu.addSeparator()

        if len(selected_snaps) > 1:
            a = menu.addAction(f"校订选中 ({len(selected_snaps)} 组) [E]")
            a.triggered.connect(lambda: self.do_edit_selected(selected_snaps))
        elif ops.get("edit"):
            a = menu.addAction("校订 [E]")
            a.triggered.connect(lambda: self.do_edit_single(snap_i))

        a = menu.addAction("审核通过")
        a.setEnabled(ops.get("ok", False))
        a.triggered.connect(lambda: self.do_ok(snap_i))
        a = menu.addAction("标记异常")
        a.triggered.connect(lambda: self.do_flag(snap_i))
        if len(selected_snaps) > 1:
            a = menu.addAction(f"✕ 删除选中 ({len(selected_snaps)} 组)")
            a.triggered.connect(lambda: self._delete_selected_snaps(selected_snaps))
        else:
            a = menu.addAction("✕ 删除")
            a.triggered.connect(lambda: self.do_delete(snap_i))

        if ops.get("placeholder"):
            a = menu.addAction("▸ 占位")
            a.triggered.connect(lambda: self.do_placeholder(snap_i))

        menu.addSeparator()

        if ops.get("reset"):
            a = menu.addAction("↺ 重置")
            a.triggered.connect(lambda: self.do_reset(snap_i))

        # ── AI 选项 ──
        menu.addSeparator()
        a = menu.addAction("AI 分析此对")
        a.triggered.connect(lambda: self._review.analyze_snaps([snap_i]))
        if len(selected_snaps) > 1:
            a = menu.addAction(f"AI 批量分析 ({len(selected_snaps)} 对)")
            a.triggered.connect(lambda: self._review.analyze_snaps(selected_snaps))

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _copy_snap_as_markdown(self, snaps: List[int]):
        """将选中 snaps 的文本复制为 Markdown 表格格式。"""
        text = self._format_snaps(snaps, fmt="markdown")
        QApplication.clipboard().setText(text)
        self._safe_status(f"📋 已复制 {len(snaps)} 个 snap (Markdown)")

    def _copy_snap_as_tsv(self, snaps: List[int]):
        """将选中 snaps 的文本复制为 TSV 格式。"""
        text = self._format_snaps(snaps, fmt="tsv")
        QApplication.clipboard().setText(text)
        self._safe_status(f"📋 已复制 {len(snaps)} 个 snap (TSV)")

    def _format_snaps(self, snaps: List[int], fmt: str = "markdown") -> str:
        """将 snaps 格式化为人类可读文本。

        fmt='markdown': 单张统一 Markdown 表格（多 snap 时合并为一张表）
        fmt='tsv':      制表符分隔，便于粘贴到 Excel
        """
        if self._repair_state is None:
            return ""
        snap = self._repair_state.snapshot
        # 获取当前表格视图（含当前状态文本）
        from dualign.services.repair import make_table_view

        view = make_table_view(self._repair_state)
        # 按 snap_index 分组当前行
        cur_rows: Dict[int, List[TableRow]] = {}
        for r in view.rows:
            cur_rows.setdefault(r.snap_index, []).append(r)

        if fmt == "markdown":
            # ── 统一单张 Markdown 表格 ──
            md_lines: List[str] = []
            md_lines.append("| Snap | 类型 | 标记 | 原文 | 译文 |")
            md_lines.append("|---|---|---|---|---|")
            for si in snaps:
                if si >= len(snap.original_ops):
                    continue
                s_idx, t_idx, sc = snap.original_ops[si]
                ls, lt = len(s_idx), len(t_idx)
                init_type = f"{ls}:{lt}"
                rows = cur_rows.get(si, [])
                r0 = rows[0] if rows else None
                marker = r0.marker if r0 else ""
                cur_src_lines = [r.src_text for r in rows]
                cur_tgt_lines = [r.tgt_text for r in rows]
                cnt = max(len(cur_src_lines), len(cur_tgt_lines))
                for k in range(cnt):
                    s = cur_src_lines[k] if k < len(cur_src_lines) else ""
                    t = cur_tgt_lines[k] if k < len(cur_tgt_lines) else ""
                    snap_label = str(si) if k == 0 else ""
                    type_label = init_type if k == 0 else ""
                    marker_label = marker if (k == 0 and marker) else ""
                    # 转义管道符和换行
                    s = s.replace("|", "\\|").replace("\n", " ")
                    t = t.replace("|", "\\|").replace("\n", " ")
                    md_lines.append(
                        f"| {snap_label} | {type_label} | {marker_label} | {s} | {t} |"
                    )
            md_lines.append("")
            return "\n".join(md_lines)

        # ── TSV 格式 ──
        lines: List[str] = []
        for si in snaps:
            if si >= len(snap.original_ops):
                continue
            s_idx, t_idx, sc = snap.original_ops[si]
            ls, lt = len(s_idx), len(t_idx)
            init_type = f"{ls}:{lt}"
            rows = cur_rows.get(si, [])
            r0 = rows[0] if rows else None
            cur_type = r0.cur_type if r0 else init_type
            cur_score = r0.score if r0 else float(sc)
            marker = r0.marker if r0 else ""
            cur_src_lines = [r.src_text for r in rows]
            cur_tgt_lines = [r.tgt_text for r in rows]

            marker_s = f" [{marker}]" if marker else ""
            lines.append(
                f"snap[{si}]\tinit={init_type}\tscore={sc:.1%}"
                f"\tcur={cur_type}\tscore={cur_score:.1%}{marker_s}"
            )
            cnt = max(len(cur_src_lines), len(cur_tgt_lines))
            for k in range(cnt):
                s = cur_src_lines[k] if k < len(cur_src_lines) else ""
                t = cur_tgt_lines[k] if k < len(cur_tgt_lines) else ""
                lines.append(f"{k+1}\t{s}\t{t}")

        return "\n".join(lines)

    def _refresh(self):
        """刷新 UI。_apply_filter 内部会调用 _rebuild_anomalies。"""
        if self._repair_state is None:
            return
        # 预览模式：清除评分缓存（_preview_scores/_preview_async_scores），
        # 强制 _render_preview 重新请求评分以反映最新修复状态
        if getattr(self, "_preview_active", False):
            if hasattr(self, "_preview_scores"):
                self._preview_scores = None
            if hasattr(self, "_preview_async_scores"):
                self._preview_async_scores = None
        self._apply_filter()
        self._update_doc_summary()
        self._update_status_bar()
        self._sync_undo_redo()
        # 操作后立即触发一次轮询，加速 pending snap 的评分刷新
        _sm = getattr(self, "_score_mgr", None)
        if _sm is not None:
            _sm.poll_now()

    # ══════════════════════════════════════════════════════════
    # ScoreManager 集成
    # ══════════════════════════════════════════════════════════

    def _load_initial_scores(self):
        """从持久化 _score_cache 恢复分数，注册文本提供器 + 启动轮询。"""
        if self._repair_state is None:
            return
        self._score_mgr.set_text_provider(self._get_subrow_text_for_score)
        n_loaded = 0
        for g in self._repair_state.current.groups:
            for r in g.rows:
                key = f"{g.snap_i}_{r.sub}"
                score = getattr(self, "_score_cache", {}).get(key)
                if score is not None:
                    self._score_mgr.set_ready_score(g.snap_i, r.sub, score)
                    n_loaded += 1
                else:
                    # 无持久化分数→留 pending，_poll 异步重算
                    self._score_mgr.set_ready_score(g.snap_i, r.sub, r.score)
                    self._score_mgr.invalidate(g.snap_i, r.sub)
        self._score_mgr.start_polling()

    def _get_subrow_text_for_score(self, snap_index: int, sub: int):
        """供 ScoreManager 轮询使用的文本提供器。按子行独立评分。

        Returns:
            (src_text, tgt_text) or None（子行不存在时）
        """
        if self._repair_state is None:
            return None
        g = self._repair_state.current.group(snap_index)
        if g is None or sub >= len(g.rows):
            return None
        r = g.rows[sub]
        return (r.src_text or "", r.tgt_text or "")

    def _on_score_updated(self, snap_index: int, sub: int, new_score: float):
        """单子行分数就绪 → 持久化 + 更新单元格。"""
        # 持久化
        key = f"{snap_index}_{sub}"
        if not hasattr(self, "_score_cache"):
            self._score_cache = {}
        self._score_cache[key] = new_score

        if getattr(self, "_render_in_progress", False):
            return

        show_scores = (
            getattr(self, "_filter_panel", None) and self._filter_panel.show_scores
        )
        for row_idx, row in enumerate(getattr(self, "_render_cache_rows", [])):
            if row.snap_index == snap_index and row.sub == sub:
                cell = self.table.item(row_idx, 4)
                if cell:
                    from dualign.gui.base_table import make_score_cell

                    new_cell = make_score_cell(new_score, show_scores, precision=1)
                    self.table.setItem(row_idx, 4, new_cell)
                break

    def _on_preview_flat_ready(self, batch_id: int, scores):
        """预览表扁平评分异步就绪。"""
        # 丢弃旧批次
        if batch_id != getattr(self, "_preview_batch_id", 0):
            return
        if scores is not None:
            self._preview_async_scores = scores
        else:
            # 失败 → 全 0
            import numpy as np

            self._preview_async_scores = np.zeros(
                len(getattr(self, "_preview_async_scores", []) or []),
                dtype=np.float64,
            )
        self._render_preview()

    def _on_score_status_changed(self, snap_index: int, sub: int, state: str):
        """评分状态变更。"""
        if state == "failed":
            self._safe_status(f"snap[{snap_index}] 评分计算失败")
            self._set_temp_status(f"snap[{snap_index}] 评分计算失败", "warning")

    def _on_theme_changed(self, scheme: str):
        """主题切换（dark/light）时刷新所有 QSS 依赖色。

        只刷新结构色（dock、表格线、分隔线等），
        Fusion QPalette 已自动处理通用控件颜色。
        """
        from dualign.gui.theme import T as _t

        # 网格线由 BaseTextTable 基类统一管理——仅刷新颜色变量
        self.table.setStyleSheet(
            "QTableWidget { outline: none; }"
            f"QTableWidget::item {{"
            f"  border-right: 1px solid {_t.BORDER_DIM};"
            f"  padding: 2px;"
            f"}}"
            "QTableWidget::item:hover {"
            "  background: transparent;"
            "}"
        )
        if hasattr(self, "_preview_table"):
            self._preview_table.setStyleSheet(
                "QTableWidget { outline: none; }"
                f"QTableWidget::item {{"
                f"  border-right: 1px solid {_t.BORDER_DIM};"
                f"  padding: 2px;"
                f"}}"
                "QTableWidget::item:hover {"
                "  background: transparent;"
                "}"
            )
        # 状态栏背景
        if hasattr(self, "_status_dots") and self._status_dots:
            pass

            # 不需要显式设置，QPalette 已处理

    def _rebuild_anomalies(self, k: Optional[float] = None):
        """重建异常列表。使用 SnapState 体系。
        Args:
            k: Z-score 阈值，为 None 时使用默认值 3.0。
        """
        if self._repair_state is None:
            self._anomalies = []
            return

        from dualign.models.snap_state import build_snap_states, refresh_snap_states

        state = self._repair_state
        ch = state.current
        snap = state.snapshot

        # 构建 SnapState
        snap_states = build_snap_states(
            snapshot=snap,
            src_lines=list(snap.original_src_lines),
            tgt_lines=list(snap.original_tgt_lines),
            repair_log=state.repair_log,
            k=k,
        )
        snap_states = refresh_snap_states(snap_states, snap, ch, state.repair_log)

        # ── 记录所有含异常类型的 snap（不受筛选影响），供 _apply_filter 判断"纯 1:1"用 ──
        self._all_anomaly_snaps = {
            si for si, st in enumerate(snap_states) if st.anomaly_types
        }

        # 从 filter_panel 获取筛选条件
        review = getattr(self, "_review", None)
        fp = (
            review._filter_panel
            if review and hasattr(review, "_filter_panel")
            else None
        )
        if fp is None:
            self._anomalies = []
            self._review.set_anomalies([])
            return

        sf = fp.snap_filter
        self._anomalies = []
        # 直接构建当前文本异常映射，供 _render_table 读取
        self._current_atypes_map: Dict[int, Set[str]] = {}

        for g in ch.groups:
            si = g.snap_i
            st = snap_states[si]
            r0 = g.rows[0]
            action = state.action_for_op(si)

            # ── 异常匹配（同组 OR）──
            # 按检测依据模式选择 anomaly 来源：
            #   - 初始文本: SnapState.initial_anomaly_types（Layer 1 不可变事实）
            #   - 当前文本: SnapState.current_anomaly_types（Layer 2 可变状态）
            # FLAGGED 不是对齐器检测的，独立于 ref_current 模式处理
            atypes = (
                st.current_anomaly_types if fp.ref_current else st.initial_anomaly_types
            )
            origin_match = (
                any(k in atypes for k in fp.active_origin_keys if k != "FLAGGED")
                or ("FLAGGED" in fp.active_origin_keys and st.is_flagged)
                if fp.active_origin_keys
                else False
            )

            # ── 处理状态匹配（同组 OR）──
            state_match = (
                st.approval in fp.active_state_keys if fp.active_state_keys else False
            )

            # ── 跨组逻辑：AND 或 OR ──
            if sf.cross_group_op == "AND":
                if not origin_match or not state_match:
                    continue
            else:  # OR
                if not origin_match and not state_match:
                    continue

            # 无任何异常特征且无操作 → 跳过
            has_origin = bool(atypes)
            if not has_origin and action is None and not st.is_deleted:
                continue

            # 跨 snap 校订
            snap_indices = [si]
            resolution = action.kind if action else ""
            if action and action.data.get("orig_snaps"):
                snap_indices = sorted(action.data["orig_snaps"])

            self._anomalies.append(
                {
                    "snap_index": si,
                    "snap_indices": snap_indices,
                    "src_text": r0.src_text,
                    "tgt_text": r0.tgt_text,
                    "init_type": r0.init_type,
                    "cur_type": r0.cur_type,
                    "score": r0.score,
                    "marker": r0.marker,
                    "resolution": resolution,
                    "approval": st.approval,
                    "signals": st.signals,
                    "anomaly_types": st.initial_anomaly_types,
                }
            )
            # 构建当前文本异常映射（col 3 Layer 2 用）
            self._current_atypes_map[si] = set(st.current_anomaly_types)

        self._review.set_anomalies(self._anomalies)

    def _apply_filter(self):
        """筛选 + 渲染表格。先重建异常列表以反映最新质量控制勾选。

        预览模式：按原始行索引平坦排列，诚实展示逐行对照和错位问题。
        拒绝文档（不可靠对齐）自动锁定预览模式，此时无 snap 概念。
        """
        self._rebuild_anomalies()

        if self._repair_state is None:
            return

        # ── 不可靠对齐自动锁定预览模式 ──
        qa = getattr(self, "_last_quality_assessment", None)
        is_unreliable = qa and qa.get("quality") == "unreliable"
        if is_unreliable and not self._preview_active:
            self._on_view_mode_toggled(True)
            # 视图模式开关也会相应更新（由 toggled 信号链触发）

        # 切换预览/普通模式 — 由 StatusBar 视图模式切换触发
        preview = self._preview_active
        if preview:
            self._render_preview()
            return

        view = make_table_view(self._repair_state)
        all_rows = view.rows

        anomaly_snaps: Dict[int, Set[str]] = {}
        anomaly_signals: Dict[int, List[str]] = {}
        for a in self._anomalies:
            si = a["snap_index"]
            if si not in anomaly_snaps:
                anomaly_snaps[si] = set()
            anomaly_snaps[si].update(a["anomaly_types"])
            sigs = a.get("signals", [])
            if sigs:
                anomaly_signals[si] = sigs
        # FLAGGED 在 initial_anomaly_types 中已移除，但颜色条和异常标记仍需展示
        for a in self._anomalies:
            if a.get("resolution") == "flag":
                si = a["snap_index"]
                anomaly_snaps.setdefault(si, set()).add("FLAGGED")
        ctx = self._filter_panel.context_lines
        context_snaps: Set[int] = set()

        # ── 确定应当显示的 snap 集合 ──
        show_snaps = set(anomaly_snaps.keys())
        for snap_i in show_snaps:
            for offset in range(-ctx, ctx + 1):
                if offset != 0:
                    context_snaps.add(snap_i + offset)

        # ── 筛选表行（含 FocusManager.force_show_snaps 覆盖）──
        filtered: List[TableRow] = []
        self._row_op_map = {}
        force_show = self._focus.force_show_snaps if hasattr(self, "_focus") else set()
        for row in all_rows:
            is_anomaly = row.snap_index in anomaly_snaps
            is_context = row.snap_index in context_snaps and not is_anomaly
            is_force = row.snap_index in force_show
            # show_all: 所有未被当前筛选标记为异常的行以普通行显示
            # 不引入 _all_anomaly_snaps——用户取消勾选的异常类对应的 snap
            # 应以普通行出现在表中，而非被隐藏。
            is_plain = (
                self._filter_panel.show_all
                and not is_anomaly
                and not is_context
                and not is_force
            )

            if is_anomaly or is_context or is_force or is_plain:
                filtered.append(row)
                self._row_op_map[len(filtered) - 1] = row.snap_index

        self._render_table(filtered, anomaly_snaps, anomaly_signals, context_snaps)

        # ── 无异常且非显示全部 → 切换到空状态提示页 ──
        if not self._filter_panel.show_all and not filtered:
            if hasattr(self, "_table_stack") and self._table_stack.count() > 2:
                self._table_stack.setCurrentIndex(2)
                n_total = (
                    len(self._repair_state.snapshot.original_ops)
                    if self._repair_state
                    else 0
                )
                self._empty_subtitle.setText(
                    f"共检查了 {n_total} 对文本，当前异常类型筛选条件下，未发现异常文本对。"
                )
        elif hasattr(self, "_table_stack") and self._table_stack.count() > 2:
            # 有渲染行时切回主表（防止空状态页残留）
            if self._table_stack.currentIndex() == 2:
                self._table_stack.setCurrentIndex(0)

    def _switch_table_mode(self, preview: bool):
        """切换主表/预览表之间的 QStackedWidget 堆叠页。"""
        if hasattr(self, "_table_stack"):
            self._table_stack.setCurrentIndex(1 if preview else 0)

    # ── 预览模式辅助 ──

    def _get_flat_lines(self) -> tuple[list[str], list[str]]:
        """从 RepairState 提取两侧平坦行。

        原文列 = 按序收集当前状态的原文行
        译文列 = 按序收集当前状态的译文行
        两侧长度可以不等，不插入空行补位，不按 snap 配对。

        规则:
          - [D] 删除 → 整组跳过不输出
          - [M] 合并 → _smart_join 多行文本为一行（与 render_rows/导出一致）
          - [E]/[S]/[P] → 取 AlignedRow 的内联文本
          - 无标记 → 取 AlignedRow 的原始文本，短侧空行跳过

        Returns:
            (src_lines, tgt_lines)
        """
        if self._repair_state is None:
            return [], []

        from dualign.core import _smart_join_lines

        ch = self._repair_state.current
        snap = self._repair_state.snapshot
        src_out: list[str] = []
        tgt_out: list[str] = []

        for g in ch.groups:
            if not g.rows:
                continue
            r0 = g.rows[0]
            marker = r0.marker

            if is_deleted(marker):
                continue

            if is_merge(marker):
                # [M]: 智能合并为一行（与 render_rows / 导出文件一致）
                s_idx, t_idx, _ = snap.original_ops[g.snap_i]
                src_out.append(_smart_join_lines([snap.src_text(i) for i in s_idx]))
                tgt_out.append(_smart_join_lines([snap.tgt_text(j) for j in t_idx]))
            else:
                # [E]/[S]/[P]/[OK]/[F]/无标记: 取子行文本，跳过空串
                for r in g.rows:
                    if r.src_text:
                        src_out.append(r.src_text)
                    if r.tgt_text:
                        tgt_out.append(r.tgt_text)

        return src_out, tgt_out

    def _render_preview(self):
        """预览模式：平坦铺展文本，诚实展示逐行对照。

        列: 行号 | 相似度(色带) | 原文 | 译文

        单一路径：
          1. 文本：有 RepairState 则 _get_flat_lines()（两侧独立收集），否则 src_lines/tgt_lines
          2. 评分：SimilarityScorer.score_pairs()，异常时退化为 0.0
          3. 缺行：淡橙高亮

        核心语义：
          - 两侧各自独立按序铺陈，不按 snap 配对，不插入空行补位
          - 2:1 snap → src 2 行、tgt 1 行，下一 snap 的 tgt 自动补位到第二行
          - [D] 删除 → 整组不输出
        """
        from dualign.gui.base_table import make_score_cell

        table = self._preview_table

        # ── 评分明细开关 ──
        fp = self._filter_panel
        show_scores = fp.show_scores if hasattr(fp, "show_scores") else True
        hdr = table.horizontalHeader()
        table.setColumnHidden(1, False)
        hdr.resizeSection(1, 60 if show_scores else 6)
        table.horizontalHeaderItem(1).setText("当前评分" if show_scores else "")
        table.horizontalHeaderItem(0).setText("行")  # 行号表头始终展示

        # ── 1. 获取当前文本（两侧独立铺陈）──
        if self._repair_state is not None:
            src_out, tgt_out = self._get_flat_lines()
        else:
            src_out = list(getattr(self, "src_lines", []) or [])
            tgt_out = list(getattr(self, "tgt_lines", []) or [])

        if not src_out and not tgt_out:
            table.setRowCount(0)
            return

        n_rows = max(len(src_out), len(tgt_out))

        # ── 2. 评分 ──
        # 四态 (优先级递减):
        #   1. _preview_scores (编码点积快速路径)
        #   2. _preview_async_scores (ScoreManager 异步路径)
        #   3. loading: 通过 ScoreManager 请求异步评分，灰色占位
        #   4. 无 scorer → 全 0
        scores = None
        # 状态 1: 编码点积快速路径
        if hasattr(self, "_preview_scores"):
            _ps = self._preview_scores
            if _ps is not None and len(_ps) > 0:
                import numpy as np

                n = len(_ps)
                scores = np.zeros(n_rows, dtype=np.float64)
                scores[: min(n, n_rows)] = _ps[: min(n, n_rows)]

        # 状态 2: ScoreManager 异步路径
        if scores is None and hasattr(self, "_preview_async_scores"):
            _pas = self._preview_async_scores
            if _pas is not None and len(_pas) > 0:
                import numpy as np

                n = len(_pas)
                scores = np.zeros(n_rows, dtype=np.float64)
                # 防御：ScoreManager 竞态可能导致 _pas 长度异常
                scores[: min(n, n_rows)] = _pas[: min(n, n_rows)]

        # 状态 3: 请求异步评分
        if scores is None and src_out and tgt_out:
            _sm = getattr(self, "_score_mgr", None)
            if _sm is not None and _sm.has_scorer:
                # 如果上一个 batch 有 pending 标记但从未收到回调（如被
                # subrow 评分覆盖），标记为 None 并重新请求
                self._preview_async_scores = None
                self._preview_batch_id = getattr(self, "_preview_batch_id", 0) + 1
                _sm.flat_batch_ready.connect(
                    self._on_preview_flat_ready, Qt.ConnectionType.UniqueConnection
                )
                _sm.request_flat_batch(src_out, tgt_out, self._preview_batch_id)
                pass
            else:
                # 无 scorer → 全 0
                import numpy as np

                scores = np.zeros(n_rows, dtype=np.float64)

        # ── 3. 渲染表格 ──
        table.setUpdatesEnabled(False)
        table.setRowCount(0)
        table.setRowCount(n_rows)
        table.clearContents()

        for ri in range(n_rows):
            # Col 0: 行号
            it_row = QTableWidgetItem(str(ri + 1))
            it_row.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            it_row.setFlags(it_row.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(ri, 0, it_row)

            # Col 1: 余弦相似度
            if scores is not None:
                score = float(scores[ri]) if ri < len(scores) else 0.0
                table.setItem(ri, 1, make_score_cell(score, show_scores))
            else:
                # 加载中：灰色 "…" 占位
                it_score = QTableWidgetItem("…")
                it_score.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                it_score.setFlags(it_score.flags() & ~Qt.ItemFlag.ItemIsEditable)
                it_score.setForeground(QColor("#9e9e9e"))
                table.setItem(ri, 1, it_score)

            # Col 2: 原文
            has_src = ri < len(src_out)
            it_src = QTableWidgetItem(src_out[ri] if has_src else "")
            it_src.setFlags(it_src.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(ri, 2, it_src)

            # Col 3: 译文
            has_tgt = ri < len(tgt_out)
            it_tgt = QTableWidgetItem(tgt_out[ri] if has_tgt else "")
            it_tgt.setFlags(it_tgt.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(ri, 3, it_tgt)

            # 缺行高亮
            if not (has_src and has_tgt):
                for it in (it_src, it_tgt):
                    it.setBackground(QColor(255, 152, 0, 30))

        table.setUpdatesEnabled(True)
        table.resizeRowsToContents()
        for ri in range(n_rows):
            table.setRowHeight(ri, table.rowHeight(ri) + 3)

    # ── _render_table 子逻辑（纯数据计算，提取以降低圈复杂度）──

    @staticmethod
    def _compute_divider_cells(
        rows: List[TableRow],
        spans: dict,
        spanned_cells: Set[Tuple[int, int]],
    ) -> Set[Tuple[int, int]]:
        """计算合并 [M] 行之间的分隔虚线单元格。"""
        divider_cells: Set[Tuple[int, int]] = set()
        for ri, row in enumerate(rows):
            if (
                row.sub > 0
                and ri - 1 >= 0
                and rows[ri - 1].snap_index == row.snap_index
            ):
                first = rows[ri - row.sub]
                if "[M]" in (first.marker or ""):
                    prev = ri - 1
                    for col in (5, 6):
                        if (prev, col) not in spanned_cells and (
                            prev,
                            col,
                        ) not in spans:
                            divider_cells.add((prev, col))
        return divider_cells

    @staticmethod
    def _compute_snap_display(row: TableRow) -> str:
        """计算 Snap 列显示文本（col 0）。"""
        if row.sub != 0:
            return ""
        init_lines = row.init_type.split("\n") if row.init_type else []
        snap_nums = []
        for ln in init_lines:
            if ln.startswith("snap "):
                try:
                    snap_nums.append(int(ln.split()[1]))
                except (IndexError, ValueError):
                    pass
        if len(snap_nums) > 1:
            return "\n---\n".join(str(s) for s in snap_nums)
        return str(row.snap_index)

    @staticmethod
    def _compute_init_type_display(row: TableRow) -> str:
        """计算初始类型列显示文本（col 1）。"""
        init_lines = row.init_type.split("\n") if row.init_type else []
        bundled = len(init_lines) > 1 and ("---" in row.init_type)
        if bundled and row.sub == 0:
            pure_types = [ln for ln in init_lines if not ln.startswith("snap ")]
            return "  ".join(pure_types)
        elif bundled:
            return init_lines[-1] if len(init_lines) >= 2 else ""
        elif row.sub == 0 or row.init_type:
            return row.init_type
        return ""

    @staticmethod
    def _compute_cur_type_text(
        row: TableRow, cur_atypes: Set[str], show_cur: bool
    ) -> str:
        """计算当前状态列文本（col 3, 两层: 标记 + 异常标签）。"""
        if not show_cur:
            return ""
        l1 = row.marker if row.marker else ""
        l2 = format_anomaly_line(cur_atypes)
        if l1 and l2:
            return f"{l1}\n{l2}"
        elif l1:
            return l1
        elif l2:
            return l2
        return ""

    def _compute_snap_text_changes(
        self, rows: List[TableRow]
    ) -> Tuple[Set[int], Set[int]]:
        """预计算 snap 级文本变化标记（用于星标）。

        遍历每个 snap 的全部 repair_log actions，只要有任何一条操作
        改变了文本内容（edit/placeholder/split），即标星。
        返回 (changed_src_snaps, changed_tgt_snaps)。
        """
        snap_changed_src: Set[int] = set()
        snap_changed_tgt: Set[int] = set()
        if self._repair_state is not None:
            snapshot = self._repair_state.snapshot
            for row in rows:
                si = row.snap_index
                if si in snap_changed_src and si in snap_changed_tgt:
                    continue
                for action in self._repair_state.repair_log:
                    if action.op_index != si:
                        continue
                    sc, tc = has_snap_text_changed(si, action, snapshot)
                    if sc:
                        snap_changed_src.add(si)
                    if tc:
                        snap_changed_tgt.add(si)
                    if si in snap_changed_src and si in snap_changed_tgt:
                        break
        return snap_changed_src, snap_changed_tgt

    def _render_table(
        self,
        rows: List[TableRow],
        anomaly_snaps: Dict[int, Set[str]],
        anomaly_signals: Dict[int, List[str]],
        context_snaps: Set[int],
    ):
        """渲染 7 列表格。每格颜色在创建时即确定，无后置覆盖。

        着色规则:
          col 0 (Snap):      #888 灰色
          col 1 (init_type):  type_cl(init_type) — 1:1 灰 / N:M 金 / 1:0 红
          col 2 (init_score): score_to_color(orig) — 连续渐变
          col 3 (cur_type):    marker_cl > type_cl(cur_type) — 有标记则优先
          col 4 (cur_score):  score_to_color(cur) — 连续渐变
          col 5 (src):        [D] → 暗红+删除线 / 上下文 → 灰斜体 / 异常 → anomaly_cl / 默认 TEXT_CL_NORMAL
          col 6 (tgt):        同上
        """
        self._render_cache_rows = rows
        self._render_in_progress = True
        table = self.table
        table.setUpdatesEnabled(False)
        # 先重置 rowCount=0 再设为目标值，强制 Qt 完全重建行结构
        # 否则行数不变时 Qt 会缓存旧行，导致 setItem 后界面不刷新
        table.setRowCount(0)
        table.setRowCount(len(rows))
        table.clearSpans()
        table.clearContents()

        hdr = table.horizontalHeader()

        # 跨行合并（含 Snap 列 col 0）
        spans = compute_spans(rows, col_offset=1, snap_col=0)
        # 预计算哪些 (row, col) 被 span 覆盖（子区域），对这些格子跳过 setItem
        spanned_cells: Set[Tuple[int, int]] = set()
        for (sr, col), (rs, cs) in spans.items():
            if sr < len(rows) and rs > 1:
                table.setSpan(sr, col, rs, cs)
                for r in range(sr + 1, sr + rs):
                    for c in range(col, col + cs):
                        spanned_cells.add((r, c))

        covered_cur_rows: Set[int] = set()
        for (sr, c_), (_, cnt) in spans.items():
            if c_ == 3 and cnt > 1:
                for ri in range(sr + 1, sr + cnt):
                    covered_cur_rows.add(ri)

        # ── 单元格级分隔虚线 ──
        self._divider_delegate.set_divider_cells(
            self._compute_divider_cells(rows, spans, spanned_cells)
        )

        # ── Snap 列宽：根据当前最大 snap 索引动态调整 ──
        if self._repair_state is not None:
            _max_si = max((r.snap_index for r in rows), default=0)
            hdr.resizeSection(0, calc_snap_width(_max_si))

        # ── 预计算 snap 级文本变化标记（用于星标）──
        snap_changed_src, snap_changed_tgt = self._compute_snap_text_changes(rows)

        fp = self._filter_panel
        show_scores = fp.show_scores
        ctx_rows: Set[int] = set()  # 上下文行索引

        # 评分列：明细→数字，紧凑→6px 色带
        hdr = table.horizontalHeader()
        # 评分列切换 56px（明细）或 6px（紧凑色带）
        for col in (2, 4):
            table.setColumnHidden(col, False)
            hdr.resizeSection(col, 60 if show_scores else 6)
            table.horizontalHeaderItem(col).setText(
                COLUMN_HEADERS[col] if show_scores else ""
            )

        for i, row in enumerate(rows):
            self._row_op_map[i] = row.snap_index
            is_anomaly = row.snap_index in anomaly_snaps
            is_ctx = (
                row.snap_index in context_snaps
                and not is_anomaly
                and row.marker == ""
                and not self._filter_panel.show_all
            )
            is_del = is_deleted(row.marker)
            show_cur = (row.sub == 0) or (i not in covered_cur_rows)
            atypes = anomaly_snaps.get(row.snap_index, set())
            # col 3 Layer 2 始终基于当前文本结构，直接读取 _rebuild_anomalies 构建的映射
            cur_atypes = self._current_atypes_map.get(row.snap_index, set())
            if is_ctx:
                ctx_rows.add(i)

            # ── 文本内容 ──
            src_text = row.src_text
            tgt_text = row.tgt_text

            # ── snap 号 (col 0) ──
            snap_display = self._compute_snap_display(row)

            # ── 初始类型 (col 1) ──
            init_display = self._compute_init_type_display(row)

            # col 3: 两层显示（标记 + 异常标签）
            cur_text = self._compute_cur_type_text(row, cur_atypes, show_cur)

            # ── 预计算每格颜色 ──
            # 编辑校订：检测 edit 后文本是否与原始一致，仅变动侧着色
            src_changed = False
            tgt_changed = False
            if self._repair_state is not None:
                action = self._repair_state.action_for_op(row.snap_index)
                src_changed, tgt_changed = compute_text_colors(
                    row.snap_index,
                    row.marker,
                    atypes,
                    action,
                    self._repair_state.snapshot,
                )

            col_colors = [
                None,  # col 0: snap — 默认色
                type_cl(row.init_type),  # col 1: 初始类型色
                None,  # col 2: 评分色（由 make_score_cell 自行处理）
                (
                    marker_cl(row.marker)
                    if row.marker
                    else (
                        anomaly_cl(priority_anomaly_type(atypes))
                        if atypes
                        else _color_table.TYPE_CL_11
                    )
                ),  # col 3: Layer1 标记色 / Layer2 异常色 / 灰
                None,  # col 4: 当前评分色（由 make_score_cell 自行处理）
                text_color_for_side(
                    True, src_changed, tgt_changed, is_del, is_ctx, row.marker, atypes
                ),  # col 5: src
                text_color_for_side(
                    False, src_changed, tgt_changed, is_del, is_ctx, row.marker, atypes
                ),  # col 6: tgt
            ]
            texts = [
                snap_display,
                init_display,
                "",  # col 2: 初始评分（由 make_score_cell 填充）
                cur_text,
                "",  # col 4: 当前评分（由 make_score_cell 填充）
                src_text,
                tgt_text,
            ]

            # ── 评分单元格：紧凑模式→色带，明细模式→数字 ──
            items = []
            for ci, (txt, clr) in enumerate(zip(texts, col_colors)):
                if ci == 2:
                    # 初始评分列
                    it = make_score_cell(row.orig_score, show_scores)
                elif ci == 4:
                    # 当前评分列 — 委托 ScoreManager，按子行独立评分
                    _sm_score, _sm_state = self._score_mgr.get_score_state(
                        row.snap_index, row.sub
                    )
                    # 确保 split 等操作产生的新子行已注册——否则 _poll 永远发现不了
                    _sm_key = (row.snap_index, row.sub)
                    if _sm_state == "pending" and _sm_key not in self._score_mgr._cache:
                        self._score_mgr.invalidate(row.snap_index, row.sub)
                    it = make_score_cell(
                        _sm_score if _sm_state == "ready" else None,
                        show_scores,
                        state=_sm_state,
                        precision=1,
                    )
                else:
                    it = QTableWidgetItem(txt)
                    if clr is not None:
                        it.setForeground(clr)
                it.setData(Qt.ItemDataRole.UserRole, row.snap_index)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                items.append(it)

            # ── 变化标记：该 snap 文本内容与初始对齐输出不同时，所有行均标星 ──
            si = row.snap_index
            if si in snap_changed_src and len(items) > 5:
                items[5].setData(CHANGED_FLAG_ROLE, True)
            if si in snap_changed_tgt and len(items) > 6:
                items[6].setData(CHANGED_FLAG_ROLE, True)

            # 对齐：col 0-4 居中，col 5-6 左对齐
            for ci in range(5):
                items[ci].setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # 删除行：全列加删除线 + 暗前景
            if is_del:
                for it in items:
                    f = it.font()
                    f.setStrikeOut(True)
                    it.setFont(f)
                    it.setForeground(_color_table.TEXT_CL_DELETED)
            # 上下文行：全列斜体 + 灰前景
            elif is_ctx:
                for it in items:
                    f = it.font()
                    f.setItalic(True)
                    it.setFont(f)
                    it.setForeground(_color_table.TEXT_CL_CONTEXT)

            for ci, it in enumerate(items):
                if (i, ci) not in spanned_cells:
                    try:
                        table.setItem(i, ci, it)
                    except RuntimeError:
                        import traceback

                        traceback.print_exc()

        # ── 行高：自计算 + deficit-fill 均摊 ──
        # 基线排除所有跨行 span 锚点格，仅用非 span 列确定每行最小高度。
        # 然后对所有跨行列（col 1-6）做 deficit-fill。
        table.setWordWrap(True)
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        table.setUpdatesEnabled(True)

        font = table.font()
        col_widths = [table.columnWidth(c) for c in range(7)]
        PAD = 4

        def _cell_px(row_i: int, col_i: int) -> int:
            if (row_i, col_i) in spanned_cells:
                return 0
            it = table.item(row_i, col_i)
            if it is None:
                return 0
            txt = it.text() or ""
            if not txt:
                return 0
            cw = col_widths[col_i]
            txt_w = cw - 4
            if txt_w < 20:
                txt_w = cw
            doc = QTextDocument()
            doc.setDefaultFont(font)
            doc.setTextWidth(txt_w)
            doc.setPlainText(txt)
            return int(doc.size().height()) + PAD

        # 收集跨行 span 的所有锚点格坐标
        anchor_coords: Set[Tuple[int, int]] = set()
        for (sr, col), (rs, _) in spans.items():
            if rs > 1:
                anchor_coords.add((sr, col))

        # 基线：仅取非 span 锚点格的原生高度
        base = [0] * len(rows)
        for i in range(len(rows)):
            for ci in range(7):
                if (i, ci) in anchor_coords or (i, ci) in spanned_cells:
                    continue
                base[i] = max(base[i], _cell_px(i, ci))

        # 对每个跨行 span（col 1-6）做 deficit-fill
        for (sr, col), (rs, _) in sorted(spans.items()):
            if rs <= 1 or col == 0:
                continue
            span_h = _cell_px(sr, col)
            current = sum(base[sr : sr + rs])
            deficit = span_h - current
            if deficit > 0:
                extra = int(math.ceil(deficit / rs))
                for ri in range(sr, sr + rs):
                    base[ri] += extra

        for i, h in enumerate(base):
            table.setRowHeight(i, h)

        # 强制立即重绘
        table.viewport().update()
        from PySide6.QtCore import QTimer as _QTimer

        _QTimer.singleShot(200, table.viewport().update)

        # ── 同步所有列宽到底部预览表和 AI 建议表 ──
        _QTimer.singleShot(80, lambda: self._sync_all_preview_widths())

        # "仅焦点"模式自动选中首个异常 snap 后高亮
        if (
            hasattr(self, "_auto_select_on_render")
            and self._auto_select_on_render is not None
        ):
            snap_i = self._auto_select_on_render
            self._auto_select_on_render = None
            if snap_i is not None:
                self._focus.go_to_snap(snap_i, source="table")

        # 渲染完成后同步 HighlightDelegate 选中行/焦点行
        self._update_table_highlight()
        self._render_in_progress = False
        table.viewport().update()

    def _sync_all_preview_widths(window):
        """渲染完成后同步 AI 建议表的列宽。"""
        review = getattr(window, "_review", None)
        if review is None:
            return
        review._sync_suggestion_widths()
