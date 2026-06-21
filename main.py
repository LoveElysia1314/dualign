#!/usr/bin/env python
"""Dualign — 独立入口

用法:
  python main.py             启动 GUI（默认）
  python main.py --help      查看全部命令
"""

import sys
import os

_self_dir = os.path.dirname(os.path.abspath(__file__))
_src_dir = os.path.join(_self_dir, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from dualign.__main__ import main

if __name__ == "__main__":
    # 无参数时默认启动 GUI
    if len(sys.argv) <= 1:
        sys.argv.append("gui")
    sys.exit(main())
