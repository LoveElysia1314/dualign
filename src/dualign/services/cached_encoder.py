"""
Dualign — CachedEncoder: 嵌入编码的统一缓存代理

所有文本编码调用通过此代理完成。以 content_hash 为键，通过
EmbeddingCache 自动复用已编码向量。对调用方透明——签名兼容
OllamaEncoder.encode()。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from dualign.common import content_hash as _content_hash
from dualign.common import instruction_hash as _instruction_hash

if TYPE_CHECKING:
    from dualign.services.embedding_cache import EmbeddingCache

logger = logging.getLogger(__name__)


class CachedEncoder:
    """嵌入编码的统一缓存代理。

    encode() 与 OllamaEncoder.encode() 签名兼容：入参 list[str]，
    返回 np.ndarray[N, dim]。可用在任何需要 encode_fn 的地方。
    """

    def __init__(
        self,
        encoder,
        cache: "EmbeddingCache",
        model_name: str = "",
    ):
        """
        Args:
            encoder: OllamaEncoder | OpenAICompatibleEncoder 实例
            cache: EmbeddingCache 实例（通常指向 {entry_id}/vecs.db）
            model_name: 存入缓存的模型标识。空时自动从 encoder._model 读取
        """
        self._encoder = encoder
        self._cache = cache
        self._model_name = model_name or getattr(encoder, "_model", "unknown")
        # 使用编码器实际使用的 instruction 文本构建缓存键
        # （而非全局 INSTRUCTION_TEXT），确保不同提供方的缓存自然隔离
        actual_instruction = getattr(encoder, "_instruction", None) or ""
        self._instr_hash = (
            _instruction_hash(actual_instruction) if actual_instruction else "noinstr"
        )
        self._key_prefix = f"{self._model_name}_{self._instr_hash}"
        self._hit_count = 0
        self._miss_count = 0

    # ── 统计 ──

    @property
    def hit_count(self) -> int:
        return self._hit_count

    @property
    def miss_count(self) -> int:
        return self._miss_count

    @property
    def cache_hit_rate(self) -> float:
        total = self._hit_count + self._miss_count
        return self._hit_count / total if total > 0 else 0.0

    # ── 核心 ──

    def encode(self, texts: list[str]) -> np.ndarray:
        """缓存优先的批量编码。

        流程:
          1. 逐行 content_hash → SQLite get_batch
          2. 未命中的行 → 调用底层 encoder → L2 归一化 → put_batch
          3. 按原始顺序组装 ndarray[N, dim]

        Args:
            texts: 文本行列表

        Returns:
            np.ndarray[N, dim] — L2 归一化嵌入向量
            空输入 → np.zeros((0, dim))
        """
        if not texts:
            return np.zeros((0, 768), dtype=np.float32)

        # 缓存键 = 内容哈希 + 模型名 + instruction 哈希
        # 任一变化 → 键不同 → 自然穿透到重新编码
        hashes = [f"{_content_hash([t])}_{self._key_prefix}" for t in texts]
        cached = self._cache.get_batch(hashes)

        # ── 收集 miss ──
        miss_texts: list[str] = []
        miss_hashes: list[str] = []
        for h, t in zip(hashes, texts):
            if h in cached:
                self._hit_count += 1
            else:
                self._miss_count += 1
                miss_texts.append(t)
                miss_hashes.append(h)

        # ── 编码 miss ──
        if miss_texts:
            miss_embs = np.array(
                self._encoder.encode(miss_texts, normalize_embeddings=True)
            )
            # 确保 L2 归一化（部分后端已归一化，冗余安全）
            norms = np.linalg.norm(miss_embs, axis=1, keepdims=True)
            miss_embs = miss_embs / np.maximum(norms, 1e-12)

            # 回存
            self._cache.put_batch(
                [
                    (miss_hashes[i], miss_embs[i], self._model_name)
                    for i in range(len(miss_hashes))
                ]
            )

            # 回填到 cached
            for h, v in zip(miss_hashes, miss_embs):
                cached[h] = v

        return np.stack([cached[h] for h in hashes])

    def __call__(self, texts):
        return self.encode(texts)
