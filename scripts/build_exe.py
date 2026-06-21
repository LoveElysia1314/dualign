#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Dualign GUI 独立打包脚本 (PyInstaller)
=========================================

用法:
    # 安装打包依赖
    pip install pyinstaller

    # 打包为单目录（推荐，启动更快）
    python scripts/build_exe.py

    # 打包为单文件
    python scripts/build_exe.py --onefile

    # 指定输出目录
    python scripts/build_exe.py --outdir dist/dualign

输出:
    默认输出到 dist/dualign/，包含 dualign.exe 及所有依赖。

注意事项:
    - 本脚本默认打包 GUI 模式，入口为 dualign/__main__.py 的 gui 子命令
    - 打包后需配合 Ollama 服务使用（需单独安装）
    - AI 审校功能需在运行时设置 DEEPSEEK_API_KEY 环境变量
"""

from __future__ import annotations

import os
import sys
import shutil
import argparse
import subprocess
import platform
from pathlib import Path

# ── 项目根目录 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
DEMO_DIR = PROJECT_ROOT / "demo"
DIST_DIR = PROJECT_ROOT / "dist"
SPEC_NAME = "dualign.spec"

# ── 品牌资源 ──
BRANDING_DIR = PROJECT_ROOT / "assets" / "branding"
APP_ICO = BRANDING_DIR / "dualign.ico"
RESOURCE_DIR = SRC_DIR / "dualign" / "resources"

# ── 默认入口（指向 dualign 包的 __main__.py 的 main_gui 函数） ──
# 打包后命令行用法: dualign.exe gui [--src A.md --tgt B.md]
ENTRY_POINT = str(SRC_DIR / "dualign" / "__main__.py")


def check_dependencies() -> list[str]:
    """检查打包所需依赖，返回缺失的包列表。"""
    missing = []
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        missing.append("pyinstaller")
    try:
        import PySide6  # noqa: F401
    except ImportError:
        missing.append("PySide6")
    return missing


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dualign GUI 打包脚本 (PyInstaller，默认单目录)",
    )
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="打包为单文件（启动较慢，不推荐）",
    )
    parser.add_argument(
        "--outdir",
        default=str(DIST_DIR / "dualign"),
        help="输出目录（默认: dist/dualign）",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="打包 console 版本（显示控制台窗口，方便调试）",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="先清理旧的 build/dist 目录",
    )
    return parser.parse_args()


def build_spec_content(
    outdir: str,
    onefile: bool,
    console: bool,
) -> str:
    """生成 PyInstaller .spec 文件内容。"""
    # 收集数据文件：demo 目录（含 raw 示例数据）、docs 目录、AI 提示词
    # 相对于 PROJECT_ROOT 的路径
    demo_data_src = "demo"
    demo_data_dst = "demo"
    docs_data_src = "docs"
    docs_data_dst = "docs"
    # AI 审校代理所需的 tools.json 和 agent-prompt.md
    prompts_src = "src/dualign/services/prompts"
    prompts_dst = "dualign/services/prompts"

    # ── 隐式导入列表（PyInstaller 可能遗漏的动态导入） ──
    # dualign/common.py 中的 numpy、mdformat、json 是懒加载的
    # dualign/providers.py 中的 cryptography、requests 是懒加载的
    # dualign/services/ai_repair_agent.py 中的 openai 是懒加载的
    # 这些都可能被 PyInstaller 漏掉，显式声明确保包含
    hidden_imports = [
        # PySide6 子模块
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        # dualign 内部模块
        "dualign",
        "dualign.common",
        "dualign.providers",
        "dualign.core",
        "dualign.core.aligner",
        "dualign.core.punctuation",
        "dualign.core.file_pair_matcher",
        "dualign.models",
        "dualign.models.state",
        "dualign.models.action",
        "dualign.models.report",
        "dualign.models.snap_state",
        "dualign.services",
        "dualign.services.repair",
        "dualign.services.ai_repair_agent",
        "dualign.services.embedding",
        "dualign.gui",
        "dualign.gui.window",
        "dualign.gui.window_table",
        "dualign.gui.base_table",
        "dualign.gui.review",
        "dualign.gui.filter",
        "dualign.gui.dialogs",
        "dualign.gui.panels",
        "dualign.gui.snap_indicator",
        "dualign.gui.preview_table",
        # 懒加载的第三方库
        "numpy",
        "requests",
    ]

    excludes = [
        # 移除开发/测试相关的包
        "pytest",
        "tkinter",
        "test",
        # 移除不需要的 PySide6 模块（节省体积）
        "PySide6.QtBluetooth",
        "PySide6.QtNetwork",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtRemoteObjects",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtSql",
        "PySide6.QtSvg",
        "PySide6.QtTest",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngine",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebSockets",
        "PySide6.QtXml",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
        "PySide6.QtPrintSupport",
        "PySide6.QtDataVisualization",
        "PySide6.QtCharts",
        "PySide6.QtGraphs",
    ]

    mode = "onedir" if not onefile else "onefile"
    console_flag = "True" if console else "False"

    return f"""# -*- mode: python ; coding: utf-8 -*-
