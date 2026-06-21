"""
Dualign — 全局配置与缓存路径

常量定义、环境变量解析、缓存目录管理。
"""

from __future__ import annotations

import os
import json
from typing import Optional

# ═══════════════════════════════════════════════════════════════
# 1. 全局常量
# ═══════════════════════════════════════════════════════════════

_DEFAULT_OLLAMA_MODEL = "leoipulsar/harrier-0.6b"

MODEL_NAME = os.environ.get("DUALIGN_MODEL", f"ollama:{_DEFAULT_OLLAMA_MODEL}")

_DEFAULT_INSTRUCTION = "Instruct: Identify parallel sentences across languages\nQuery: "

INSTRUCTION_TEXT = os.environ.get("DUALIGN_INSTRUCTION", _DEFAULT_INSTRUCTION)

REPORT_FORMAT_VERSION = 1

APP_DATA_DIR = os.path.join(os.path.expanduser("~"), ".dualign")

# ── 统一缓存根目录 ──
DUALIGN_CACHE_ROOT: Optional[str] = None
DUALIGN_CACHE_DIR_ENV = "DUALIGN_CACHE_DIR"


def _default_cache_root() -> str:
    """返回操作系统标准的缓存目录。"""
    import platform as _platform

    system = _platform.system()
    if system == "Windows":
        local_app_data = os.environ.get(
            "LOCALAPPDATA",
            os.path.join(os.path.expanduser("~"), "AppData", "Local"),
        )
        return os.path.join(local_app_data, "dualign", "cache")
    elif system == "Darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Caches", "dualign")
    else:
        xdg = os.environ.get(
            "XDG_CACHE_HOME",
            os.path.join(os.path.expanduser("~"), ".cache"),
        )
        return os.path.join(xdg, "dualign")


def get_cache_root() -> str:
    """解析生效的缓存根目录。"""
    if DUALIGN_CACHE_ROOT is not None:
        return DUALIGN_CACHE_ROOT
    env_cache = os.environ.get(DUALIGN_CACHE_DIR_ENV)
    if env_cache:
        return env_cache
    return _default_cache_root()


def get_embedding_cache_dir(entry_id: str = "") -> str:
    """返回该章节的嵌入缓存目录（统一缓存根下按 entry_id 分目录）。"""
    root = get_cache_root()
    cache_dir = os.path.join(root, "emb")
    if entry_id:
        cache_dir = os.path.join(cache_dir, entry_id)
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _ui_session_cache_path(entry_id: str = "") -> str:
    """返回 UI 状态缓存路径。"""
    root = get_cache_root()
    os.makedirs(os.path.join(root, "session"), exist_ok=True)
    return os.path.join(root, "session", f"{entry_id}.json")


def get_report_cache_dir() -> str:
    """返回报告缓存根目录。"""
    _override = os.environ.get("DUALIGN_REPORT_DIR", "")
    if _override:
        return _override
    _cfg_path = os.path.join(APP_DATA_DIR, "config.json")
    if os.path.isfile(_cfg_path):
        try:
            with open(_cfg_path, encoding="utf-8") as _f:
                _cfg = json.load(_f)
            _custom = _cfg.get("report_dir", "")
            if _custom:
                return _custom
        except Exception:
            pass
    return os.path.join(get_cache_root(), "reports")


def repair_session_path(entry_id: str, repaired_dir: str = "") -> str:
    """返回修复报告/会话统一文件路径。"""
    if not repaired_dir:
        repaired_dir = get_report_cache_dir()
    os.makedirs(repaired_dir, exist_ok=True)
    return os.path.join(repaired_dir, f"{entry_id}.report.json")
