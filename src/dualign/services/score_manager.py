"""
Dualign — ScoreManager: 统一评分管理器

评分粒度：每对 (snap_index, sub) 独立评分。
异步 + 防抖 + 轮询自愈 + 信号驱动局部刷新。
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

from PySide6.QtCore import Qt, QObject, QTimer, Signal, Slot

from dualign.services.similarity import SimilarityScorer

logger = logging.getLogger(__name__)

SCORE_STATE_PENDING = "pending"
SCORE_STATE_LOADING = "loading"
SCORE_STATE_READY = "ready"
SCORE_STATE_FAILED = "failed"

# ═══════════════════════════════════════════════════════════════
# key 编码：将 (snap_index, sub) 编为单个 int 供 worker dict 用
# ═══════════════════════════════════════════════════════════════

_KEY_SHIFT = 1000000


def _encode_key(snap_index: int, sub: int) -> int:
    return snap_index * _KEY_SHIFT + sub


def _decode_key(key: int) -> Tuple[int, int]:
    return (key // _KEY_SHIFT, key % _KEY_SHIFT)


# ═══════════════════════════════════════════════════════════════
# 内部条目
# ═══════════════════════════════════════════════════════════════


class _ScoreEntry:
    __slots__ = ("score", "state", "timestamp", "request_seq")

    def __init__(
        self,
        score: Optional[float] = None,
        state: str = SCORE_STATE_PENDING,
        request_seq: int = 0,
    ):
        self.score = score
        self.state = state
        self.timestamp = time.time()
        self.request_seq = request_seq


# ═══════════════════════════════════════════════════════════════
# ScoreWorker
# ═══════════════════════════════════════════════════════════════


class ScoreWorker(QObject):
    finished = Signal(object, int)
    error = Signal(str, int)
    _trigger = Signal()

    def __init__(self, scorer: SimilarityScorer, parent=None):
        super().__init__(parent)
        self._scorer = scorer
        self._pairs: list = []
        self._request_seq: int = 0
        self._cancelled = False

    def assign(self, pairs: list, request_seq: int):
        self._pairs = pairs
        self._request_seq = request_seq
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @Slot()
    def run(self):
        if self._cancelled or not self._pairs:
            self.finished.emit({}, self._request_seq)
            return
        try:
            keys = [p[0] for p in self._pairs]
            src_texts = [p[1] for p in self._pairs]
            tgt_texts = [p[2] for p in self._pairs]

            scores = self._scorer.score_pairs(src_texts, tgt_texts)

            if self._cancelled:
                self.finished.emit({}, self._request_seq)
                return

            results = {}
            for i, k in enumerate(keys):
                results[k] = float(scores[i]) if i < len(scores) else 0.0
            self.finished.emit(results, self._request_seq)
        except Exception as e:
            logger.error(f"ScoreWorker 评分失败: {e}", exc_info=True)
            self.error.emit(str(e), self._request_seq)


# ═══════════════════════════════════════════════════════════════
# ScoreManager
# ═══════════════════════════════════════════════════════════════


class ScoreManager(QObject):
    """以 (snap_index, sub) 为粒度的统一评分管理器。"""

    score_updated = Signal(int, int, float)  # (snap_index, sub, score)
    status_changed = Signal(int, int, str)  # (snap_index, sub, state)
    flat_batch_ready = Signal(int, object)

    _DEBOUNCE_MS = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: dict = {}  # (snap_index, sub) -> _ScoreEntry
        self._scorer: Optional[SimilarityScorer] = None
        self._text_provider = None  # callable(snap_index, sub) -> (src, tgt) or None

        self._worker: Optional[ScoreWorker] = None
        self._worker_thread = None
        self._request_seq: int = 0
        self._pending_req: dict = {}  # {(snap_index,sub): latest_seq}

        self._pending_flat_batch_id: Optional[int] = None

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(self._DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._flush_pending)
        self._debounced_pairs: list = []

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(3000)
        self._poll_timer.timeout.connect(self._poll)

        self._flush_in_progress = False

    # ═════════════════════════════════════════════════════════
    # 公开 API — 以 (snap_index, sub) 为参数
    # ═════════════════════════════════════════════════════════

    def set_scorer(self, scorer: Optional[SimilarityScorer]):
        self._scorer = scorer

    @property
    def has_scorer(self) -> bool:
        return self._scorer is not None

    def get_score_state(self, snap_index: int, sub: int = 0) -> tuple:
        """返回 (score_or_None, state_str)。"""
        key = (snap_index, sub)
        entry = self._cache.get(key)
        if entry is None:
            return (None, SCORE_STATE_PENDING)
        return (entry.score, entry.state)

    def set_ready_score(self, snap_index: int, sub: int, score: float):
        self._cache[(snap_index, sub)] = _ScoreEntry(
            score=score, state=SCORE_STATE_READY
        )

    def set_text_provider(self, provider):
        """provider(snap_index, sub) -> (src_text, tgt_text) or None"""
        self._text_provider = provider

    def start_polling(self):
        if not self._poll_timer.isActive():
            self._poll_timer.start()

    def poll_now(self):
        self._poll()

    def _poll(self):
        if not self._text_provider or not self.has_scorer:
            return
        for key, entry in list(self._cache.items()):
            if entry.state != SCORE_STATE_PENDING:
                continue
            si, sub = key
            texts = self._text_provider(si, sub)
            if texts is not None:
                self.request_score(si, sub, texts[0], texts[1])

    def invalidate(self, snap_index: Optional[int] = None, sub: Optional[int] = None):
        """失效。snap_index=None 清空，sub=None 失效该 snap 全部子行。

        与旧版不同：指定 sub 时即使 cache 中尚无该 key 也创建 PENDING
        条目。这是 split 等操作创建新子行所必需的——新子行必须出现在
        _cache 中，_poll 才能发现并申请评分。
        """
        if snap_index is None:
            self._cache.clear()
            self._pending_req.clear()
            return
        if sub is not None:
            key = (snap_index, sub)
            self._cache[key] = _ScoreEntry(state=SCORE_STATE_PENDING)
            self._pending_req.pop(key, None)
        else:
            keys = [k for k in self._cache if k[0] == snap_index]
            for k in keys:
                self._cache[k] = _ScoreEntry(state=SCORE_STATE_PENDING)
                self._pending_req.pop(k, None)
            if not keys:
                self._cache[(snap_index, 0)] = _ScoreEntry(state=SCORE_STATE_PENDING)

    def invalidate_snaps(self, snap_indices: list[int]):
        for si in snap_indices:
            self.invalidate(si)

    def request_score(self, snap_index: int, sub: int, src_text: str, tgt_text: str):
        if self._scorer is None:
            return

        key = (snap_index, sub)
        seq = self._request_seq + 1
        self._request_seq = seq
        self._pending_req[key] = seq
        self._cache[key] = _ScoreEntry(state=SCORE_STATE_LOADING, request_seq=seq)
        self.status_changed.emit(snap_index, sub, SCORE_STATE_LOADING)

        enc_key = _encode_key(snap_index, sub)
        self._debounced_pairs.append((enc_key, src_text, tgt_text))
        self._debounce_timer.start()

    # ═════════════════════════════════════════════════════════
    # 预览表扁平评分
    # ═════════════════════════════════════════════════════════

    def request_flat_batch(
        self, src_texts: list[str], tgt_texts: list[str], batch_id: int = 0
    ) -> int:
        if self._scorer is None or not src_texts or not tgt_texts:
            return batch_id

        n = max(len(src_texts), len(tgt_texts))
        src = list(src_texts) + [""] * (n - len(src_texts))
        tgt = list(tgt_texts) + [""] * (n - len(tgt_texts))

        pairs = [(-(i + 1), src[i], tgt[i]) for i in range(n)]
        seq = self._request_seq + 1
        self._request_seq = seq
        self._pending_flat_batch_id = batch_id

        self._ensure_worker()
        self._worker.assign(pairs, seq)
        self._worker._trigger.emit()
        return batch_id

    # ═════════════════════════════════════════════════════════
    # 内部
    # ═════════════════════════════════════════════════════════

    def _flush_pending(self):
        if self._flush_in_progress or not self._debounced_pairs:
            return
        self._flush_in_progress = True
        try:
            pairs = self._debounced_pairs
            self._debounced_pairs = []
            self._do_score_async(pairs, self._request_seq)
        finally:
            self._flush_in_progress = False

    def _do_score_async(self, pairs: list, seq: int):
        if not pairs:
            return
        # 保护：flat batch 已发出尚未完成时，不覆写 worker 的作业
        if self._pending_flat_batch_id is not None:
            return
        self._ensure_worker()
        self._worker.assign(pairs, seq)
        self._worker._trigger.emit()

    def _ensure_worker(self):
        from PySide6.QtCore import QThread

        if self._worker_thread is not None and self._worker_thread.isRunning():
            return

        self._worker_thread = QThread(self)
        self._worker_thread.setObjectName("ScoreWorkerThread")

        scorer_copy = None
        if self._scorer is not None:
            from dualign.services.similarity import SimilarityScorer

            scorer_copy = SimilarityScorer(
                entry_id=self._scorer.entry_id,
                cache_dir=getattr(self._scorer, "_cache_dir", ""),
            )

        self._worker = (
            ScoreWorker(scorer_copy) if scorer_copy else ScoreWorker(self._scorer)
        )
        self._worker.moveToThread(self._worker_thread)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker._trigger.connect(
            self._worker.run, Qt.ConnectionType.QueuedConnection
        )
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.start()

    def _on_worker_finished(self, results: dict, seq: int):
        # ── 扁平批次 ──
        if self._pending_flat_batch_id is not None:
            # 扁平批次键为负整数 (-1, -2, ...)；子行评分键为
            # snap_index * 10000 + sub（非负）。竞态下子行结果可能
            # 在扁平批次等待期间到达，必须通过键符号区分，否则会
            # 按 max(abs(k)) 构造出巨型数组导致下游崩溃。
            if results:
                first_key = next(iter(results))
                if not (isinstance(first_key, int) and first_key < 0):
                    # 子行评分结果到达：说明 flat batch 被后续 peri-subrow
                    # _flush_pending 覆盖了 worker。清除 pending 标记，
                    # 让下一轮 _render_preview 能重新发起请求。
                    self._pending_flat_batch_id = None
                else:
                    batch_id = self._pending_flat_batch_id
                    self._pending_flat_batch_id = None
                    import numpy as np

                    n_positions = max(abs(k) for k in results) if results else 0
                    scores = np.zeros(n_positions, dtype=np.float64)
                    for neg_pos, sc in results.items():
                        idx = abs(neg_pos) - 1
                        if 0 <= idx < n_positions:
                            scores[idx] = sc
                    self.flat_batch_ready.emit(batch_id, scores)
                    return
            else:
                # 空结果：取消或出错，仍按扁平批次处理
                batch_id = self._pending_flat_batch_id
                self._pending_flat_batch_id = None
                self.flat_batch_ready.emit(batch_id, None)
                return

        # ── 子行评分 ──
        for enc_key, score in results.items():
            snap_index, sub = _decode_key(enc_key)
            key = (snap_index, sub)
            pending_seq = self._pending_req.get(key)
            if pending_seq is not None and seq < pending_seq:
                continue

            self._cache[key] = _ScoreEntry(
                score=score, state=SCORE_STATE_READY, request_seq=seq
            )
            self._pending_req.pop(key, None)
            self.score_updated.emit(snap_index, sub, score)
            self.status_changed.emit(snap_index, sub, SCORE_STATE_READY)

    def _on_worker_error(self, error_msg: str, seq: int):
        logger.error(f"ScoreWorker 错误 (seq={seq}): {error_msg}")

        # 扁平批次错误：仅当 worker 处理的是扁平批次时发送 None
        if self._pending_flat_batch_id is not None:
            batch_id = self._pending_flat_batch_id
            self._pending_flat_batch_id = None
            self.flat_batch_ready.emit(batch_id, None)
            # 注意：即使是子行评分出错，只要 pending_flat_batch_id
            # 已设置，也发送 flat_batch_ready(None) 让预览表回退到
            # 全零评分，避免预览表永久等待。

        for key, s in list(self._pending_req.items()):
            if s > seq:
                continue
            snap_index, sub = key
            entry = self._cache.get(key)
            if entry is not None and entry.state == SCORE_STATE_LOADING:
                self._cache[key] = _ScoreEntry(state=SCORE_STATE_FAILED)
                self._pending_req.pop(key, None)
                self.status_changed.emit(snap_index, sub, SCORE_STATE_FAILED)

    def cleanup(self):
        if self._worker is not None:
            self._worker.cancel()
        if self._worker_thread is not None and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
            self._worker_thread = None
            self._worker = None
