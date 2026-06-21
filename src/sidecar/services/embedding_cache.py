"""
Dualign — EmbeddingCache: 行级嵌入向量缓存（SQLite 后端）

基于 SQLite WAL 模式，每行文本以 content_hash 为键独立存储。
改一行只编一行，不改的行缓存命中 → 零编码开销。

用法:
    cache = EmbeddingCache(db_path)
    vec = cache.get("a1b2c3...")
    cache.put_batch([("hash1", vec1, model), ("hash2", vec2, model)])
    arr, misses = cache.get_all_embeddings(texts)
    cache.remove_unused(active_hashes_set)
"""

from __future__ import annotations

import sqlite3
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingCache:
    """行级嵌入向量缓存，SQLite 后端 (WAL 模式)。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ── 连接管理 ────────────────────────────────────────────

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS vecs(
                hash       TEXT PRIMARY KEY,
                blob       BLOB    NOT NULL,
                model      TEXT    NOT NULL,
                dim        INTEGER NOT NULL,
                created_at TEXT    DEFAULT (datetime('now'))
            )""")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS merge_cache(
                side       TEXT    NOT NULL,
                snap_i     INTEGER NOT NULL,
                sub_key    TEXT    NOT NULL,
                blob       BLOB    NOT NULL,
                model      TEXT    NOT NULL,
                dim        INTEGER NOT NULL,
                created_at TEXT    DEFAULT (datetime('now')),
                PRIMARY KEY (side, snap_i, sub_key)
            )""")
            self._conn.commit()
        return self._conn

    def close(self):
        """显式关闭连接。"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ═══════════════════════════════════════════════════════════
    # 行级嵌入：vecs 表
    # ═══════════════════════════════════════════════════════════

    def get(self, text_hash: str) -> Optional[np.ndarray]:
        """单行查询。未命中返回 None。"""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT blob, dim FROM vecs WHERE hash=?", (text_hash,)
        ).fetchone()
        if row is None:
            return None
        return np.frombuffer(row[0], dtype=np.float32).reshape((row[1],))

    def get_batch(self, hashes: list[str]) -> dict[str, np.ndarray]:
        """批量查询。返回 {hash: vector}，未命中者不在结果中。"""
        if not hashes:
            return {}
        conn = self._ensure_conn()
        placeholders = ",".join("?" * len(hashes))
        rows = conn.execute(
            f"SELECT hash, blob, dim FROM vecs WHERE hash IN ({placeholders})",
            hashes,
        ).fetchall()
        return {
            r[0]: np.frombuffer(r[1], dtype=np.float32).reshape((r[2],)) for r in rows
        }

    def put(self, text_hash: str, vec: np.ndarray, model: str):
        """单行写入。"""
        conn = self._ensure_conn()
        conn.execute(
            "INSERT OR REPLACE INTO vecs (hash, blob, model, dim) VALUES (?,?,?,?)",
            (text_hash, vec.astype(np.float32).tobytes(), model, vec.shape[0]),
        )
        conn.commit()

    def put_batch(self, items: list[tuple[str, np.ndarray, str]]):
        """批量写入（单事务）。"""
        conn = self._ensure_conn()
        rows = [(h, v.astype(np.float32).tobytes(), m, v.shape[0]) for h, v, m in items]
        conn.executemany(
            "INSERT OR REPLACE INTO vecs (hash, blob, model, dim) VALUES (?,?,?,?)",
            rows,
        )
        conn.commit()

    # ═══════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════
    # 维护
    # ═══════════════════════════════════════════════════════════

    @property
    def count(self) -> int:
        """缓存中的条目总数。"""
        conn = self._ensure_conn()
        return conn.execute("SELECT COUNT(*) FROM vecs").fetchone()[0]

    @property
    def size_bytes(self) -> int:
        """数据库文件大小（字节）。"""
        import os

        if os.path.isfile(self._db_path):
            return os.path.getsize(self._db_path)
        return 0
