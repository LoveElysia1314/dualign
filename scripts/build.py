#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Dualign — PyInstaller 打包 & Inno Setup 安装程序制作脚本
=====================================================

用法:
    python scripts/build.py                   # 完整构建：venv → PyInstaller → 安装包
    python scripts/build.py --skip-installer  # 仅 PyInstaller 打包，跳过安装程序
    python scripts/build.py --clean           # 清理所有构建产物后重来

输出:
    dist/dualign/                             PyInstaller 输出（单文件夹，含 exe + 所有依赖）
    Dualign_Setup_v{VERSION}.exe               Inno Setup 安装包
    Dualign_Portable_v{VERSION}.zip            便携版 ZIP（解压即可运行，无需安装）
    Dualign_Setup_v{VERSION}.zip               安装包 ZIP（用于 GitHub Releases 分发）

依赖:
    - Python ≥ 3.10
    - PyInstaller（脚本会自动安装）
    - Inno Setup 6（安装程序制作，需单独安装）
"""

from __future__ import annotations

import os
import sys
import subprocess
import shutil
import zipfile
import argparse
import hashlib
from pathlib import Path
import re
import site

# 禁用用户站点包
site.ENABLE_USER_SITE = False

# 加载 .env 本地配置（必须在引用任何环境变量之前）
# 将项目根目录加入 sys.path，确保 scripts/ 可作为包导入
_project_root_for_env = Path(__file__).resolve().parent.parent
if str(_project_root_for_env) not in sys.path:
    sys.path.insert(0, str(_project_root_for_env))
from scripts.env_loader import load_env as _load_env

_load_env()

# ═══════════════════════════════════════════════════════════════
# 项目常量
# ═══════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
# 从 pyproject.toml 动态读取版本号，与项目保持一致
_pyproject_text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
_match = re.search(r'^version\s*=\s*"([^"]+)"', _pyproject_text, re.MULTILINE)
VERSION = _match.group(1) if _match else "0.0.0"
APP_NAME = "Dualign"
APP_EXE_NAME = "dualign.exe"
ENTRY_POINT = str(PROJECT_ROOT / "main.py")

# 嵌入模型提示词文件（AI 审校功能依赖）
AGENT_PROMPT_SRC = SRC_DIR / "dualign" / "services" / "prompts" / "agent-prompt.md"
AGENT_PROMPT_DST = "dualign/services/prompts"

# Demo 示例数据
DEMO_SRC = PROJECT_ROOT / "demo"
DEMO_DST = "demo"

# 文档文件夹
DOCS_SRC = PROJECT_ROOT / "docs"
DOCS_DST = "docs"


# ── 构建哈希缓存 ──
# 将 .dualign_build_hash 存储在 dist/ 目录旁，标记 PyInstaller 产物的输入指纹
BUILD_HASH_FILE = "dist/.dualign_build_hash"


def _collect_build_input_files() -> list[Path]:
    """收集所有影响 PyInstaller 产物内容的源文件列表。"""
    files: list[Path] = [
        PROJECT_ROOT / "main.py",
        PROJECT_ROOT / "pyproject.toml",
        Path(__file__).resolve(),  # build.py 自身（spec 从中生成）
        PROJECT_ROOT / "scripts" / "build_exe.py",
        PROJECT_ROOT / "scripts" / "setup.template.iss",
    ]
    # src/ 下所有文件
    if SRC_DIR.is_dir():
        for p in SRC_DIR.rglob("*"):
            if p.is_file():
                files.append(p)
    # 数据文件
    if AGENT_PROMPT_SRC.exists():
        files.append(AGENT_PROMPT_SRC)
    if DEMO_SRC.is_dir():
        for p in DEMO_SRC.rglob("*"):
            if p.is_file():
                files.append(p)
    if DOCS_SRC.is_dir():
        for p in DOCS_SRC.rglob("*"):
            if p.is_file():
                files.append(p)
    # 品牌资产
    branding_dir = PROJECT_ROOT / "assets" / "branding"
    if branding_dir.is_dir():
        for p in branding_dir.rglob("*"):
            if p.is_file():
                files.append(p)
    # 运行时资源
    res_dir = SRC_DIR / "dualign" / "resources"
    if res_dir.is_dir():
        for p in res_dir.rglob("*"):
            if p.is_file():
                files.append(p)
    return files


def _compute_build_hash() -> str:
    """计算当前源码树的 SHA256 指纹。"""
    h = hashlib.sha256()
    for fp in sorted(_collect_build_input_files(), key=lambda x: str(x.resolve())):
        rel = fp.relative_to(PROJECT_ROOT)
        h.update(str(rel).encode("utf-8"))
        try:
            h.update(fp.read_bytes())
        except Exception:
            pass
    return h.hexdigest()


def _is_build_cached() -> bool:
    """检查 dist/ 是否存在且与当前源码哈希匹配。"""
    hash_path = PROJECT_ROOT / BUILD_HASH_FILE
    if not hash_path.is_file():
        return False
    out_dir = PROJECT_ROOT / "dist" / "dualign"
    if not out_dir.is_dir():
        return False
    if not (out_dir / APP_EXE_NAME).is_file():
        return False
    try:
        cached = hash_path.read_text(encoding="utf-8").strip()
        return cached == _compute_build_hash()
    except Exception:
        return False


def _save_build_hash():
    """保存当前源码哈希到 dist/。"""
    h = _compute_build_hash()
    hash_path = PROJECT_ROOT / BUILD_HASH_FILE
    hash_path.parent.mkdir(parents=True, exist_ok=True)
    hash_path.write_text(h, encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# BuildConfig
# ═══════════════════════════════════════════════════════════════


class BuildConfig:
    """构建配置（单文件夹模式打包）。"""

    def __init__(self):
        self.project_root = PROJECT_ROOT
        self.script_dir = Path(__file__).resolve().parent
        self.venv_dir = self.project_root / "venv"
        self.output_dir = self.project_root / "dist"
        self.app_dir = self.output_dir
        self.build_dir = self.project_root / "build_pyinstaller"
        self.spec_path = self.project_root / "dualign.spec"

        # 虚拟环境路径
        bin_dir = "Scripts" if sys.platform == "win32" else "bin"
        self.venv_bin_dir = self.venv_dir / bin_dir
        self.python_exe = self.venv_bin_dir / "python"
        self.pip_exe = self.venv_bin_dir / "pip"

        if sys.platform == "win32":
            self.python_exe = self.python_exe.with_suffix(".exe")
            self.pip_exe = self.pip_exe.with_suffix(".exe")


# ═══════════════════════════════════════════════════════════════
# 1. 虚拟环境
# ═══════════════════════════════════════════════════════════════


def setup_virtual_environment(config: BuildConfig):
    """创建并配置虚拟环境。"""
    if not config.venv_dir.exists():
        print(f"[1/7] 创建虚拟环境: {config.venv_dir}")
        subprocess.run(
            [sys.executable, "-m", "venv", str(config.venv_dir)],
            check=True,
        )

    if not config.python_exe.exists():
        sys.exit(f"错误：未找到 Python: {config.python_exe}")
    if not config.pip_exe.exists():
        sys.exit(f"错误：未找到 Pip: {config.pip_exe}")

    print(f"[1/7] 虚拟环境就绪: {config.venv_dir}")


# ═══════════════════════════════════════════════════════════════
# 2. 依赖安装
# ═══════════════════════════════════════════════════════════════


def install_dependencies(config: BuildConfig):
    """安装 Dualign 全部依赖（GUI 已包含在核心依赖中）。"""
    print("[2/7] 升级 pip...")
    subprocess.run(
        [str(config.python_exe), "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
    )

    print("[2/7] 安装项目依赖（基于本地仓库版本）...")
    # 用非可编辑安装（不带 -e），确保 PyInstaller 打包的是 site-packages
    # 中的实际拷贝而非指向源码目录的符号链接。显式传递项目根目录路径。
    subprocess.run(
        [
            str(config.pip_exe),
            "install",
            str(config.project_root),
        ],
        check=True,
    )

    # 确保 PyInstaller 已安装
    print("[2/7] 安装 PyInstaller...")
    subprocess.run(
        [str(config.pip_exe), "install", "pyinstaller"],
        check=True,
    )


# ═══════════════════════════════════════════════════════════════
# 3. 清理缓存
# ═══════════════════════════════════════════════════════════════


def clean_build_cache(config: BuildConfig):
    """清理旧的构建产物。"""
    print("[3/7] 清理旧构建缓存...")
    for p in [
        config.project_root / "__pycache__",
        config.project_root / "build",
        config.project_root / "dist",
        config.project_root / "build_pyinstaller",
        config.project_root / "dualign.spec",
    ]:
        if p.exists():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
# 4-5. 执行 PyInstaller 构建（委托 build_exe.py）
# ═══════════════════════════════════════════════════════════════


def run_pyinstaller_build(config: BuildConfig):
    """执行 PyInstaller 打包 — 委托给 build_exe.py，避免重复维护 spec 逻辑。

    支持缓存：源码未变时跳过。
    """
    if _is_build_cached():
        print("[4-5/7] 源码未变动，跳过 PyInstaller 打包（使用缓存）")
        return

    print("[4-5/7] 执行 PyInstaller 打包（委托 build_exe.py）...")

    build_exe = str(PROJECT_ROOT / "scripts" / "build_exe.py")
    cmd = [
        sys.executable,
        build_exe,
        "--outdir",
        str(config.output_dir / "dualign"),
        "--clean",
    ]

    try:
        subprocess.run(cmd, check=True, cwd=str(config.project_root))
        print("[4-5/7] PyInstaller 打包成功")
        _save_build_hash()
    except subprocess.CalledProcessError as e:
        print(f"[4-5/7] PyInstaller 打包失败 (exit code {e.returncode})")
        raise


# ═══════════════════════════════════════════════════════════════
# 6. 复制资源文件
# ═══════════════════════════════════════════════════════════════


def copy_resources(config: BuildConfig):
    """单文件夹模式下资源已由 COLLECT 打包，无需额外复制。"""
    print("[6/7] 单文件夹模式，资源已由 COLLECT 打包，跳过复制")


# ═══════════════════════════════════════════════════════════════
# 7. 生成 Inno Setup 安装脚本
# ═══════════════════════════════════════════════════════════════


def find_inno_compiler() -> Path | None:
    """查找 Inno Setup 编译器 ISCC.exe。

    优先级:
      1. ISCC_PATH 环境变量（来自 .env 或系统环境）
      2. 常见安装路径
      3. where 命令（系统 PATH 中查找）
    """
    # 1. 环境变量（支持 .env 配置）
    env_path = os.environ.get("ISCC_PATH", "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
        print(f"  [提示] ISCC_PATH 指向的文件不存在: {env_path}")

    # 2. 常见安装路径
    common_paths = [
        "C:/Program Files (x86)/Inno Setup 6/ISCC.exe",
        "C:/Program Files/Inno Setup 6/ISCC.exe",
    ]
    for path in common_paths:
        if os.path.exists(path):
            return Path(path)

    # 3. 系统 PATH 中查找
    try:
        result = subprocess.run(
            ["where", "ISCC.exe"], capture_output=True, text=True, shell=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip().split("\n")[0])
    except Exception:
        pass

    return None


def generate_iss_file(config: BuildConfig) -> Path:
    """从模板填充生成 Inno Setup .iss 脚本。

    读取 scripts/setup.template.iss，替换 @PLACEHOLDER@ 后写出临时文件。
    使用相对路径 `..\\dist\\dualign` 指向 PyInstaller 输出，不含任何本机路径。
    """

    print("[7/7] 生成 Inno Setup 安装脚本...")

    # 先验证构建产物是否存在
    exe_path = config.project_root / "dist" / "dualign" / APP_EXE_NAME
    if not exe_path.exists():
        raise FileNotFoundError(f"未找到可执行文件: {exe_path}")

    # 读取模板
    template_path = config.script_dir / "setup.template.iss"
    if not template_path.is_file():
        raise FileNotFoundError(
            f"找不到 ISS 模板文件: {template_path}\n"
            f"请确保 scripts/setup.template.iss 存在。"
        )
    iss_content = template_path.read_text(encoding="utf-8")

    # 填充占位符（@FORMAT@ 避免与 Inno Setup 的 {…} / Python f-string 冲突）
    # @APP_DIR_RELATIVE@ 是 scripts/ → dist/dualign/ 的相对路径
    app_dir_relative = os.path.join("..", "dist", "dualign")
    substitutions = {
        "@APP_NAME@": APP_NAME,
        "@APP_VERSION@": VERSION,
        "@APP_EXE_NAME@": APP_EXE_NAME,
        "@APP_DIR_RELATIVE@": app_dir_relative,
    }
    for placeholder, value in substitutions.items():
        iss_content = iss_content.replace(placeholder, value)

    # 写出临时 .iss 文件（编译后清理）
    iss_file = config.script_dir / "setup.iss"
    iss_file.write_text(iss_content, encoding="utf-8")

    return iss_file


def build_installer(config: BuildConfig):
    """使用 Inno Setup 编译安装包。"""
    if not config.app_dir.exists():
        print("[7/7] 错误：构建输出目录不存在，请先执行 PyInstaller 打包")
        return False

    inno_compiler = find_inno_compiler()
    if not inno_compiler:
        print("[7/7] 错误：未找到 Inno Setup 编译器 (ISCC.exe)")
        print("       请安装 Inno Setup 6: https://jrsoftware.org/isinfo.php")
        print("       或使用 --skip-installer 跳过安装程序制作")
        return False

    print(f"[7/7] 找到 Inno Setup: {inno_compiler}")

    iss_file = generate_iss_file(config)

    try:
        output_dir = config.script_dir / "Output"
        output_dir.mkdir(exist_ok=True)

        print("[7/7] 正在编译安装包...")
        result = subprocess.run(
            [str(inno_compiler), str(iss_file)],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(config.script_dir),
            encoding="utf-8",
        )

        # 清理临时 ISS 文件
        try:
            iss_file.unlink()
        except Exception:
            pass

        # 移动输出文件到项目根目录
        setup_name = f"Dualign_Setup_v{VERSION}.exe"
        setup_src = output_dir / setup_name
        if setup_src.exists():
            setup_dst = config.project_root / setup_name
            shutil.move(str(setup_src), str(setup_dst))
            print(f"[7/7] 安装包已生成: {setup_dst}")

            # 清理 Output 目录
            shutil.rmtree(output_dir, ignore_errors=True)

            # 生成 ZIP 包
            create_zip_package(config, setup_name)

            return True
        else:
            print(f"[7/7] 警告：未找到生成的安装包 {setup_name}")
            return False

    except subprocess.CalledProcessError as e:
        print(f"[7/7] Inno Setup 编译失败 (exit code {e.returncode})")
        if e.stdout:
            print(e.stdout[-2000:])
        if e.stderr:
            print(e.stderr[-2000:])
        return False


# ═══════════════════════════════════════════════════════════════
# 8. ZIP 打包（安装包 + 便携版）
# ═══════════════════════════════════════════════════════════════


def create_zip_package(config: BuildConfig, setup_filename: str):
    """创建便携版 ZIP 包和安装包 ZIP。"""
    # ── 便携版：dist/dualign/ 文件夹（免安装，解压即用）──
    app_dir = config.app_dir / "dualign"  # dist/dualign/
    if app_dir.is_dir():
        port_zip_name = f"Dualign_Portable_v{VERSION}.zip"
        port_zip_path = config.project_root / port_zip_name
        port_zip_path.unlink(missing_ok=True)
        total = sum(1 for _ in app_dir.rglob("*") if _.is_file())
        with zipfile.ZipFile(port_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, fp in enumerate(app_dir.rglob("*")):
                if fp.is_file():
                    arcname = str(fp.relative_to(app_dir.parent))
                    zf.write(fp, arcname)
                    if i % 50 == 0:
                        print(f"\r    [{i}/{total}] 压缩中...", end="", flush=True)
        size_mb = port_zip_path.stat().st_size / (1024 * 1024)
        print(f"\n    ✅ 便携版 ZIP: {port_zip_name} ({size_mb:.1f} MB)")

    # ── 安装包 ZIP ──
    zip_name = f"Dualign_Setup_v{VERSION}.zip"
    zip_path = config.project_root / zip_name
    zip_path.unlink(missing_ok=True)

    setup_path = config.project_root / setup_filename
    if setup_path.exists():
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(setup_path, setup_filename)
        size_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f"    ✅ 安装包 ZIP: {zip_name} ({size_mb:.1f} MB)")


# ═══════════════════════════════════════════════════════════════
# 9. 收尾清理
# ═══════════════════════════════════════════════════════════════


def cleanup_temp_directories(config: BuildConfig):
    """清理临时构建文件夹。"""
    temp_dirs = [
        config.build_dir,  # build_pyinstaller
        config.project_root / "build",  # PyInstaller 工作目录
        config.script_dir / "Output",  # Inno Setup 输出目录
    ]
    temp_files = [
        config.project_root / "dualign.spec",  # PyInstaller spec 文件
    ]

    for p in temp_dirs:
        if p.exists():
            try:
                shutil.rmtree(p)
            except Exception:
                pass

    for p in temp_files:
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# 10. 主流程
# ═══════════════════════════════════════════════════════════════


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dualign PyInstaller 打包 & Inno Setup 安装程序制作",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="强制清理所有构建产物后重来",
    )
    parser.add_argument(
        "--skip-installer",
        action="store_true",
        help="跳过 Inno Setup 安装程序制作",
    )
    parser.add_argument(
        "--skip-venv",
        action="store_true",
        help="跳过虚拟环境创建（使用当前 Python 环境）",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = BuildConfig()

    # ── 步骤执行 ──
    try:
        if args.clean:
            clean_build_cache(config)

        if not args.skip_venv:
            setup_virtual_environment(config)
            install_dependencies(config)
        else:
            print("[跳过] 虚拟环境设置与依赖安装")

        # 清理 PyInstaller 工作目录
        if config.project_root / "build" in [
            Path(p) for p in [config.project_root / "build"]
        ]:
            shutil.rmtree(config.project_root / "build", ignore_errors=True)

        run_pyinstaller_build(config)
        copy_resources(config)

        if not args.skip_installer:
            build_installer(config)
        else:
            print("[跳过] Inno Setup 安装程序制作")

        cleanup_temp_directories(config)

        # ── 完成 ──
        print()
        print("=" * 60)
        print(f"  ✅ 构建成功 — {APP_NAME} v{VERSION}")
        print("=" * 60)

        exe_path = config.output_dir / "dualign" / APP_EXE_NAME
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print(f"  可执行文件: {exe_path} ({size_mb:.1f} MB)")
        else:
            print(f"  输出目录:   {config.output_dir / 'dualign'}")

        setup_path = config.project_root / f"Dualign_Setup_v{VERSION}.exe"
        if setup_path.exists():
            size_mb = setup_path.stat().st_size / (1024 * 1024)
            print(f"  安装包:     {setup_path} ({size_mb:.1f} MB)")

        print()
        print("  📦 使用说明:")
        print(f"     1. 确保已安装并启动 Ollama (默认 http://localhost:11434)")
        print(f"     2. 已拉取嵌入模型: ollama pull leoipulsar/harrier-0.6b")
        print(f"     3. 启动 GUI: dualign.exe gui")
        print(f"     4. 命令行对齐: dualign.exe align --src A.md --tgt B.md")
        print(f"     5. 如需 AI 审校: set DEEPSEEK_API_KEY=sk-...")
        print()

    except Exception as e:
        print(f"\n❌ 构建失败: {e}")
        import traceback

        traceback.print_exc()

        # 尝试清理
        try:
            cleanup_temp_directories(config)
        except Exception:
            pass

        sys.exit(1)


if __name__ == "__main__":
    main()
