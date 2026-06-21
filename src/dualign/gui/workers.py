"""
Dualign — GUI 后台工作线程

所有 QThread 子类集中在此，供 DualignWindow 使用。
嵌入缓存统一由 EmbeddingCache (SQLite 行级) 管理。
"""

from __future__ import annotations

import os
import sys
import time
import logging
import threading

import numpy as np
from PySide6.QtCore import QThread, Signal

from dualign.core import (
    align,
    AlignConfig,
    AlignmentResult,
)
from dualign.common import (
    load_text_lines,
    content_hash as _content_hash,
)
from dualign.config import (
    get_embedding_cache_dir,
)
from dualign.services.embedding import (
    load_model_for_provider,
    _try_lazy_load_model,
)
from dualign.services.cached_encoder import CachedEncoder
from dualign.services.embedding_cache import EmbeddingCache

logger = logging.getLogger(__name__)


# ── 编码线程 ────────────────────────────────────────────────


class EncodeThread(QThread):
    status_signal = Signal(str)
    finished_signal = Signal(
        np.ndarray,
        np.ndarray,
        list,
        list,
        str,
        str,
    )
    text_ready_signal = Signal(str, str, list, list)
    error_signal = Signal(str, str)

    def __init__(
        self,
        src_path,
        tgt_path,
        parent=None,
        src_lines=None,
        tgt_lines=None,
        entry_id="",
    ):
        super().__init__(parent)
        self.src_path = src_path
        self.tgt_path = tgt_path
        self._src_lines = src_lines
        self._tgt_lines = tgt_lines
        self.entry_id = entry_id
        self.time_s = 0.0
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            self._run_impl()
        except Exception as e:
            import traceback as _tb

            tb_str = _tb.format_exc()
            print(f"\n{'='*60}", file=sys.stderr)
            print("[EncodeThread] 未捕获异常:", file=sys.stderr)
            print(tb_str, file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
            self.error_signal.emit(f"编码失败: {e}", tb_str)

    def _run_impl(self):
        t0 = time.time()

        if self._src_lines is not None and self._tgt_lines is not None:
            src_lines = self._src_lines
            tgt_lines = self._tgt_lines
        else:
            src_lines = load_text_lines(self.src_path)
            tgt_lines = load_text_lines(self.tgt_path)
        src_hash = _content_hash(src_lines)
        tgt_hash = _content_hash(tgt_lines)
        self.text_ready_signal.emit(src_hash, tgt_hash, src_lines, tgt_lines)

        # ── 加载模型 ──
        model = _try_lazy_load_model()
        if model is None:
            self.status_signal.emit("模型加载中……")
            model = load_model_for_provider()
        if model is None:
            self.status_signal.emit("✗ 模型加载失败")
            self.error_signal.emit(
                "模型加载失败",
                "无法连接到嵌入模型（Ollama）。请确保已启动 Ollama 并拉取了所需模型。",
            )
            return
        if self._stop_event.is_set():
            return

        # ── CachedEncoder: 统一缓存代理 ──
        cache_dir = get_embedding_cache_dir(self.entry_id)
        db_path = os.path.join(cache_dir, "vecs.db")
        cache = EmbeddingCache(db_path)
        cenc = CachedEncoder(model, cache)

        # ── 缓存优先编码（内部自动查缓存 / 编码 / 回存）──
        src_emb = cenc.encode(src_lines)
        if self._stop_event.is_set():
            return
        tgt_emb = cenc.encode(tgt_lines)

        self.time_s = time.time() - t0
        self.status_signal.emit(
            f"✓ 嵌入编码完成 — {self.time_s:.1f}s "
            f"({len(src_lines)}×{len(tgt_lines)} 行, "
            f"缓存命中率 {cenc.cache_hit_rate:.0%})"
        )
        self.finished_signal.emit(
            src_emb,
            tgt_emb,
            src_lines,
            tgt_lines,
            src_hash,
            tgt_hash,
        )


# ── 对齐线程 ────────────────────────────────────────────────


class AlignWorker(QThread):
    status_signal = Signal(str)
    progress_signal = Signal(int)
    finished_signal = Signal(AlignmentResult)
    error_signal = Signal(str, str)

    def __init__(
        self,
        config: AlignConfig,
        src_emb,
        tgt_emb,
        src_lines,
        tgt_lines,
        encode_fn=None,
        src_path: str = "",
        tgt_path: str = "",
        entry_id: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.src_path = src_path
        self.tgt_path = tgt_path
        self.entry_id = entry_id
        self.src_emb = src_emb
        self.tgt_emb = tgt_emb
        self.src_lines = src_lines
        self.tgt_lines = tgt_lines
        self.encode_fn = encode_fn
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            self._run_impl()
        except Exception as e:
            import traceback as _tb

            tb_str = _tb.format_exc()
            print(f"\n{'='*60}", file=sys.stderr)
            print("[AlignWorker] 未捕获异常:", file=sys.stderr)
            print(tb_str, file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
            self.status_signal.emit(f"对齐异常: {e}")
            self.error_signal.emit(f"对齐失败: {e}", tb_str)

    def _run_impl(self):
        if self._stop_event.is_set():
            return
        self.status_signal.emit("计算对齐方案……")
        self.progress_signal.emit(10)

        # ── CachedEncoder: 合并文本编码也走缓存 ──
        encode_fn = self.encode_fn
        if self.entry_id and encode_fn:
            cache_dir = get_embedding_cache_dir(self.entry_id)
            db_path = os.path.join(cache_dir, "vecs.db")
            cache = EmbeddingCache(db_path)
            # 从 encode_fn 反查 model 引用（window_actions 传入的是 model.encode）
            from dualign.services.embedding import _try_lazy_load_model

            model = _try_lazy_load_model()
            if model is not None:
                cenc = CachedEncoder(model, cache)
                encode_fn = cenc.encode

        result = align(
            self.src_lines,
            self.tgt_lines,
            self.src_emb,
            self.tgt_emb,
            self.config,
            encode_fn=encode_fn,
        )
        self.progress_signal.emit(100)
        s = result.stats
        self.status_signal.emit(
            f"✓ 对齐完成 — 真锚点 {s['n_true_anchors']}/{s['n_restricted_ops']}, "
            f"{len(result.all_ops)} ops (μ{s['avg_similarity']:.3f}), "
            f"矩阵 {s['sim_time_s']:.2f}s + 锚点 {s['anchor_time_s']:.2f}s "
            f"+ DP {s['dp_time_s']:.2f}s = {s['align_time_s']:.2f}s"
        )
        self.finished_signal.emit(result)


# ── 一键修复后台线程 ────────────────────────────────────────


class AutoRepairWorker(QThread):
    """后台执行一键修复，避免阻塞主线程（拆分需模型编码）。"""

    status_signal = Signal(str)
    finished_signal = Signal(object)
    error_signal = Signal(str, str)

    def __init__(
        self,
        state,
        strategy: str = "src",
        model=None,
        cache=None,
        parent=None,
    ):
        super().__init__(parent)
        self._state = state
        self._strategy = strategy
        self._model = model
        self._cache = cache

    def run(self):
        from dualign.services.repair import RepairService

        self.status_signal.emit("一键修复中…")
        try:
            before = len(self._state._repair_log)
            result = RepairService.auto_repair(
                self._state, self._strategy, model=self._model, cache=self._cache
            )
            for act in result._repair_log[before:]:
                act.data["approvals"] = {"auto"}
            self.finished_signal.emit(result)
        except Exception as e:
            import traceback as _tb

            tb_str = _tb.format_exc()
            print(f"\n{'='*60}", file=sys.stderr)
            print("[AutoRepairWorker] 未捕获异常:", file=sys.stderr)
            print(tb_str, file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)
            self.status_signal.emit(f"修复异常: {e}")
            self.error_signal.emit(f"自动修复失败: {e}", tb_str)


# ── 环境检测线程 ────────────────────────────────────────────


class EnvCheckThread(QThread):
    """后台异步检测运行环境（嵌入模型 + AI 审校 Agent），不阻塞 UI。"""

    env_checked = Signal(dict)  # 包含所有检测结果的字典

    def run(self):
        result: dict = {}

        # 1. 嵌入服务检测
        try:
            from dualign.providers import ProviderManager

            ProviderManager.load()
            active = ProviderManager.active()
            if active:
                ok, detail, models = ProviderManager.health_check(active)
                result["embed_ok"] = ok
                result["embed_detail"] = detail
                result["embed_provider"] = active.label
                result["embed_model"] = active.model_name
                result["models_available"] = models
            else:
                result["embed_ok"] = False
                result["embed_detail"] = "未配置嵌入提供方"
                result["embed_provider"] = ""
                result["embed_model"] = ""
                result["models_available"] = []
        except Exception as e:
            import traceback as _tb

            _tb.print_exc()
            result["embed_ok"] = False
            result["embed_detail"] = f"检测失败: {e}"
            result["embed_provider"] = ""
            result["embed_model"] = ""
            result["models_available"] = []

        # 2. AI Agent 检测
        try:
            from dualign.providers import active_repair_agent

            agent = active_repair_agent()
            if agent:
                has_url = bool(agent.base_url and agent.base_url.strip())
                has_key = bool(agent.api_key)
                result["ai_ok"] = has_url and has_key
                result["ai_detail"] = agent.label
            else:
                result["ai_ok"] = False
                result["ai_detail"] = "未配置（可选）"
        except Exception:
            import traceback as _tb

            _tb.print_exc()
            result["ai_ok"] = False
            result["ai_detail"] = "检测失败"

        self.env_checked.emit(result)
