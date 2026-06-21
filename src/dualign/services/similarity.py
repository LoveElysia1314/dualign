"""
Dualign 0.7.0 — SimilarityScorer: 统一文本对评分器

职责:
  1. 统一入口：谁要评分都来找我
  2. 缓存优先：先查 EmbeddingCache，miss 才调用编码器
  3. 批量编码：收集所有 miss → 一次 API 调用 → 存缓存 → 返回

用法:
    scorer = SimilarityScorer(entry_id="ch01")
    scores = scorer.score_pairs(
        ["hello", "world"],
        ["你好", "世界"],
    )
    # → np.array([0.85, 0.72])  每对余弦相似度

与现有系统的关系:
  - 与 EncodeThread / .npz 缓存互不干扰（后者专用供对齐核心使用）
  - 与 load_model_for_provider() 共享编码器模型实例
  - 纯 Python，无 GUI/Qt 依赖
"""

from __future__ import annotations

import os
import logging
from typing import Optional

import numpy as np

from dualign.config import (
    get_cache_root,
)
from dualign.services.embedding_cache import EmbeddingCache
from dualign.services.cached_encoder import CachedEncoder

logger = logging.getLogger(__name__)


class SimilarityScorer:
    """统一文本对评分器。

    线程安全说明：EmbeddingCache（SQLite WAL 模式）支持并发读；
    编码器（OllamaEncoder/OpenAICompatibleEncoder）非线程安全，
    SimilarityScorer 应在单线程中使用。
    """

    def __init__(
        self,
        entry_id: str = "",
        encoder_model: object = None,
        cache_dir: str = "",
    ):
        """
        Args:
            entry_id: 章节标识（用于缓存隔离），如 "ch01"
            encoder_model: 已初始化的编码器实例。None 时首次 encode() 延迟加载
            cache_dir: 缓存目录。空时自动使用 get_cache_root() / emb / entry_id
        """
        self._entry_id = entry_id
        self._encoder = encoder_model
        self._cache: Optional[EmbeddingCache] = None
        self._cache_dir = cache_dir

        # 命中/未命中统计
        self._hit_count = 0
        self._miss_count = 0

    # ── 属性 ──

    @property
    def cache(self) -> EmbeddingCache:
        if self._cache is None:
            cache_dir = self._cache_dir
            if not cache_dir:
                root = get_cache_root()
                cache_dir = os.path.join(root, "emb")
                if self._entry_id:
                    cache_dir = os.path.join(cache_dir, self._entry_id)
            os.makedirs(cache_dir, exist_ok=True)
            db_path = os.path.join(cache_dir, "vecs.db")
            self._cache = EmbeddingCache(db_path)
        return self._cache

    @property
    def entry_id(self) -> str:
        return self._entry_id

    # ── 编码器加载（延迟 + 复用全局缓存） ─────────────────

    def _ensure_encoder(self):
        """延迟加载编码器。复用 load_model_for_provider 的全局 _MODEL_CACHE。"""
        if self._encoder is not None:
            return
        from dualign.services.embedding import load_model_for_provider

        self._encoder = load_model_for_provider()

    # ── 核心：encode ───────────────────────────────────────

    def encode(self, texts: list[str]) -> np.ndarray:
        """缓存感知的批量编码。委托 CachedEncoder。"""
        self._ensure_encoder()
        cenc = CachedEncoder(self._encoder, self.cache)
        result = cenc.encode(texts)
        self._hit_count += cenc.hit_count
        self._miss_count += cenc.miss_count
        return result

    # ── 评分 ───────────────────────────────────────────────

    def score_pairs(
        self,
        src_texts: list[str],
        tgt_texts: list[str],
    ) -> np.ndarray:
        """逐行计算原文/译文的余弦相似度。

        - 两侧都有文本: dot(src_vec, tgt_vec) — 余弦值（向量已归一化）
        - 仅一侧有文本:  0.0
        - 行数不等:      短侧补齐空串（编码后为 0 向量 → 余弦 0.0）

        Args:
            src_texts: 原文文本行列表
            tgt_texts: 译文文本行列表

        Returns:
            np.ndarray[N] — 每行一个余弦相似度值，范围 [0, 1]
        """
        n = max(len(src_texts), len(tgt_texts))

        # 补齐到等长
        src = list(src_texts) + [""] * (n - len(src_texts))
        tgt = list(tgt_texts) + [""] * (n - len(tgt_texts))

        src_emb = self.encode(src)
        tgt_emb = self.encode(tgt)

        # 堆叠法：逐行余弦 = sum(a*b) / (norm(a) * norm(b))
        # 向量已归一化，所以 dot = 余弦
        scores = np.sum(src_emb * tgt_emb, axis=1)

        # 单侧文本 → 0.0
        src_empty = np.array([not bool(t.strip()) for t in src])
        tgt_empty = np.array([not bool(t.strip()) for t in tgt])
        scores[src_empty | tgt_empty] = 0.0

        return scores

    # ── 统计 ───────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        """缓存命中/未命中统计。"""
        return {
            "hit": self._hit_count,
            "miss": self._miss_count,
            "hit_rate": (
                self._hit_count / (self._hit_count + self._miss_count)
                if (self._hit_count + self._miss_count) > 0
                else 0.0
            ),
            "cache_entries": self.cache.count,
            "cache_size_bytes": self.cache.size_bytes,
            "entry_id": self._entry_id,
        }

    def close(self):
        """释放资源（关闭 SQLite 连接）。"""
        if self._cache is not None:
            self._cache.close()
            self._cache = None
