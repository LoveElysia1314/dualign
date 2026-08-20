"""Create disposable working copies for the bundled Demo document pair."""

from __future__ import annotations

import sys
import shutil
import tempfile
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


def get_demo_source_paths() -> tuple[Path, Path]:
    """Return the immutable, bundled Demo source paths."""

    root = _find_demo_dir()
    document_a = root / "raw" / "sample.source.md"
    document_b = root / "raw" / "sample.target.md"
    for path, name in ((document_a, "文档 A"), (document_b, "文档 B")):
        if not path.is_file():
            raise FileNotFoundError(f"Demo {name} 文件不存在: {path}")
    return document_a, document_b


def create_demo_working_pair(
    work_root: str | Path | None = None,
) -> tuple[Path, Path, Path]:
    """Copy the bundled pair into a fresh, writable Demo workspace.

    A unique directory is used for every call, so saving or explicitly
    overwriting one Demo run can never mutate the bundled examples or affect a
    later run.  The operating system may clean these temporary workspaces.
    """

    source_a, source_b = get_demo_source_paths()
    parent = Path(work_root).resolve() if work_root is not None else None
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    workspace = Path(
        tempfile.mkdtemp(
            prefix="dualign-demo-",
            dir=str(parent) if parent is not None else None,
        )
    )
    document_a = workspace / source_a.name
    document_b = workspace / source_b.name
    shutil.copy2(source_a, document_a)
    shutil.copy2(source_b, document_b)
    return document_a, document_b, workspace


def get_demo_paths() -> tuple[str, str, str]:
    """Return a fresh writable pair for the GUI and standalone Demo."""

    document_a, document_b, _workspace = create_demo_working_pair()
    for path, name in ((document_a, "文档 A"), (document_b, "文档 B")):
        if not path.is_file():
            raise FileNotFoundError(f"Demo {name} 副本创建失败: {path}")
    return str(document_a), str(document_b), f"{LABEL}（临时副本）"