# Dualign GUI 打包配置 — 由 build_exe.py 自动生成

a = Analysis(
    ['{ENTRY_POINT}'],
    pathex=[],
    binaries=[],
    datas=[
        ('{demo_data_src}', '{demo_data_dst}'),
        ('{docs_data_src}', '{docs_data_dst}'),
        ('{prompts_src}', '{prompts_dst}'),
        ('assets/branding', 'assets/branding'),
        ('src/dualign/resources', 'dualign/resources'),
    ],
    hiddenimports={hidden_imports!r},
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes={excludes!r},
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='dualign',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console={console_flag},
    disable_windowed_tracker=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/branding/dualign.ico',
)

if '{mode}' == 'onedir':
    # 单目录模式额外生成 COLLECT
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='dualign',
    )
"""


def main():
    args = parse_args()

    # ── 1. 检查依赖 ──
    missing = check_dependencies()
    if missing:
        print(f"❌ 缺少必要依赖: {', '.join(missing)}")
        print(f"   请运行: pip install {' '.join(missing)}")
        sys.exit(1)

    # ── 2. 打印配置信息后直接执行 ──
    print(f"🔨 Dualign GUI 打包工具")
    print(f"   项目目录: {PROJECT_ROOT}")
    print(f"   入口文件: {ENTRY_POINT}")
    print(f"   输出目录: {args.outdir}")
    print(f"   打包模式: {'单文件' if args.onefile else '单目录'}")
    print(f"   控制台: {'显示' if args.debug else '隐藏'}")
    print()

    # ── 3. 清理 ──
    if args.clean:
        build_dir = PROJECT_ROOT / "build"
        spec_file = PROJECT_ROOT / SPEC_NAME
        for p in [build_dir, Path(args.outdir), spec_file]:
            if p.exists():
                print(f"   清理: {p}")
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()

    # ── 4. 生成 .spec 文件 ──
    spec_content = build_spec_content(
        outdir=args.outdir,
        onefile=args.onefile,
        console=args.debug,
    )
    spec_path = PROJECT_ROOT / SPEC_NAME
    spec_path.write_text(spec_content, encoding="utf-8")
    print(f"✅ 已生成: {spec_path}")

    # ── 5. 执行 PyInstaller ──
    print(f"🚀 正在打包 (PyInstaller {mode_str(args)})...")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(spec_path),
        "--distpath",
        str(Path(args.outdir).parent),
        "--workpath",
        str(PROJECT_ROOT / "build"),
    ]
    if args.clean:
        cmd.append("--noconfirm")

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print(f"❌ 打包失败 (exit code {result.returncode})")
        sys.exit(1)

    # ── 6. 完成 ──
    out = Path(args.outdir)
    exe_path = out / "dualign.exe"
    size_str = ""
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        size_str = f" ({size_mb:.1f} MB)"

    print(f"✅ 打包成功!")
    print(f"   输出目录: {out.resolve()}")
    print(f"   可执行文件: {out / 'dualign.exe'}")
    print(f"   内含 Demo 数据: {out / 'demo' / 'raw'}")
    print()
    print("📦 使用说明:")
    print(f"   1. 确保已安装并启动 Ollama (默认 http://localhost:11434)")
    print(f"   2. 已拉取嵌入模型: ollama pull leoipulsar/harrier-0.6b")
    print(f"   3. 运行: .\\dualign.exe       (默认启动 GUI)")
    print(f"   4. 或运行: .\\dualign.exe gui")
    print(f"   5. 如需 AI 审校: set DEEPSEEK_API_KEY=your_key && .\\dualign.exe gui")
    print()
    print("💡 提示: 将输出目录添加到 PATH 后可直接在终端调用 dualign")


def mode_str(args: argparse.Namespace) -> str:
    return "单文件" if args.onefile else "单目录"


if __name__ == "__main__":
    main()
