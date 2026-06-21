"""
模式一：最简 for 循环
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

适用场景：少量文件对（< 10 对），串行执行，不需进度报告。
教学目的：展示 Dualign 的最简集成方式——只需两样东西：
          (1) 文件对列表, (2) for 循环。

前置条件:
  - Ollama 运行中，已拉取 leoipulsar/harrier-0.6b
  - pip install -e . （已安装 dualign）

┌─────────────────────────────────────────────────────────────┐
│  这是教学示例，展示了集成 Dualign 所需的最小代码量。          │
│  它不可直接运行——你需要替换 file_pairs 为自己的实际文件路径。 │
└─────────────────────────────────────────────────────────────┘

数据结构假设:
    file_pairs = [
        ("ch01.source.md", "ch01.target.md"),  # (原文, 译文)
        ("ch02.source.md", "ch02.target.md"),
        ("ch03.source.md", "ch03.target.md"),  # ...
    ]
"""

# ── 1. 唯一的导入 ──
# align_chapter 是对齐→自动修复→导出的原子 API
from dualign.services.cli_pipeline import align_chapter

# ── 2. 准备文件对列表 ──
# 这是消费端需要自行准备的核心数据：原文路径 ↔ 译文路径
# 来源可以是：目录扫描结果、catalog 配置、数据库记录……
file_pairs = [
    ("data/ch01.source.md", "data/ch01.target.md"),
    ("data/ch02.source.md", "data/ch02.target.md"),
    ("data/ch03.source.md", "data/ch03.target.md"),
]

# ── 3. 串行对齐 ──
# align_chapter 的返回值：
#   success: bool    — 是否成功
#   quality: str     — "reliable" | "degraded" | "unreliable"
#   report_path: str — report.json 的路径
#   ops: list        — 对齐操作列表
#   error: str       — 仅失败时有
for src, tgt in file_pairs:
    result = align_chapter(
        src_path=src,
        tgt_path=tgt,
        output_dir="output/repaired/",  # 修复后 .md 的输出目录
        strategy="src",  # src | tgt | minimal
    )

    status = "✓" if result["success"] else "✗"
    print(f"{status} {src} → quality={result.get('quality', '?')}")

# ── 输出产物 ──
# output/repaired/
#   ├── ch01.report.json     # 对齐报告（含异常列表）
#   ├── ch01.source.md       # 修复后原文
#   ├── ch01.target.md       # 修复后译文
#   ├── ch02.report.json     # ...
#   └── ...

# ── 进阶提示 ──
# 1. align_chapter 内部已包含内容级哈希缓存。
#    文件未变化时重复调用自动跳过，不需要额外处理。
# 2. 如果已有对齐产物不想重复运行，可在外部检查 report.json 存在性：
#    import os
#    if os.path.isfile(f"output/repaired/{entry_id}.report.json"):
#        print(f"跳过 {entry_id} — 已对齐")
#        continue
