"""
Dualign — 轻量级 .env 加载器

从项目根目录的 .env 文件中读取环境变量配置。
设计原则：
  - 零外部依赖（纯标准库实现）
  - 支持 # 注释
  - 支持 KEY=VALUE 和 KEY="VALUE" 两种格式
  - 已存在的环境变量不会被覆盖（可用 os.environ 预设）

用法:
    from scripts.env_loader import load_env
    load_env()
    iscc = os.environ.get("ISCC_PATH", "")
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def load_env(env_path: str | None = None) -> None:
    """加载 .env 文件到 os.environ（不覆盖已有变量）。

    Args:
        env_path: .env 文件路径。默认为项目根目录下的 .env。
    """
    if env_path is None:
        # 定位到项目根目录（本文件在 scripts/ 下）
        env_path = str(Path(__file__).resolve().parent.parent / ".env")

    env_file = Path(env_path)
    if not env_file.is_file():
        return  # .env 不存在时静默跳过

    # KEY=VALUE 或 KEY="VALUE"
    pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$")

    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue
            m = pattern.match(line)
            if m:
                key = m.group(1)
                value = m.group(2)
                # 去掉可选的外层引号
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                # 不覆盖已有环境变量
                if key not in os.environ:
                    os.environ[key] = value
