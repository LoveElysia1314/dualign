"""
Dualign — 运行时资源包

包含 GUI 图标、品牌 logo 等资源文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def _icon_path() -> Optional[str]:
    """返回首个可用的图标文件路径，兼容开发环境和 PyInstaller 打包环境。

    优先级:
      1. 运行时资源目录 (dualign.ico)
      2. 运行时资源目录 (dualign.svg)
      3. 源资产目录 (assets/branding/dualign.ico)
    """
    import sys as _sys

    _pkg_dir = Path(__file__).parent
    if getattr(_sys, "frozen", False) and hasattr(_sys, "_MEIPASS"):
        _base = Path(_sys._MEIPASS)
        candidates = [
            _base / "dualign" / "resources" / "dualign.ico",
            _base / "dualign" / "resources" / "dualign.svg",
            _base / "assets" / "branding" / "dualign.ico",
        ]
    else:
        _root = _pkg_dir.parent.parent.parent
        candidates = [
            _pkg_dir / "dualign.ico",
            _pkg_dir / "dualign.svg",
            _root / "assets" / "branding" / "dualign.ico",
        ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def load_app_icon():
    """返回 QIcon 或 None（若所有候选路径均不存在）。"""
    path = _icon_path()
    if path is None:
        return None
    from PySide6.QtGui import QIcon

    return QIcon(path)
