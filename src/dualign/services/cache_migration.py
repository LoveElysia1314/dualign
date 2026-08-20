"""Migration helpers for consolidating per-entry embedding databases."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dualign.services.embedding_cache import EmbeddingCache


@dataclass(frozen=True)
class MigrationResult:
    source_databases: int
    source_rows: int
    inserted_rows: int
    target_rows: int
    removed_databases: int
    target_path: Path


def legacy_embedding_databases(embedding_dir: str | Path) -> list[Path]:
    """Return the old ``emb/{entry_id}/vecs.db`` files in stable order."""
    root = Path(embedding_dir).resolve()
    return sorted(
        path.resolve()
        for path in root.glob("*/vecs.db")
        if path.is_file() and path.parent.parent == root
    )


def migrate_embedding_caches(
    embedding_dir: str | Path,
    *,
    remove_legacy: bool = False,
    on_progress: Callable[[int, int, Path], None] | None = None,
) -> MigrationResult:
    """Merge old per-entry databases into ``embedding_dir/vecs.db``.

    The operation is idempotent. Legacy files are removed only after every
    source hash has been verified in the global database.
    """
    root = Path(embedding_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target_path = root / "vecs.db"
    sources = legacy_embedding_databases(root)

    with EmbeddingCache(str(target_path)) as cache:
        cache.count

    connection = sqlite3.connect(target_path, timeout=60.0)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=60000")

    source_rows = 0
    inserted_rows = 0
    verified_sources: list[Path] = []
    try:
        for index, source in enumerate(sources, 1):
            if on_progress is not None:
                on_progress(index, len(sources), source)
            connection.execute("ATTACH DATABASE ? AS legacy", (str(source),))
            try:
                rows = connection.execute(
                    "SELECT COUNT(*) FROM legacy.vecs"
                ).fetchone()[0]
                before = connection.total_changes
                with connection:
                    connection.execute("""
                        INSERT OR IGNORE INTO main.vecs(hash, blob, model, dim, created_at)
                        SELECT hash, blob, model, dim, created_at FROM legacy.vecs
                        """)
                inserted_rows += connection.total_changes - before
                source_rows += rows
                missing = connection.execute("""
                    SELECT COUNT(*)
                    FROM legacy.vecs AS source
                    WHERE NOT EXISTS (
                        SELECT 1 FROM main.vecs AS target
                        WHERE target.hash = source.hash
                    )
                    """).fetchone()[0]
                if missing:
                    raise RuntimeError(f"迁移校验失败: {source} 缺少 {missing} 条向量")
                verified_sources.append(source)
            finally:
                connection.execute("DETACH DATABASE legacy")

        target_rows = connection.execute("SELECT COUNT(*) FROM vecs").fetchone()[0]
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()

    removed = 0
    if remove_legacy:
        for source in verified_sources:
            for path in (
                source,
                Path(f"{source}-wal"),
                Path(f"{source}-shm"),
            ):
                if path.is_file():
                    path.unlink()
            try:
                source.parent.rmdir()
            except OSError:
                pass
            removed += 1

    return MigrationResult(
        source_databases=len(sources),
        source_rows=source_rows,
        inserted_rows=inserted_rows,
        target_rows=target_rows,
        removed_databases=removed,
        target_path=target_path,
    )
