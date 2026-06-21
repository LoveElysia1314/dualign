"""
Dualign — Demo 文件加载器

统一的 demo 文件查找入口，供 GUI 欢迎页和 demo_gui.py 共同使用。

用法:
    from dualign.demo import get_demo_paths
    src, tgt, label = get_demo_paths()
"""

from __future__ import annotations

import sys
from pathlib import Path

LABEL = "demo: 与天使相遇"


def _find_demo_dir() -> Path:
    """在源码树和打包环境中定位 demo/ 目录。"""
    # 源码树: 从 src/dualign/demo.py → ../../demo/
    candidate = Path(__file__).resolve().parent.parent.parent / "demo"
    if candidate.is_dir():
        return candidate
    # PyInstaller 打包: _MEIPASS 同级 demo/
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "demo"
        if candidate.is_dir():
            return candidate
    # 打包后: sys.executable 同级 demo/
    exe_dir = Path(sys.executable).parent / "demo"
    if exe_dir.is_dir():
        return exe_dir
    raise FileNotFoundError(
        "找不到 demo 目录。请确保源码树或打包目录中包含 demo/ 文件夹。"
    )


def get_demo_paths() -> tuple[str, str, str]:
    """返回 (src_path, tgt_path, label)

    供 GUI 欢迎页的"体验 Demo"按钮和 demo_gui.py 共同使用。
    无需缓存隔离——统一使用默认缓存目录，与普通文件对行为一致。
    """
    root = _find_demo_dir()
    src = root / "raw" / "sample.source.md"
    tgt = root / "raw" / "sample.target.md"
    for p, name in [(src, "原文"), (tgt, "译文")]:
        if not p.is_file():
            raise FileNotFoundError(f"Demo {name}文件不存在: {p}")
    return str(src), str(tgt), LABEL
