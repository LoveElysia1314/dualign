"""
Dualign — EmbeddingCache: 行级嵌入向量缓存（SQLite 后端）

基于 SQLite WAL 模式，每行文本以 content_hash 为键独立存储。
改一行只编一行，不改的行缓存命中 → 零编码开销。

用法:
    with EmbeddingCache(db_path) as cache:
        vec = cache.get("a1b2c3...")
        cache.put_batch([("hash1", vec1, model), ("hash2", vec2, model)])
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np


class EmbeddingCache:
    """行级嵌入向量缓存，SQLite 后端 (WAL 模式)。"""

    _QUERY_BATCH_SIZE = 900

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ── 连接管理 ────────────────────────────────────────────

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._db_path, timeout=30.0)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("PRAGMA wal_autocheckpoint=1000")
                conn.execute("""CREATE TABLE IF NOT EXISTS vecs(
                    hash       TEXT PRIMARY KEY,
                    blob       BLOB    NOT NULL,
                    model      TEXT    NOT NULL,
                    dim        INTEGER NOT NULL,
                    created_at TEXT    DEFAULT (datetime('now'))
                )""")
                conn.commit()
            except sqlite3.Error:
                conn.close()
                raise
            self._conn = conn
        return self._conn

    def close(self) -> None:
        """显式关闭连接（释放 Windows 上的文件锁）。"""
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> "EmbeddingCache":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

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
        unique_hashes = list(dict.fromkeys(hashes))
        result: dict[str, np.ndarray] = {}
        for offset in range(0, len(unique_hashes), self._QUERY_BATCH_SIZE):
            batch = unique_hashes[offset : offset + self._QUERY_BATCH_SIZE]
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT hash, blob, dim FROM vecs WHERE hash IN ({placeholders})",
                batch,
            ).fetchall()
            result.update(
                {
                    row[0]: np.frombuffer(row[1], dtype=np.float32).reshape((row[2],))
                    for row in rows
                }
            )
        return result

    def put_batch(self, items: list[tuple[str, np.ndarray, str]]) -> None:
        """批量写入（单事务）。"""
        if not items:
            return
        conn = self._ensure_conn()
        unique_items = {
            text_hash: (vector, model) for text_hash, vector, model in items
        }
        rows = [
            (
                text_hash,
                vector.astype(np.float32, copy=False).tobytes(),
                model,
                vector.shape[0],
            )
            for text_hash, (vector, model) in unique_items.items()
        ]
        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO vecs (hash, blob, model, dim) VALUES (?,?,?,?)",
                rows,
            )

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
        """数据库、WAL 和共享内存文件的总大小（字节）。"""
        return sum(
            path.stat().st_size
            for path in (
                Path(self._db_path),
                Path(f"{self._db_path}-wal"),
                Path(f"{self._db_path}-shm"),
            )
            if path.is_file()
        )
