"""
Dualign v0.7.0 — Demo 演示文件

用法:
  python -m demo.demo_cli              # 对齐 + 自动修复管线
  python -m demo.demo_gui              # 交互式 GUI 演示
  python -m demo.demo_ai_repaired      # AI 审校代理演示
"""

import sys
from pathlib import Path

# 确保项目根目录和 src/ 在 sys.path 中
_THIS = Path(__file__).resolve().parent
_ROOT = _THIS.parent
_SRC_DIR = _ROOT / "src"

for p in [str(_ROOT), str(_SRC_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

DEMO_DIR = _THIS
RAW_DIR = _THIS / "raw"
SRC_PATH = RAW_DIR / "sample.source.md"
TGT_PATH = RAW_DIR / "sample.target.md"
CACHE_DIR = _THIS / "cache"
OUTPUT_DIR = _THIS / "output"
