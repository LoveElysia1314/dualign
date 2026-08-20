#!/usr/bin/env python
"""Consolidate legacy per-entry embedding databases into one global cache."""

from __future__ import annotations

import argparse
from pathlib import Path

from dualign.config import get_cache_root
from dualign.services.cache_migration import migrate_embedding_caches


def main() -> int:
    parser = argparse.ArgumentParser(
        description="合并 emb/{entry_id}/vecs.db 为全局 emb/vecs.db"
    )
    parser.add_argument(
        "--embedding-dir",
        type=Path,
        default=Path(get_cache_root()) / "emb",
    )
    parser.add_argument(
        "--remove-legacy",
        action="store_true",
        help="校验全部向量后删除旧的分章数据库",
    )
    args = parser.parse_args()

    def progress(index: int, total: int, source: Path) -> None:
        if index == 1 or index == total or index % 50 == 0:
            print(f"[{index}/{total}] {source.parent.name}")

    result = migrate_embedding_caches(
        args.embedding_dir,
        remove_legacy=args.remove_legacy,
        on_progress=progress,
    )
    print(f"source_databases: {result.source_databases}")
    print(f"source_rows: {result.source_rows}")
    print(f"inserted_rows: {result.inserted_rows}")
    print(f"target_rows: {result.target_rows}")
    print(f"removed_databases: {result.removed_databases}")
    print(f"database: {result.target_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
