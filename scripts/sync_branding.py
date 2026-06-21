#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sync_branding.py — 同步品牌资产到运行时资源包

将 assets/branding/ 中更新的图标文件复制到 src/dualign/resources/，
确保运行时能读到最新图标。

用法:
    python scripts/sync_branding.py            # 复制全部
    python scripts/sync_branding.py --check    # 仅检查有无差异
"""

from __future__ import annotations

import hashlib
import sys
import filecmp
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "assets" / "branding"
DST = PROJECT_ROOT / "src" / "dualign" / "resources"

# 需要同步到运行时的文件（源 → 目标，名对名）
SYNC_PAIRS: list[tuple[str, str]] = [
    ("dualign.ico", "dualign.ico"),
    ("dualign.svg", "dualign.svg"),
    ("dualign.png", "dualign.png"),
    ("dualign-outline.png", "dualign-outline.png"),
]


def _files_identical(src_file: Path, dst_file: Path) -> bool:
    if not src_file.is_file() or not dst_file.is_file():
        return False
    return filecmp.cmp(src_file, dst_file, shallow=False)


def main() -> int:
    check_only = "--check" in sys.argv
    changed = 0
    missing = 0
    identical = 0

    for src_name, dst_name in SYNC_PAIRS:
        src_file = SRC / src_name
        dst_file = DST / dst_name

        if not src_file.is_file():
            print(f"  ⚠ 源文件不存在: {src_file}")
            continue

        if _files_identical(src_file, dst_file):
            identical += 1
            continue

        if check_only:
            changed += 1
            print(f"  Δ {src_name} → 有差异")
        else:
            DST.mkdir(parents=True, exist_ok=True)
            dst_file.write_bytes(src_file.read_bytes())
            changed += 1
            print(f"  ✓ {src_name} → 已同步")

    if not src_file.is_file():
        print(f"  ⚠ 源目录不存在: {SRC}")
        return 1

    print()
    print(
        f"  总文件: {len(SYNC_PAIRS)}"
        f"  |  相同: {identical}"
        f"  |  {'差异' if check_only else '同步'}: {changed}"
        f"  |  缺失: {missing}"
    )

    if check_only and changed > 0:
        return 1  # exit code 1 = 有变更
    return 0


if __name__ == "__main__":
    sys.exit(main())
