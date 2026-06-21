"""
Dualign — DualignWindow: 主窗口

设计目标：GUI 层只做三件事：
  1. 展示数据 (_render_table)
  2. 响应用户操作 → _apply_action → RepairService
  3. 管理 UI 状态 (筛选/导航/历史)

不做：
  - 不直接操作 ChapterState
  - 不实现修复逻辑
  - 不计算文本输出
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path
from typing import List, Optional, Any, Set

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMessageBox,
    QAbstractItemView,
    QFileDialog,
    QApplication,
    QDialog,
)

from dualign.core import AlignmentResult
from dualign.common import load_text_lines, content_hash
from dualign.models.state import AlignmentSnapshot
from dualign.models.action import RepairAction
from dualign.services.repair import (
    RepairState,
    RepairService,
)
from dualign.gui.dialogs import BlockEditDialog
from dualign.gui.workspace import FileQueueItem
from dualign.gui.settings import (
    DualignConfig,
    KEY_LAST_OPEN_DIR,
)
from dualign.core import _smart_join_lines as _join

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
# 各列拖拽最小宽度（px），低于此值自动回弹
# 0=Snap, 1=初始类型, 2=初始评分, 3=当前类型, 4=当前评分
# 4 个中文字符 ≈ 48px + 两侧 padding ≈ 60px（评分列无 padding 但需容纳百分比值）
_COL_MIN_WIDTHS = {0: 40, 1: 60, 2: 60, 3: 60, 4: 60}


# ═══════════════════════════════════════════════════════════════
# DualignWindow — 方法实现（被 dualign.gui.window 采纳为方法）
# ═══════════════════════════════════════════════════════════════


class WindowActionsMixin:
    """WindowActionsMixin — 通过多重继承为 DualignWindow 提供方法。"""

    def _on_open_demo(self):
        """打开 Demo 示例文件对。

        路径解析委托给 dualign.demo.get_demo_paths()，
        与 demo_gui.py 逻辑完全一致。
        """
        try:
            from dualign.demo import get_demo_paths

            src, tgt, label = get_demo_paths()
            self.load_file_pair(src, tgt, label=label)
        except (ImportError, FileNotFoundError) as e:
            self._safe_status(f"Demo 文件不存在: {e}")
            self._on_open_files()

    def _on_workspace_load(self, src: str, tgt: str, label: str):
        """WorkspacePanel 请求对齐指定文件对。"""
        self.load_file_pair(src, tgt, label)

    def _on_workspace_add_queue(self):
        """＋ 添加按钮回调：弹出文件选择器，加入队列。"""
        src_path, _ = QFileDialog.getOpenFileName(
            self, "选择原文", "", "Markdown (*.md);;Text (*.txt);;All (*)"
        )
        if not src_path:
            return
        tgt_path, _ = QFileDialog.getOpenFileName(
            self, "选择译文", "", "Markdown (*.md);;Text (*.txt);;All (*)"
        )
        if not tgt_path:
            return
        label = Path(src_path).stem.split(".")[0]
        fq = FileQueueItem(label=label, src_path=src_path, tgt_path=tgt_path)
        self._workspace.add_to_queue(fq)

    def _on_workspace_align_checked(self):
        """对齐当前选中的文件对。"""
        sel = self._workspace.selected_item()
        if sel:
            self.load_file_pair(sel.src_path, sel.tgt_path, sel.label)

    def _on_workspace_remove_checked(self):
        """移除当前选中的文件对。"""
        self._workspace.remove_selected()

    def _on_workspace_nav(self, direction: int):
        """队列导航：-1=prev, 1=next。"""
        if direction < 0:
            self._workspace._nav_prev()
        else:
            self._workspace._nav_next()

    def _on_reset_current_snap(self):
        """重置当前选中文本对的修复。"""
        if self._repair_state is None:
            return
        snap_i = self._review._cur_snap_i()
        if snap_i is None or snap_i < 0:
            self._safe_status("请先在审校面板中选中一个文本对")
            return
        self._undo_stack.append(self._repair_state)
        self._repair_state = self._repair_state.reset_op(snap_i)
        if hasattr(self, "_score_mgr"):
            self._score_mgr.invalidate_snaps([snap_i])
        self._reset_accepted_proposals([snap_i])
        self._refresh()
        self._save_session()
        self._set_temp_status(f"已重置 snap[{snap_i}] 的修复", "info")

    def load_file_pair(
        self,
        src_path: str,
        tgt_path: str,
        label: str = "",
    ):
        """加载文件对并启动对齐流水线。

        缓存命中 → 跳过编码和对齐。
        缓存未命中 → EncodeThread → _on_text_ready → _on_encoded → _on_align_done。
        每次调用自动取消前一次未完成操作（_load_op_id 防旧回调污染）。
        """
        # ── 文件存在性检查 ──
        missing = []
        if not os.path.isfile(src_path):
            missing.append(f"原文: {src_path}")
        if not os.path.isfile(tgt_path):
            missing.append(f"译文: {tgt_path}")
        if missing:
            from PySide6.QtWidgets import QMessageBox

            msg = "以下文件不存在，请检查路径是否已被移动或删除：\n\n" + "\n".join(
                missing
            )
            QMessageBox.warning(self, "文件不存在", msg)
            # 从最近列表移除并刷新欢迎页
            if hasattr(self, "_workspace"):
                self._workspace.remove_recent_pair(src_path, tgt_path)
                if hasattr(self, "_welcome") and self._welcome is not None:
                    self._welcome.set_recent_pairs(self._workspace.get_recent_pairs())
            return

        # ── 取消前一次未完成的操作（停止旧线程 + 递增操作 ID）──
        self._cancel_current_load()

        self._src_path = src_path
        self._tgt_path = tgt_path

        # 推导 repaired_dir（优先使用 entry 的 repaired_dir，否则使用报告缓存目录）
        if not self._repaired_dir:
            from dualign.config import get_report_cache_dir

            self._repaired_dir = get_report_cache_dir()

        # 同步路径到工作区面板
        if hasattr(self, "_workspace"):
            self._workspace.set_file_paths(src_path, tgt_path, label or "")

        from dualign import __version__ as _v

        self.setWindowTitle(f"Dualign v{_v} — {label}")

        # ── 先推导 entry_id（用于日志和 cache，在缓存命中前就必需）──
        _entry_id = ""
        if self._current_entry:
            _entry_id = getattr(self._current_entry, "entry_id", "") or ""
        if not _entry_id:
            _entry_id = Path(src_path).stem.split(".")[0]
        self._current_entry_id = _entry_id

        # ── 初始化 SimilarityScorer（行级嵌入缓存 + 评分器）──
        # 在编码/对齐之前创建，确保任何需要评分的地方都能正常工作
        from dualign.services.similarity import SimilarityScorer

        self._scorer = SimilarityScorer(entry_id=self._current_entry_id)

        # ── 尝试从 report.json 恢复对齐缓存 ──
        self.src_lines = load_text_lines(src_path)
        self.tgt_lines = load_text_lines(tgt_path)
        _result = None
        _report_path = self._session_path()
        if os.path.isfile(_report_path):
            try:
                with open(_report_path, encoding="utf-8") as _f:
                    _r = json.load(_f)

                if _r.get("ops") and _r.get("src_hash"):
                    if _r["src_hash"] == content_hash(self.src_lines) and _r[
                        "tgt_hash"
                    ] == content_hash(self.tgt_lines):
                        _ops_raw = _r["ops"]
                        _stats = _r.get("stats", {})
                        _all_ops = [
                            (
                                tuple(o["s"]),
                                tuple(o["t"]),
                                float(o["sc"]),
                            )
                            for o in _ops_raw
                        ]
                        _result = AlignmentResult(
                            all_ops=_all_ops,
                            anchors=[],
                            anchor_op_indices={},
                            stats=_stats,
                        )
            except Exception:
                import traceback as _tb

                _tb.print_exc()
                _result = None

        if _result is not None:
            result = _result
            # 直接进入对齐完成阶段，跳过编码
            self._ensure_table_in_stacked()
            self._show_table()
            self._on_align_done(result)
            self._update_feature_gating()
            return

        # ── 缓存未命中 → 编码 + 对齐 ──
        from dualign.gui.workers import EncodeThread

        # 欢迎页显示对齐进度
        if hasattr(self, "_welcome") and self._welcome is not None:
            self._welcome.set_aligning("正在编码…")

        self._status("编码中...")
        QApplication.processEvents()

        self._enc_thread = EncodeThread(src_path, tgt_path, entry_id=_entry_id)
        self._enc_thread.status_signal.connect(lambda msg: self._status(msg))
        self._enc_thread.text_ready_signal.connect(self._on_text_ready)
        self._enc_thread.finished_signal.connect(self._on_encoded)
        self._enc_thread.error_signal.connect(self._on_worker_error)
        self._enc_thread.start()

    def load_from_provider(self, entries: List[Any]):
        """从 FileListProvider 加载章节列表。"""
        self._entries = entries
        # 构建文件队列
        items = []
        for e in entries:
            label = getattr(e, "label", str(e))
            src = getattr(e, "source_path", "")
            tgt = getattr(e, "target_path", "")
            items.append(
                FileQueueItem(label=label, src_path=src, tgt_path=tgt, entry=e)
            )
        self._workspace.set_queue(items)
        if entries:
            self._on_entry_selected(entries[0])

    def _on_entry_selected(self, entry: Any):
        """章节选中（项目模式）。加载文件对并对齐。"""
        self._current_entry = entry
        src_path = getattr(entry, "source_path", "")
        tgt_path = getattr(entry, "target_path", "")
        label = getattr(entry, "label", "")
        # 从 entry 获取 repaired_dir（项目模式优先使用配置的目录）
        entry_repaired = getattr(entry, "repaired_dir", "")
        if entry_repaired:
            self._repaired_dir = entry_repaired
        if src_path and tgt_path:
            self._workspace.set_file_paths(src_path, tgt_path, label)
            self.load_file_pair(src_path, tgt_path, label)
        # 无需手动激活面板，原生 QTabBar 已处理

    def _cancel_current_load(self):
        """取消当前正在进行的加载操作。

        停止所有后台线程，递增操作 ID，使后续到达的旧回调被 _on_encoded / _on_align_done 忽略。
        编码线程通过 stop_event 通知 OllamaEncoder 在批次间中断；
        wait(15000) 给足 15 秒让当前 HTTP 批次完成，避免 QThread 销毁时报错闪退。
        """
        self._load_op_id += 1
        self._current_load_op_id = self._load_op_id

        if self._enc_thread is not None and self._enc_thread.isRunning():
            self._enc_thread.stop()
            # ── 尝试关闭底层 HTTP session 以加速中断阻塞的 POST 请求 ──
            try:
                from dualign.services.embedding import _MODEL_CACHE

                for _model in _MODEL_CACHE.values():
                    if hasattr(_model, "_session") and _model._session is not None:
                        try:
                            _model._session.close()
                            _model._session = None
                        except Exception:
                            pass
            except Exception:
                import traceback as _tb

                _tb.print_exc()
            self._enc_thread.wait(15000)
            self._enc_thread = None

        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(15000)
            self._worker = None

    def _on_text_ready(self, _src_hash, _tgt_hash, src_lines, tgt_lines):
        """文本就绪回调 — 立即进入预览模式展示原文/译文行。

        EncodeThread 读取文件后第一时间发射，此时编码尚未开始。
        用户可阅读文本，评分列暂显示灰色 "…"。
        """
        op_id = getattr(self, "_current_load_op_id", 0)
        if op_id != self._load_op_id:
            return
        self.src_lines, self.tgt_lines = src_lines, tgt_lines
        self._preview_scores = None
        self._ensure_table_in_stacked()
        self._show_table()
        self._switch_table_mode(True)
        self._preview_active = True
        self._status_bar.set_preview_active(True, phase="正在编码…")
        self._render_preview()

    def _on_encoded(self, se, te, sl, tl, sh, th):
        """EncodeThread 完成后回调（接收 6 个参数）。存到实例变量，启动对齐。

        如果 _load_op_id 已变更（即新的 load_file_pair 已启动），则丢弃此结果。
        """
        # ── 操作 ID 校验：丢弃前一次取消操作残留的延迟回调 ──
        op_id = getattr(self, "_current_load_op_id", 0)
        if op_id != self._load_op_id:
            return

        try:
            self.src_emb, self.tgt_emb = se, te
            self.src_lines, self.tgt_lines = sl, tl
            self._src_hash, self._tgt_hash = sh, th

            # ── 预览模式: 用本地 dot 刷新评分列（零 API 调用）──
            if self._preview_active:
                import numpy as _np

                n = min(len(sl), len(tl))
                if n > 0:
                    diag = _np.sum(se[:n] * te[:n], axis=1).astype(_np.float64)
                    self._preview_scores = diag
                self._render_preview()
                self._status_bar.set_preview_active(True, phase="正在对齐…")

            self._status("对齐中...")
            QApplication.processEvents()
            self._start_align()
        except Exception as e:
            self._show_error("编码完成回调", e)

    def _align_cache_dir(self) -> str:
        """返回对应当前章节的统一缓存目录。"""
        from dualign.config import get_embedding_cache_dir

        src_path = getattr(self, "_src_path", "")
        entry_id = Path(src_path).stem.split(".")[0] if src_path else "_unknown"
        d = get_embedding_cache_dir(entry_id)
        os.makedirs(d, exist_ok=True)
        return d

    def _session_cache_path(self) -> str:
        """修复会话路径，位于 repaired_dir 下，与编码缓存分离。"""
        from dualign.config import repair_session_path

        # 从 entry 中提取 entry_id，用作文件名前缀
        entry_id = ""
        if self._current_entry:
            entry_id = getattr(self._current_entry, "entry_id", "") or ""
        if not entry_id and hasattr(self, "_src_path") and self._src_path:
            entry_id = Path(self._src_path).stem.split(".")[0]
        return repair_session_path(entry_id, self._repaired_dir)

    def _start_align(self):
        """构造 AlignWorker 并启动对齐。先尝试对齐结果缓存复用。"""
        from dualign.gui.workers import AlignWorker
        from dualign.services.embedding import _try_lazy_load_model

        if self.src_emb is None or self.tgt_emb is None:
            return

        # ── 停止前一次未完成的对齐线程 ──
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
            self._worker = None

        # ── 对齐缓存复用 ──
        if hasattr(self, "_src_path") and hasattr(self, "_tgt_path"):
            _rp = self._session_path()
            if os.path.isfile(_rp):
                try:
                    with open(_rp, encoding="utf-8") as _f:
                        _r = json.load(_f)
                    _saved_src = _r.get("src_hash", "")
                    _saved_tgt = _r.get("tgt_hash", "")
                    if (
                        _r.get("ops")
                        and _saved_src == content_hash(self.src_lines)
                        and _saved_tgt == content_hash(self.tgt_lines)
                    ):
                        _ops_raw = _r["ops"]
                        _result = AlignmentResult(
                            all_ops=[
                                (
                                    tuple(o["s"]),
                                    tuple(o["t"]),
                                    float(o["sc"]),
                                )
                                for o in _ops_raw
                            ],
                            anchors=[],
                            anchor_op_indices={},
                            stats=_r.get("stats", {}),
                        )
                        self._status("对齐缓存命中", "success")
                        # ── 恢复相似度矩阵（预览模式使用）──
                        try:
                            npy_path = _rp.replace(".report.json", ".sim.npy")
                            if os.path.isfile(npy_path):
                                import numpy as _np

                                self._sim_matrix = _np.load(npy_path)
                        except Exception:
                            import traceback as _tb

                            _tb.print_exc()
                            self._sim_matrix = None
                        self._ensure_table_in_stacked()
                        self._on_align_done(_result)
                        return
                except Exception:
                    import traceback as _tb

                    _tb.print_exc()

        # ── 对齐缓存未命中 → 清除旧修复会话（基于旧对齐结果，已失效）──
        # 但注意：report.json 中可能包含外部 AI 校订的 repair_log/ai_proposals/ai_review，
        # 使对齐缓存失效：清空 ops 和 stats，保留 AI 相关字段供新对齐后复用。
        self._invalidate_align_cache()

        model = _try_lazy_load_model()
        self._status("开始计算对齐方案...")
        QApplication.processEvents()

        self._worker = AlignWorker(
            self._align_config,
            self.src_emb,
            self.tgt_emb,
            self.src_lines,
            self.tgt_lines,
            encode_fn=model.encode if model else None,
            src_path=getattr(self, "_src_path", ""),
            tgt_path=getattr(self, "_tgt_path", ""),
            entry_id=getattr(self, "_current_entry_id", ""),
        )
        self._worker.finished_signal.connect(self._on_align_done)
        self._worker.error_signal.connect(self._on_worker_error)
        self._worker.start()

    def _on_align_done(self, result: AlignmentResult):
        """对齐完成后初始化修复状态。尝试加载已有的修复会话。

        如果 _load_op_id 已变更（即新的 load_file_pair 已启动），则丢弃此结果。
        全方法 try/except 保护，防止未捕获异常导致窗口闪退。
        """
        try:
            # ── 操作 ID 校验：丢弃前一次取消操作残留的延迟回调 ──
            op_id = getattr(self, "_current_load_op_id", 0)
            if op_id != self._load_op_id:
                return

            self._alignment_snapshot = AlignmentSnapshot.from_alignment(
                result.all_ops, self.src_lines, self.tgt_lines
            )
            self._align_stats = result.stats
            # 保持 _sim_matrix（.npy 恢复优先），仅新鲜对齐时覆盖
            if getattr(result, "sim_matrix", None) is not None:
                self._sim_matrix = result.sim_matrix

            # ── 尝试加载已有的修复会话 ──
            loaded = self._load_session()
            if loaded is not None:
                self._repair_state = loaded
                self._status("已恢复上次修复会话", "success")
            else:
                self._repair_state = RepairState.from_ops(
                    result.all_ops, self.src_lines, self.tgt_lines
                )

            self._undo_stack.clear()
            self._redo_stack.clear()
            self._strategy = "src"

            # ── 质量评估（必须先于持久化，定义 quality/rejections/indicators）──
            stats = result.stats
            n_src = stats.get("n_source", 0) or len(self.src_lines or [])
            n_tgt = stats.get("n_target", 0) or len(self.tgt_lines or [])

            from dualign.services.quality_gate import (
                QUALITY_OK,
                QUALITY_UNRELIABLE,
                assess_alignment_quality,
                _gap_row_ratio,
            )

            gap_ratio = _gap_row_ratio(result.all_ops, n_src, n_tgt)
            n_overflow = stats.get("n_overflow_rows", 0)

            assessment = assess_alignment_quality(
                stats,
                n_src,
                n_tgt,
                gap_row_ratio=gap_ratio,
                n_overflow_rows=n_overflow,
                config=getattr(self, "_quality_config", None),
            )
            quality = assessment["quality"]
            rejections = assessment.get("rejections", [])
            indicators = assessment["indicators"]

            if quality == QUALITY_UNRELIABLE:
                self._status(
                    f"⚠ 真锚点覆盖不足 (密度={indicators['anchor_density']:.0%})",
                    "warning",
                )
            elif "gap_dominated" in rejections:
                self._status(
                    f"⚠ 间隙行占比 {indicators['gap_row_ratio']:.0%}",
                    "warning",
                )
            elif quality == QUALITY_OK:
                self._status("对齐完成", "success")

            self._last_quality_assessment = assessment

            # ── 保存对齐结果缓存（单个保存块，写入 ops/stats/quality/hash）──
            if hasattr(self, "_src_path") and self._src_path:
                _report_path = self._session_path()
                os.makedirs(os.path.dirname(_report_path), exist_ok=True)
                _report = {}
                if os.path.isfile(_report_path):
                    try:
                        with open(_report_path, encoding="utf-8") as _f:
                            _report = json.load(_f)
                    except Exception:
                        import traceback as _tb

                        _tb.print_exc()
                        _report = {}

                _new_src_hash = content_hash(list(self.src_lines))
                _new_tgt_hash = content_hash(list(self.tgt_lines))
                _old_src_hash = _report.get("src_hash", "")
                _old_tgt_hash = _report.get("tgt_hash", "")
                if _old_src_hash != _new_src_hash or _old_tgt_hash != _new_tgt_hash:
                    _report.pop("repair_log", None)
                    _report.pop("ai_review", None)

                _report["ops"] = [
                    {"s": list(s), "t": list(t), "sc": round(float(sc), 4)}
                    for s, t, sc in result.all_ops
                ]
                _report["stats"] = stats
                _report["src_hash"] = _new_src_hash
                _report["tgt_hash"] = _new_tgt_hash
                _report["quality"] = {
                    "level": quality,
                    "rejections": rejections,
                    "indicators": indicators,
                }
                with open(_report_path, "w", encoding="utf-8") as _f:
                    json.dump(_report, _f, ensure_ascii=False, separators=(",", ":"))

            # ── 将初始分数载入 ScoreManager 缓存 ──
            if hasattr(self, "_score_mgr"):
                self._score_mgr.invalidate()
                self._load_initial_scores()
                # 对齐完成后注入 scorer（已由 load_file_pair 创建）
                if hasattr(self, "_scorer") and self._scorer is not None:
                    self._score_mgr.set_scorer(self._scorer)

            # ── 为新对齐创建全新的 score_cache（加载 session 后清除旧值）──
            # 放在 _load_initial_scores 之后，因为后者已从 _score_cache 读取完毕
            if hasattr(self, "_score_cache"):
                self._score_cache.clear()

            # ── 退出预览模式，恢复到标准 7 列表格 ──
            if self._preview_active:
                self._status_bar.set_preview_active(False)
                self._preview_active = False
                self._preview_scores = None
                self._switch_table_mode(False)
                # 恢复底部 AI 面板（预览模式入口折叠的）
                saved = getattr(self, "_preview_saved_bottom", None)
                if saved and self._bottom_collapsed:
                    self._toggle_bottom_panel()
                self._preview_saved_bottom = None
                # 同步视图模式开关
                self._status_bar.set_view_mode(False)

            self._ensure_table_in_stacked()
            self._show_table()
            self._refresh()
            self._update_feature_gating()
            # 加载会话后重建 AI 建议表格
            if hasattr(self, "_review"):
                self._review._rebuild_ai_suggestions()
            # 同步底部面板展开/折叠状态
            self._sync_bottom_panel()
            # ── 确保导出文件反映最新修复状态 ──
            self._export_repaired_files()
        except Exception as e:
            import traceback as _tb

            _tb.print_exc()
            self._show_error("对齐完成", e)
            self._safe_status("✗ 对齐完成时出错")

    def _on_realign(self):
        """重新对齐 — 清除缓存后重新编码 + 对齐（异步，不阻塞 GUI）。"""
        if not self.src_lines or not self.tgt_lines:
            return
        # ── 使用 _invalidate_align_cache 替代 _clear_session，保留外部 AI 校订数据 ──
        self._invalidate_align_cache()

        from dualign.gui.workers import EncodeThread

        self._status("重新编码中…")
        QApplication.processEvents()

        src_path = getattr(self, "_src_path", "")
        tgt_path = getattr(self, "_tgt_path", "")
        if src_path and tgt_path:
            self._enc_thread = EncodeThread(
                src_path, tgt_path, entry_id=self._current_entry_id
            )
            self._enc_thread.status_signal.connect(self._status)
            self._enc_thread.finished_signal.connect(self._on_encoded)
            self._enc_thread.error_signal.connect(self._on_worker_error)
            self._enc_thread.start()
        else:
            self._status("错误: 无法找到源文件路径", "error")

    def _apply_action(self, action: RepairAction, auto: bool = False):
        """唯一入口：应用修复操作 + 刷新 UI。

        auto=False: 用户手动操作 → approval=manual_reviewed（持久化）
        auto=True:  一键修复/自动处理 → approval=auto_repaired（持久化）

        统一校验：通过 RepairService.valid_operations 检查操作在当前状态下是否合法。
        不合法时跳过执行并在状态栏提示（不会崩溃或产生不一致状态）。
        """
        if self._repair_state is None:
            return

        # ── 统一合法性校验 ──
        from dualign.services.repair import RepairService

        ops = RepairService.valid_operations(self._repair_state, action.op_index)
        kind = action.kind
        if kind == "merge":
            if not ops.get("merge", False):
                self._status(f"⚠ 跳过: snap[{action.op_index}] 当前不可合并", "warning")
                return
        elif kind in ("split",):
            if not ops.get("split_tgt", False) and not ops.get("split_src", False):
                self._status(f"⚠ 跳过: snap[{action.op_index}] 当前不可拆分", "warning")
                return
        elif kind in ("edit", "edit_tgt", "edit_src"):
            if not ops.get("edit", False):
                self._status(f"⚠ 跳过: snap[{action.op_index}] 当前不可校订", "warning")
                return
        elif kind == "delete":
            if not ops.get("delete", False):
                self._status(f"⚠ 跳过: snap[{action.op_index}] 当前不可删除", "warning")
                return
        elif kind in ("placeholder_src", "placeholder_tgt"):
            if not ops.get("placeholder", False):
                self._status(
                    f"⚠ 跳过: snap[{action.op_index}] 当前不可插占位符", "warning"
                )
                return
        elif kind in ("ok", "flag"):
            if not ops.get(kind, False):
                self._status(
                    f"⚠ 跳过: snap[{action.op_index}] 操作 {kind} 不可用", "warning"
                )
                return

        action.data["approvals"] = {"auto"} if auto else {"manual"}
        self._undo_stack.append(self._repair_state)
        self._redo_stack.clear()
        self._repair_state = self._repair_state.apply(action)
        # 标记受影响 snap 失效，等待 poll_now 捡起重算
        _affected = action.data.get("orig_snaps", [action.op_index])
        if hasattr(self, "_score_mgr"):
            self._score_mgr.invalidate_snaps(_affected)
        # 文本变更类操作 → 重置该 snap 已采纳的 AI 建议为 pending
        _text_changing_kinds = {
            "edit",
            "edit_tgt",
            "edit_src",
            "merge",
            "split",
            "delete",
            "placeholder_src",
            "placeholder_tgt",
        }
        if action.kind in _text_changing_kinds:
            _store = self._repair_state.ai_proposal_store
            for _si in _affected:
                _store.reset(_si)
            if hasattr(self, "_review"):
                self._review._rebuild_ai_suggestions()

        self._save_session()
        self._refresh()

        # ── 用临时状态反馈操作结果 ──
        _action_labels = {
            "merge": "已合并",
            "split": "已拆分",
            "edit": "已校订",
            "delete": "已删除",
            "flag": "已标记异常",
            "ok": "已审核通过",
            "placeholder_src": "已插占位符",
            "placeholder_tgt": "已插占位符",
        }
        lbl = _action_labels.get(kind, f"已{kind}")
        self._set_temp_status(f"{lbl} snap[{action.op_index}]", "success")

        # 撤销栈溢出提醒
        if len(self._undo_stack) == self._undo_stack.maxlen:
            self._status("撤销栈已达上限 (50)，将覆盖最旧记录", "warning")

    def do_merge(self, snap_i: int):
        """合并当前文本对。"""
        if self._repair_state is None:
            return
        s_idx, t_idx, _sc = self._repair_state.snapshot.original_ops[snap_i]
        self._apply_action(
            RepairAction.make_merge(snap_i, sub_count=max(len(s_idx), len(t_idx)))
        )

    def do_split(self, snap_i: int):
        """拆分文本对 — 自动拆分少行的一侧（按硬分割）。

        注意：拆分涉及模型编码，可能耗时。通过状态栏提示用户。
        """
        if self._repair_state is None:
            return
        if not self._ensure_model():
            self._status("拆分需要编码模型，请先完成一次对齐", "warning")
            return
        snap = self._repair_state.snapshot
        s_idx, t_idx, _sc = snap.original_ops[snap_i]
        ls, lt = len(s_idx), len(t_idx)

        side = "src" if ls <= lt else "tgt"
        self._status(f"拆分 snap[{snap_i}] {side} 侧…")
        QApplication.processEvents()

        # 创建嵌入缓存，使 split 产生的新文本被缓存
        from dualign.config import get_embedding_cache_dir
        from dualign.services.embedding_cache import EmbeddingCache

        ec = EmbeddingCache(
            os.path.join(get_embedding_cache_dir(self._current_entry_id), "vecs.db")
        )

        state = RepairService.apply_split(
            self._repair_state, snap_i, side, self._model, cache=ec
        )
        if state is self._repair_state:
            self._status("拆分失败：文本无法进一步拆分或重对齐失败", "warning")
            return  # 跳过 refresh，保留提示消息
        self._undo_stack.append(self._repair_state)
        self._repair_state = state
        if hasattr(self, "_score_mgr"):
            self._score_mgr.invalidate_snaps([snap_i])
        self._reset_accepted_proposals([snap_i])
        self._save_session()
        self._refresh()
        # 滚动到拆分后的文本对
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == snap_i:
                self.table.scrollToItem(
                    item, QAbstractItemView.ScrollHint.PositionAtCenter
                )
                break
        self._set_temp_status(f"已拆分 snap[{snap_i}] ({side}侧)", "success")

    def _ensure_model(self):
        """确保 self._model 已加载。返回 True 表示就绪。"""
        if self._model is not None:
            return True
        from dualign.services.embedding import (
            _try_lazy_load_model,
            load_model_for_provider,
        )

        m = _try_lazy_load_model()
        if m is None:
            try:
                m = load_model_for_provider()
            except Exception as e:
                self._status(f"模型加载失败: {e}", "error")
                return False
        self._model = m
        return True

    def do_edit_single(self, snap_i: int):
        """校订单个文本对。优先使用当前修复状态。"""
        if self._repair_state is None:
            return
        ch = self._repair_state.current
        g = ch.group(snap_i)
        snap = self._repair_state.snapshot

        # 初始文本（原始对齐输出，始终不变）
        s_idx, t_idx, _sc = snap.original_ops[snap_i]
        initial_src = [snap.src_text(i) for i in s_idx]
        initial_tgt = [snap.tgt_text(j) for j in t_idx]

        if g is not None and g.rows:
            from dualign.models.marker import is_merge

            if is_merge(g.rows[0].marker):
                # [M]: 当前文本在表格中显示为聚合行，对话框也应一致
                src_lines = [_join([r.src_text for r in g.rows if r.src_text])]
                tgt_lines = [_join([r.tgt_text for r in g.rows if r.tgt_text])]
            else:
                src_lines = [r.src_text for r in g.rows if r.src_text]
                tgt_lines = [r.tgt_text for r in g.rows if r.tgt_text]
        else:
            src_lines = list(initial_src)
            tgt_lines = list(initial_tgt)

        dlg = BlockEditDialog(
            src_lines,
            tgt_lines,
            self,
            initial_src_lines=initial_src,
            initial_tgt_lines=initial_tgt,
        )
        if dlg.exec() == BlockEditDialog.DialogCode.Accepted:
            new_src = dlg.result_src_lines
            new_tgt = dlg.result_tgt_lines
            # 不传 inherited_scores → _apply_info_full 用 osc 原始分 fallback
            action = RepairAction.make_edit(
                snap_i,
                new_src_lines=new_src,
                new_tgt_lines=new_tgt,
            )
            self._apply_action(action)

    def do_ok(self, snap_i: int):
        """审核通过 — 认可当前 1:1 状态，不做任何文本修改。"""
        self._apply_action(RepairAction.make_ok(snap_i))

    def do_flag(self, snap_i: int):
        """标记异常。"""
        self._apply_action(RepairAction.make_flag(snap_i))

    def do_delete(self, snap_i: int):
        """删除文本对。"""
        self._apply_action(RepairAction.make_delete(snap_i))

    def _delete_selected_snaps(self, snaps: List[int]):
        """批量删除选中 snap。逐个 apply delete action。"""
        if self._repair_state is None or len(snaps) < 1:
            return
        self._undo_stack.append(self._repair_state)
        self._redo_stack.clear()
        state = self._repair_state
        for si in sorted(snaps, reverse=True):
            state = state.apply(RepairAction.make_delete(si))
        self._repair_state = state
        if hasattr(self, "_score_mgr"):
            self._score_mgr.invalidate_snaps(snaps)
        self._reset_accepted_proposals(snaps)
        self._save_session()
        self._refresh()
        self._set_temp_status(f"已删除 {len(snaps)} 个文本对", "success")

    def do_placeholder(self, snap_i: int):
        """占位符 — 自动判断方向（1:0 → tgt, 0:1 → src）。"""
        if self._repair_state is None:
            return
        s_idx, t_idx, _sc = self._repair_state.snapshot.original_ops[snap_i]
        ls, lt = len(s_idx), len(t_idx)
        if ls > 0 and lt == 0:
            self._apply_action(RepairAction.make_placeholder_tgt(snap_i))
        elif ls == 0 and lt > 0:
            self._apply_action(RepairAction.make_placeholder_src(snap_i))

    def do_auto_repair_single(self, snap_i: int):
        """自动修复当前文本对。"""
        if self._repair_state is None:
            return
        s_idx, t_idx, _sc = self._repair_state.snapshot.original_ops[snap_i]
        ls, lt = len(s_idx), len(t_idx)
        if ls > 1 and lt == 1:
            self.do_split(snap_i)  # N:1 → 拆 tgt
        elif ls == 1 and lt > 1:
            self.do_merge(snap_i)  # 1:M → 合 tgt
        elif ls > 0 and lt == 0:
            self.do_placeholder(snap_i)
        elif ls == 0 and lt > 0:
            self.do_placeholder(snap_i)

    def do_edit_selected(self, snaps: List[int]):
        """跨 snap 手动校订。所有选中文本对合并编辑。"""
        if self._repair_state is None or len(snaps) < 1:
            return
        ch = self._repair_state.current
        snap = self._repair_state.snapshot
        from dualign.models.marker import is_merge

        # 收集所有原文/译文行（优先从当前状态读取）
        all_src: List[str] = []
        all_tgt: List[str] = []
        # 初始文本（原始对齐输出，始终不变）
        init_src: List[str] = []
        init_tgt: List[str] = []
        for si in sorted(snaps):
            g = ch.group(si)
            s_idx, t_idx, _sc = snap.original_ops[si]

            if g is not None and g.rows:
                if is_merge(g.rows[0].marker):
                    all_src.append(_join([r.src_text for r in g.rows if r.src_text]))
                    all_tgt.append(_join([r.tgt_text for r in g.rows if r.tgt_text]))
                else:
                    for r in g.rows:
                        if r.src_text:
                            all_src.append(r.src_text)
                        if r.tgt_text:
                            all_tgt.append(r.tgt_text)
            else:
                for i in s_idx:
                    t = snap.src_text(i)
                    if t:
                        all_src.append(t)
                for j in t_idx:
                    t = snap.tgt_text(j)
                    if t:
                        all_tgt.append(t)

            # 收集该 snap 的初始文本（始终从 snapshot 原始数据）
            for i in s_idx:
                t = snap.src_text(i)
                if t:
                    init_src.append(t)
            for j in t_idx:
                t = snap.tgt_text(j)
                if t:
                    init_tgt.append(t)

        dlg = BlockEditDialog(
            all_src,
            all_tgt,
            self,
            initial_src_lines=init_src,
            initial_tgt_lines=init_tgt,
        )
        if dlg.exec() == BlockEditDialog.DialogCode.Accepted:
            new_src = dlg.result_src_lines
            new_tgt = dlg.result_tgt_lines
            if len(snaps) == 1:
                # 不传入 inherited_scores → _apply_info_full 用 osc 原始分 fallback
                # 轮询自动触发异步评分
                action = RepairAction.make_edit(
                    snaps[0],
                    new_src_lines=new_src,
                    new_tgt_lines=new_tgt,
                )
                self._apply_action(action)
            else:
                # 多 snap 校订：不传 scores，轮询自动评分
                self._undo_stack.append(self._repair_state)
                self._redo_stack.clear()
                self._repair_state = RepairService.repair_multi_edit(
                    self._repair_state,
                    snaps,
                    new_src,
                    new_tgt,
                )
                if hasattr(self, "_score_mgr"):
                    self._score_mgr.invalidate_snaps(snaps)
                self._reset_accepted_proposals(snaps)
                self._save_session()
                self._refresh()

    def _reset_accepted_proposals(self, snap_indices: list[int]):
        """重置指定 snap 中已采纳的 AI 建议为 pending。"""
        if self._repair_state is None or not snap_indices:
            return
        store = self._repair_state.ai_proposal_store
        changed = False
        for si in snap_indices:
            for p in store.get(si):
                if p.status == "accepted":
                    p.reset()
                    changed = True
        if changed and hasattr(self, "_review"):
            self._review._rebuild_ai_suggestions()

    def do_bundle_snaps(self, snaps: List[int]):
        """跨 snap 合并：将多个 snap 捆绑为一个文本对。原文和译文均合并。"""
        if self._repair_state is None or len(snaps) < 2:
            return
        self._undo_stack.append(self._repair_state)
        self._redo_stack.clear()
        self._repair_state = RepairService.repair_bundle_snaps(
            self._repair_state, sorted(snaps)
        )
        if hasattr(self, "_score_mgr"):
            self._score_mgr.invalidate_snaps([snaps[0]])
        self._reset_accepted_proposals([snaps[0]])
        self._save_session()
        self._refresh()
        self._set_temp_status(
            f"已合并 {len(snaps)} 个文本对 → snap[{snaps[0]}]", "success"
        )

    def do_reset(self, snap_i: int):
        """重置当前文本对的修复。"""
        if self._repair_state is None:
            return
        self._undo_stack.append(self._repair_state)
        self._redo_stack.clear()
        self._repair_state = self._repair_state.reset_op(snap_i)
        if hasattr(self, "_score_mgr"):
            self._score_mgr.invalidate_snaps([snap_i])
        self._save_session()
        self._refresh()
        self._set_temp_status(f"已重置 snap[{snap_i}]", "info")

    def _apply_ai_action(self, action: RepairAction):
        """AI 操作的受控入口：用户已确认采纳，执行修复。

        统一方案：所有 AI 操作（含 delete）走统一 _apply_action 路径，
        不再为 delete 单独追加 [OK]——采纳操作本身已构成审批。
        """
        self._apply_action(action, auto=False)
        self._set_temp_status(
            f"AI 修复已应用: snap[{action.op_index}] {action.kind}", "success"
        )

    def _on_ai_repair_chapter(self):
        """菜单项：AI 一键校订当前章节。"""
        try:
            if hasattr(self, "_review"):
                if not getattr(self, "_batch_connected", False):
                    self._review.batch_finished.connect(self._on_ai_batch_finished)
                    self._review.ai_error.connect(self._on_ai_review_status)
                    self._batch_connected = True
            self._review.analyze_chapter_batch()
            self._status("AI 校订中...", "info")
        except Exception as e:
            self._show_error("AI 校订本章", e)

    def _on_ai_batch_finished(self):
        """AI 校订完成 → 持久化 + 刷新 GUI。"""
        self._set_ai_review("completed", "")
        self._save_session()
        self._status("AI 校订完成", "success")
        # 刷新主表格以反映修复后的最新状态
        self._refresh()
        self._sync_bottom_panel()

    def _on_ai_review_status(self, status_or_error: str):
        """AI 校订异常/跳过 → 写入 ai_review 状态。"""
        if status_or_error == "skipped":
            self._set_ai_review("skipped", "无待审核异常")
        else:
            self._set_ai_review("error", status_or_error)

    def _set_ai_review(self, status: str, note: str = ""):
        """写入 AI 审校状态到 report.json 的 ai_review 字段。"""
        import time as _time

        path = self._session_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                report = json.load(f)
        except Exception:
            import traceback as _tb

            _tb.print_exc()
            return
        report["ai_review"] = {
            "status": status,
            "note": note,
            "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            import traceback as _tb

            _tb.print_exc()

    def _on_batch_discover(self):
        """批量文件对发现 — 对话框 → FilePairMatcher → 导入队列。"""
        from dualign.gui.batch_discovery import BatchDiscoveryDialog

        dlg = BatchDiscoveryDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dlg.selected_pairs()
        if not selected:
            return

        added = 0
        for pair in selected:
            self._workspace.add_to_queue(
                FileQueueItem(
                    label=pair.label or pair.entry_id,
                    src_path=pair.src_path,
                    tgt_path=pair.tgt_path,
                )
            )
            added += 1

        self._safe_status(f"已导入 {added} 个文件对")
        # 自动加载第一个
        if selected and self._workspace._queue:
            first = self._workspace._queue[0]
            self.load_file_pair(first.src_path, first.tgt_path, first.label)

    def _on_welcome_recent(self, label: str, src_path: str, tgt_path: str):
        """欢迎页最近文件对点击 → 加载。"""
        if not src_path or not tgt_path:
            return
        self._workspace.add_to_queue(
            FileQueueItem(label=label, src_path=src_path, tgt_path=tgt_path)
        )
        self.load_file_pair(src_path, tgt_path, label)

    def _on_open_agent_config(self):
        """打开 Agent 配置对话框。"""
        from dualign.gui.dialogs import AgentConfigDialog

        dlg = AgentConfigDialog(self)
        dlg.config_changed.connect(self._on_agent_config_changed)
        dlg.exec()

    def _on_agent_config_changed(self):
        """Agent 配置变更后刷新 AI 面板和状态指示灯。"""
        from dualign.providers import active_repair_agent

        agent = active_repair_agent()
        if agent and agent.agent_id == "ollama_local":
            self._review.set_backend("ollama")
        else:
            self._review.set_backend("deepseek")
        # 刷新状态和功能阶梯
        self._refresh_status_dots()
        self._safe_status("Agent 配置已更新")
        self._refresh_status_dots()

    def _on_reset_all(self):
        """重置所有修复。"""
        if self._repair_state is None:
            return
        self._safe_status("重置修复中…")
        QApplication.processEvents()
        self._undo_stack.append(self._repair_state)
        self._repair_state = self._repair_state.reset()
        if hasattr(self, "_score_mgr"):
            _all_snaps = [g.snap_i for g in self._repair_state.current.groups]
            self._score_mgr.invalidate_snaps(_all_snaps)
        self._reset_accepted_proposals(_all_snaps)
        self._refresh()
        self._save_session()
        self._safe_status("已重置所有修复")

    def _on_strategy_changed(self, idx: int):
        strategies = ["minimal", "src", "tgt"]
        self._strategy = strategies[idx] if 0 <= idx < 3 else "src"

    def _on_auto_repair(self):
        """一键修复 — 通过后台线程执行，避免阻塞主线程。"""
        if self._repair_state is None:
            return
        if self._strategy == "src" and not self._ensure_model():
            self._safe_status("该策略需要编码模型，请先完成一次对齐")
            return

        # ── 锚点门控：复用 _on_align_done 已计算的品质信息 ──
        stats = getattr(self, "_align_stats", None) or {}
        n_containers = stats.get("n_containers", 0)
        qa = getattr(self, "_last_quality_assessment", None)
        if qa:
            is_unreliable = qa["quality"] == "unreliable"
        else:
            n_src = stats.get("n_source", 0) or len(self.src_lines or [])
            n_tgt = stats.get("n_target", 0) or len(self.tgt_lines or [])
            from dualign.services.quality_gate import (
                assess_alignment_quality,
                _gap_row_ratio,
            )

            # 从 self._repair_state 获取 all_ops 用于计算 gap_ratio
            if self._repair_state:
                _ops = [
                    (
                        (tuple(o["s"]), tuple(o["t"]), float(o["sc"]))
                        if isinstance(o, dict)
                        else (o[0], o[1], o[2])
                    )
                    for o in self._repair_state.snapshot.original_ops
                ]
                gap_ratio = _gap_row_ratio(_ops, n_src, n_tgt)
            else:
                gap_ratio = 0.0
            fallback = assess_alignment_quality(
                stats, n_src, n_tgt, gap_row_ratio=gap_ratio
            )
            is_unreliable = fallback["quality"] == "unreliable"

        if is_unreliable:
            ad = qa["indicators"]["anchor_density"] if qa else 0
            self._safe_status(f"✗ 已拒绝修复 — 真锚点密度 {ad:.0%}")
            return

        # 容器操作提示
        if n_containers > 0:
            self._safe_status(f"一键修复中…（含 {n_containers} 个容器操作）")
        else:
            self._safe_status("一键修复中…")
        QApplication.processEvents()

        from dualign.gui.workers import AutoRepairWorker

        # 预先保存当前状态用于撤销
        self._undo_stack.append(self._repair_state)

        # 创建嵌入缓存，使自动修复中的 split 产生的新文本被缓存
        from dualign.config import get_embedding_cache_dir
        from dualign.services.embedding_cache import EmbeddingCache

        ec = EmbeddingCache(
            os.path.join(get_embedding_cache_dir(self._current_entry_id), "vecs.db")
        )

        self._auto_repair_worker = AutoRepairWorker(
            self._repair_state, self._strategy, model=self._model, cache=ec
        )
        self._auto_repair_worker.status_signal.connect(lambda msg: self._status(msg))
        self._auto_repair_worker.finished_signal.connect(self._on_auto_repair_done)
        self._auto_repair_worker.error_signal.connect(self._on_worker_error)
        self._auto_repair_worker.start()

    def _on_auto_repair_done(self, result):
        """一键修复完成 → 更新状态并刷新 UI。"""
        self._repair_state = result
        if hasattr(self, "_score_mgr"):
            _all_snaps = [g.snap_i for g in self._repair_state.current.groups]
            self._score_mgr.invalidate_snaps(_all_snaps)
        self._save_session()
        self._refresh()
        n_actions = len(result._repair_log) if result._repair_log else 0
        self._status(f"一键修复完成 ({n_actions} 个操作)", "success")

    def _on_export(self):
        """导出修复结果。"""
        if self._repair_state is None:
            return
        from dualign.common import format_markdown_output

        src_out, tgt_out = RepairService.render_rows(self._repair_state)

        # 保存 src
        src_path, _ = QFileDialog.getSaveFileName(
            self, "保存原文", "repaired_source.md", "Markdown (*.md)"
        )
        if src_path:
            with open(src_path, "w", encoding="utf-8") as f:
                f.write(format_markdown_output(src_out))

        # 保存 tgt
        tgt_path, _ = QFileDialog.getSaveFileName(
            self, "保存译文", "repaired_target.md", "Markdown (*.md)"
        )
        if tgt_path:
            with open(tgt_path, "w", encoding="utf-8") as f:
                f.write(format_markdown_output(tgt_out))

        self._safe_status(f"已导出: {len(src_out)} 行原文 / {len(tgt_out)} 行译文")

    def _on_promote(self):
        """固化修复：用 repaired 文件覆盖原始文件。

        将已确认的修复结果写回原始文档对，并清除过期缓存。
        编码嵌入缓存通过 content_hash 自验证，保留不动。
        会话缓存和 report.json 中的旧对齐元数据会被清除。
        """
        if not self._src_path or not self._tgt_path or not self._repaired_dir:
            QMessageBox.information(self, "固化修复", "请先加载文件对后再操作。")
            return
        if not self._current_entry_id:
            QMessageBox.information(
                self, "固化修复", "无法确定章节标识，请重新加载文件。"
            )
            return

        entry_id = self._current_entry_id
        src_path = self._src_path
        tgt_path = self._tgt_path
        repaired_dir = self._repaired_dir

        # ── 用当前 strategy 做筛选 ──
        from dualign.gui.settings import KEY_STRATEGY

        strategy = (
            self._config.get(KEY_STRATEGY, "src") if hasattr(self, "_config") else "src"
        )
        strategy_label = {"src": "仅原文未变", "tgt": "仅译文未变"}.get(
            strategy, "无条件"
        )
        strategy_desc = {
            "src": "仅当原文（.source.md）在校订中未发生变化时允许固化",
            "tgt": "仅当译文（.target.md）在校订中未发生变化时允许固化",
        }.get(strategy, "无条件固化（不做内容校验）")

        # dry-run 预览
        from dualign.common import promote_repaired

        preview = promote_repaired(
            entry_id,
            src_path,
            tgt_path,
            repaired_dir,
            dry_run=True,
            strategy=strategy,
        )
        if not preview["success"]:
            if "策略拒绝" in preview.get("message", ""):
                QMessageBox.information(
                    self,
                    "固化策略拒绝",
                    f"固化策略 ({strategy_label}): {preview['message']}\n\n"
                    f"{strategy_desc}\n\n"
                    f"如需无条件固化，请先切换 strategy 或使用 CLI 的 `--strategy=` 参数。",
                )
            else:
                QMessageBox.warning(
                    self, "固化修复", f"操作不可行: {preview['message']}"
                )
            return

        msg_lines = [
            f"固化策略: {strategy_label}",
            strategy_desc,
            "",
            "将用修复后的文件覆盖原始文档对：",
            f"  原始: {src_path}",
            f"  原始: {tgt_path}",
            "",
            "原始文件将备份为 .bak。",
        ]
        cache_items = preview.get("cache_paths_cleared", [])
        if cache_items:
            msg_lines.append("以下缓存将被清除：")
            for cp in cache_items:
                msg_lines.append(f"  • {cp}")
        msg_lines.append("")
        msg_lines.append("编码缓存保持不动（自验证命中，自动失效）。")
        msg_lines.append("report.json 中的旧对齐元数据和 AI 审校记录将被清除。")
        msg_lines.append("")
        msg_lines.append("此操作不可逆（除非手动恢复 .bak 文件）。")
        msg_lines.append("确认固化？")

        reply = QMessageBox.question(
            self,
            "固化修复 — 确认",
            "\n".join(msg_lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 先持久化当前内存中的修复状态到 report.json
        self._save_session()

        # 实际执行
        result = promote_repaired(
            entry_id,
            src_path,
            tgt_path,
            repaired_dir,
            strategy=strategy,
        )
        if not result["success"]:
            QMessageBox.critical(self, "固化修复失败", result["message"])
            return

        # 成功提示
        n_cache = len(result.get("cache_paths_cleared", []))
        QMessageBox.information(
            self,
            "固化修复成功",
            f"已替换:\n"
            f"  原文: {result['src_count']} 行\n"
            f"  译文: {result['tgt_count']} 行\n\n"
            f"已清除 {n_cache} 项会话缓存\n"
            f"{'report.json 元数据已清理' if result.get('report_updated') else ''}\n"
            f"编码缓存保留（自动失效）\n"
            f"原始文件已备份为 .bak",
        )
        self._set_temp_status(
            f"固化完成: {result['src_count']} 行原文 / {result['tgt_count']} 行译文",
            "success",
        )

    def _on_undo(self):
        """撤销 — 恢复位置 + 同步 AiProposalStore。

        撤销一个操作时，对应 snap 的 AI 建议应从 accepted 回退到 pending，
        避免建议显示"已采纳"但修复已被回退的不一致状态。
        """
        if self._undo_stack:
            self._undo_snap_save = self._review._cur_snap_i()
            # 找出将被撤销的操作涉及的 snap
            old_state = self._repair_state
            self._redo_stack.append(old_state)
            self._repair_state = self._undo_stack.pop()
            # 同步 AiProposalStore：被撤销的操作回退为 pending
            undone_snaps = self._sync_proposals_on_undo(old_state, self._repair_state)
            # 标记受影响的 snap 失效
            if hasattr(self, "_score_mgr") and undone_snaps:
                self._score_mgr.invalidate_snaps(list(undone_snaps))
            self._refresh()
            # 恢复撤销前的位置
            saved = self._undo_snap_save
            self._undo_snap_save = None
            if saved is not None:
                for i, a in enumerate(self._anomalies):
                    snaps = a.get("snap_indices", [a.get("snap_index")])
                    if saved in snaps:
                        self._review.go(i, scroll_to=True)
                        break
            if undone_snaps:
                self._review._rebuild_ai_suggestions()
            self._set_temp_status(
                f"已撤销 (共 {len(self._repair_state.repair_log)} 个操作)", "info"
            )
            self._save_session()

    def _sync_proposals_on_undo(
        self, old_state: RepairState, new_state: RepairState
    ) -> List[int]:
        """撤销后同步 AiProposalStore：找出被撤销的操作对应的 snap，回退为 pending。

        Returns: 被回退的 snap 列表。
        """
        undone_snaps: Set[int] = set()
        old_log = old_state._repair_log
        new_log = new_state._repair_log
        # 找出 old 中有但 new 中没有的 action
        old_set = {(a.op_index, a.kind, a.timestamp) for a in old_log}
        new_set = {(a.op_index, a.kind, a.timestamp) for a in new_log}
        undone = old_set - new_set
        for op_i, kind, _ts in undone:
            if kind in (
                "edit",
                "edit_tgt",
                "edit_src",
                "merge",
                "merge_src",
                "merge_tgt",
                "split",
                "delete",
                "flag",
                "ok",
                "placeholder_src",
                "placeholder_tgt",
            ):
                store = new_state.ai_proposal_store
                store.reset(op_i)
                undone_snaps.add(op_i)
        return list(undone_snaps)

    def _on_redo(self):
        """恢复 — 重做被撤销的操作。"""
        if self._redo_stack:
            self._undo_stack.append(self._repair_state)
            self._repair_state = self._redo_stack.pop()
            if hasattr(self, "_score_mgr"):
                _all_snaps = [g.snap_i for g in self._repair_state.current.groups]
                self._score_mgr.invalidate_snaps(_all_snaps)
            self._refresh()
            self._set_temp_status(
                f"已恢复 (共 {len(self._repair_state.repair_log)} 个操作)", "info"
            )

    def _on_open_files(self):
        """打开文件对（记忆上次打开的路径）。"""
        cfg = DualignConfig.instance()
        cfg.load()
        last_dir = cfg.get(KEY_LAST_OPEN_DIR, "")

        src_path, _ = QFileDialog.getOpenFileName(
            self, "选择原文", last_dir, "Markdown (*.md);;Text (*.txt);;All (*)"
        )
        if not src_path:
            return
        last_dir = str(Path(src_path).parent)
        tgt_path, _ = QFileDialog.getOpenFileName(
            self, "选择译文", last_dir, "Markdown (*.md);;Text (*.txt);;All (*)"
        )
        if tgt_path:
            self._save_last_open_dir(str(Path(tgt_path).parent))
            self.load_file_pair(src_path, tgt_path)

    def _save_last_open_dir(self, path: str):
        """将路径写入配置以供下次复用。"""
        try:
            cfg = DualignConfig.instance()
            cfg.load()
            cfg.set(KEY_LAST_OPEN_DIR, path)
            cfg.save()
        except Exception:
            import traceback as _tb

            _tb.print_exc()

    def _on_placeholder(self):
        snap_i = self._review._cur_snap_i()
        if snap_i is not None:
            self.do_placeholder(snap_i)

    def _session_path(self) -> str:
        return self._session_cache_path()

    def _invalidate_align_cache(self):
        """使对齐缓存失效：清除 ops/stats/hash，也清除孤立的 repair_log。

        重新对齐后旧修复操作与新对齐结果不兼容，必须一并清除 repair_log。
        ai_proposals（未采纳的建议记录）也清除，避免用户看到过时的 AI 建议。
        ai_review 状态保留（它记录的是"已完成 AI 审校"这一事实）。
        """
        path = self._session_path()
        if os.path.isfile(path):
            try:
                import json as _json

                with open(path, encoding="utf-8") as _f:
                    report = _json.load(_f)
                report.pop("ops", None)
                report.pop("src_hash", None)
                report.pop("tgt_hash", None)
                report.pop("stats", None)
                # 清除与旧对齐绑定的修复记录
                report.pop("repair_log", None)
                report.pop("ai_proposals", None)
                with open(path, "w", encoding="utf-8") as _f:
                    _json.dump(report, _f, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                import traceback as _tb

                _tb.print_exc()
        # 清除关联的 sim.npy
        if os.path.isfile(path):
            npy_path = path.replace(".report.json", ".sim.npy")
            if os.path.isfile(npy_path):
                try:
                    os.remove(npy_path)
                except Exception:
                    import traceback as _tb

                    _tb.print_exc()
        self._sim_matrix = None

    def _save_session(self):
        if self._repair_state is None:
            return
        path = self._session_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # ── 持久化相似度矩阵（预览模式使用）──
        sim = getattr(self, "_sim_matrix", None)
        if sim is not None:
            try:
                npy_path = path.replace(".report.json", ".sim.npy")
                import numpy as _np

                _np.save(npy_path, sim)
            except Exception:
                import traceback as _tb

                _tb.print_exc()

        # ── 读取已有报告，保留 quality/stats/ai_review ──
        report = {}
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    report = json.load(f)
            except Exception:
                import traceback as _tb

                _tb.print_exc()
                report = {}

        # ── 更新核心字段 ──
        report["ops"] = [
            {"s": list(s), "t": list(t), "sc": round(float(sc), 4)}
            for s, t, sc in self._repair_state.snapshot.original_ops
        ]
        report["repair_log"] = [a.to_dict() for a in self._repair_state.repair_log]
        store = self._repair_state.ai_proposal_store
        report["ai_proposals"] = store.to_dict()
        # 持久化评分缓存（_on_score_updated 异步写入的分数）
        report["scores"] = dict(getattr(self, "_score_cache", {}))
        report["src_hash"] = (
            content_hash(list(self.src_lines)) if self.src_lines else ""
        )
        report["tgt_hash"] = (
            content_hash(list(self.tgt_lines)) if self.tgt_lines else ""
        )

        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, separators=(",", ":"))
        # ── 同步重导出 repaired 文件（AI 校订后必须反映最新状态）──
        self._export_repaired_files()

    def _export_repaired_files(self):
        """从当前 RepairState 重新导出 *.source.md 和 *.target.md。"""
        if self._repair_state is None:
            return
        from dualign.services.repair import RepairService

        session = self._session_path()
        if not session:
            return
        base = os.path.splitext(session)[0]
        spath = base + ".source.md"
        tpath = base + ".target.md"
        try:
            RepairService.render_to_files(self._repair_state, spath, tpath)
        except Exception:
            import traceback as _tb

            _tb.print_exc()

    def _load_session(self) -> Optional[RepairState]:
        """从统一报告文件中加载修复会话。

        _session_path() 指向 {repaired_dir}/{entry_id}.report.json，
        只要存在 ops 字段即可加载，repair_log/ai_proposals 为可选字段。
        """
        path = self._session_path()
        if not os.path.isfile(path):
            return None

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            import traceback as _tb

            _tb.print_exc()
            return None

        if "ops" not in data or not data["ops"]:
            # 文件存在但无 ops（可能是重新对齐后缓存已失效）
            # 检查是否有孤立的 repair_log → 记录日志后丢弃（旧修复与新对齐不兼容）
            if data.get("repair_log"):
                print(
                    f"[_load_session] 丢弃孤立 repair_log（无 ops，共 {len(data['repair_log'])} 条）",
                    file=sys.stderr,
                )
            return None

        from dualign.models.state import AlignmentSnapshot
        from dualign.models.action import RepairAction
        from dualign.models.action import AiProposalStore

        ops_raw = data.get("ops", [])
        ops = [
            (
                tuple(o["s"]),
                tuple(o["t"]),
                float(o["sc"]),
            )
            for o in ops_raw
        ]
        snap = AlignmentSnapshot.from_alignment(
            ops,
            list(self.src_lines) if self.src_lines else [],
            list(self.tgt_lines) if self.tgt_lines else [],
        )

        # 快照一致性校验
        if self._alignment_snapshot is not None and len(snap.original_ops) != len(
            self._alignment_snapshot.original_ops
        ):
            return None

        log = [RepairAction.from_dict(a) for a in data.get("repair_log", [])]
        store = AiProposalStore.from_dict(data.get("ai_proposals", {}))

        # 内容哈希校验
        saved_src_hash = data.get("src_hash", "")
        saved_tgt_hash = data.get("tgt_hash", "")
        cur_src_hash = content_hash(list(self.src_lines)) if self.src_lines else ""
        cur_tgt_hash = content_hash(list(self.tgt_lines)) if self.tgt_lines else ""
        if saved_src_hash and saved_src_hash != cur_src_hash:
            return None
        if saved_tgt_hash and saved_tgt_hash != cur_tgt_hash:
            return None

        # ── 恢复相似度矩阵（预览模式使用）──
        try:
            npy_path = path.replace(".report.json", ".sim.npy")
            if os.path.isfile(npy_path):
                import numpy as _np

                self._sim_matrix = _np.load(npy_path)
        except Exception:
            import traceback as _tb

            _tb.print_exc()
            self._sim_matrix = None

        # 恢复持久化评分缓存
        if hasattr(self, "_score_cache"):
            raw = data.get("scores")
            if isinstance(raw, dict):
                self._score_cache.clear()
                for k, v in raw.items():
                    try:
                        self._score_cache[str(k)] = float(v)
                    except (ValueError, TypeError):
                        pass

        return RepairState(snap, log, store)

    def _show_error(self, context: str, error: Exception):
        """统一的异常报告：终端 traceback + 弹窗 + 状态栏。

        所有未捕获异常都通过此方法输出，方便用户反馈和开发者定位。
        """
        import traceback as _tb

        tb = _tb.format_exc()
        # 1) 终端输出完整 traceback
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[{context}] 未捕获异常:", file=sys.stderr)
        print(tb, file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        # 2) 弹窗显示摘要
        msg = f"{context}\n\n{error}\n\n完整 traceback 已输出到终端。"
        QMessageBox.critical(self, f"异常 — {context}", msg)

        # 3) 状态栏
        self._safe_status(f"✗ {context}: {error}")

    def _safe_status(self, msg: str):
        """安全设置状态栏文本，忽略 C++ 对象已删除的 RuntimeError。

        同时推送到 StatusBar 的瞬态文本列。
        """
        try:
            if hasattr(self, "_status_bar") and self._status_bar is not None:
                self._status_bar.set_message(msg)
        except RuntimeError:
            pass

    def _set_temp_status(self, msg: str, role: str = "info"):
        """记录操作日志（仅写 LogPanel，不再推送 StatusBar）。"""
        if hasattr(self, "_log_panel") and self._log_panel is not None:
            self._log_panel.log(msg, role)

    def _on_worker_error(self, context: str, tb_str: str):
        """后台工作线程异常回调。已在终端输出完整 traceback，此处弹窗通知。"""
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[后台线程异常] {context}", file=sys.stderr)
        if tb_str:
            print(tb_str, file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)
        QMessageBox.critical(
            self,
            f"后台任务异常 — {context}",
            f"{context}\n\n完整 traceback 已输出到终端。",
        )
        self._safe_status(f"✗ 后台异常: {context}")

    def _on_show_all_snaps(self):
        """空状态页的「查看全部文本对」按钮回调。

        取消勾选筛选面板的「仅显示异常文本对」复选框并触发刷新，
        使表格切换到显示所有文本对的模式。
        """
        if hasattr(self, "_filter_panel"):
            self._filter_panel._anomaly_only_cb.setChecked(False)
            self._filter_panel._sync_anomaly_only_controls()
            self._filter_panel.filter_changed.emit()
        if hasattr(self, "_table_stack"):
            self._table_stack.setCurrentIndex(0)

    # ═══════════════════════════════════════════════════════════════
    # 查看文件 — 用系统默认编辑器打开相关文件
    # ═══════════════════════════════════════════════════════════════

    def _view_file_safe(self, path: str, label: str) -> None:
        """安全地打开文件（存在时用系统默认程序，不存在时弹提示）。"""
        if not path or not os.path.isfile(path):
            QMessageBox.information(
                self,
                "文件未找到",
                f"文件不存在：{label}\n\n{path or '（路径为空）'}\n\n"
                f"请先加载文件对并完成对齐导出。",
            )
            return
        try:
            os.startfile(path)
            self._set_temp_status(f"已打开 {label}", "info")
        except Exception as e:
            QMessageBox.warning(
                self,
                "打开失败",
                f"无法打开文件：{label}\n\n{path}\n\n错误：{e}",
            )

    def _on_view_source(self):
        """打开源文件（原文）。"""
        path = getattr(self, "_src_path", "")
        self._view_file_safe(path, "源文件（原文）")

    def _on_view_target(self):
        """打开源文件（译文）。"""
        path = getattr(self, "_tgt_path", "")
        self._view_file_safe(path, "源文件（译文）")

    def _on_view_report(self):
        """打开修复报告（report.json）。"""
        entry_id = getattr(self, "_current_entry_id", "")
        repaired_dir = getattr(self, "_repaired_dir", "")
        if not entry_id or not repaired_dir:
            # 回退：从 _session_path 推断
            sp = self._session_path() if hasattr(self, "_session_path") else ""
            if sp and os.path.isfile(sp):
                self._view_file_safe(sp, "修复报告")
                return
            QMessageBox.information(
                self,
                "文件未找到",
                "请先加载文件对并完成对齐导出。",
            )
            return
        path = os.path.join(repaired_dir, f"{entry_id}.report.json")
        self._view_file_safe(path, "修复报告")

    def _on_view_repaired_source(self):
        """打开修复后原文。"""
        sp = self._session_path() if hasattr(self, "_session_path") else ""
        if not sp:
            QMessageBox.information(
                self,
                "文件未找到",
                "请先加载文件对并完成对齐导出。",
            )
            return
        base = os.path.splitext(sp)[0]
        path = base + ".source.md"
        self._view_file_safe(path, "修复后原文")

    def _on_view_repaired_target(self):
        """打开修复后译文。"""
        sp = self._session_path() if hasattr(self, "_session_path") else ""
        if not sp:
            QMessageBox.information(
                self,
                "文件未找到",
                "请先加载文件对并完成对齐导出。",
            )
            return
        base = os.path.splitext(sp)[0]
        path = base + ".target.md"
        self._view_file_safe(path, "修复后译文")
