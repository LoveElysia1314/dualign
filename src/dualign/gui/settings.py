"""
Dualign — GUI 配置持久化管理

集中管理所有用户配置项的读写、序列化/反序列化。
替代原本散布在 DualignWindow 中的 _load_history/_save_history 逻辑。
"""

from __future__ import annotations

import os
import json
from typing import Any, Dict, Optional

# ═══════════════════════════════════════════════════════════════
# 配置键常量
# ═══════════════════════════════════════════════════════════════

# 窗口与布局
KEY_STRATEGY = "strategy"

# 筛选
KEY_SHOW_ALL = "show_all"
KEY_ANOMALY_TYPES = "anomaly_types"
KEY_APPROVAL_STATES = "approval_states"
KEY_LAST_OPEN_DIR = "last_open_dir"

# 显示选项
KEY_CONTEXT_LINES = "context_lines"
KEY_COMPACT_GRID = "compact_grid"
KEY_SHOW_HANDLED = "show_handled"  # AI 建议表「显示已处理」
KEY_CROSS_GROUP_OP = "cross_group_op"  # 筛选跨组逻辑 AND/OR

# 全量枚举（供 default_values 使用）
ALL_ANOMALY_TYPES = [
    "NON_1TO1",
    "MIX",
    "LOW_SCORE",
    "FLAGGED",
]
ALL_APPROVAL_STATES = [
    "none",
    "auto",
    "agent",
    "user",
]

# 质量门控
KEY_QUALITY_GATE = "quality_gate"

# ═══════════════════════════════════════════════════════════════
# DualignConfig — 配置管理单例
# ═══════════════════════════════════════════════════════════════


class DualignConfig:
    """GUI 配置管理器（单例模式）。

    用法:
        cfg = DualignConfig.instance()
        cfg.load()
        context = cfg.get(KEY_CONTEXT_LINES, 1)
        cfg.set(KEY_CONTEXT_LINES, 3)
        cfg.save()
    """

    _instance: Optional["DualignConfig"] = None
    _FILE_NAME = "gui_config.json"

    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._dirty: bool = False
        self._file_path: str = ""

    @classmethod
    def instance(cls) -> "DualignConfig":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 路径 ──

    def _ensure_path(self) -> str:
        if not self._file_path:
            from dualign.config import get_cache_root

            base = get_cache_root()
            os.makedirs(base, exist_ok=True)
            self._file_path = os.path.join(base, self._FILE_NAME)
        return self._file_path

    # ── 读写 ──

    def load(self) -> Dict[str, Any]:
        """从磁盘加载配置。"""
        path = self._ensure_path()
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
        except Exception:
            self._data = {}
        self._dirty = False
        return self._data

    def save(self):
        """保存到磁盘。"""
        if not self._dirty:
            return
        path = self._ensure_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, separators=(",", ":"))
            self._dirty = False
        except Exception:
            pass

    # ── 读写接口 ──

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        self._data[key] = value
        self._dirty = True

    # ── 恢复默认 ──

    @staticmethod
    def default_values() -> dict:
        """返回所有 GUI 选项的出厂默认值（不含模型/Agent 配置）。"""

        return {
            # 筛选与显示
            KEY_SHOW_ALL: True,
            KEY_CONTEXT_LINES: 1,
            KEY_COMPACT_GRID: False,  # False = 显示评分明细
            KEY_SHOW_HANDLED: True,  # AI 建议表默认显示已处理
            KEY_CROSS_GROUP_OP: "AND",
            KEY_ANOMALY_TYPES: list(ALL_ANOMALY_TYPES),
            KEY_APPROVAL_STATES: list(ALL_APPROVAL_STATES),
            # 修复策略
            KEY_STRATEGY: 1,
            # 质量门控
            KEY_QUALITY_GATE: {
                "anchor_density_min": 0.60,
                "gap_row_ratio_max": 0.10,
                "zscore_k": 3.0,
                "zscore_min_score": 0.6,
            },
        }

    def clear_all(self):
        """清除所有配置项（保留模型/Agent 配置键）。"""
        # 保留模型配置相关键名
        preserved = {"active_backend", "agent_config", "model_config"}
        self._data = {k: v for k, v in self._data.items() if k in preserved}
        self._dirty = True


# 注：config_path() 已移除——如需路径，直接使用 DualignConfig.instance()._ensure_path()
