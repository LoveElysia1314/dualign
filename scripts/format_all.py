"""
Dualign — 项目范围批量格式化脚本

用法:
    python scripts/format_all.py            # 格式化全部
    python scripts/format_all.py --check    # 仅检查不合规文件，不改写
    python scripts/format_all.py --verbose  # 显示每条命令的输出

依赖:
    pip install black        # Python 格式化
    npm install -g prettier  # Markdown / JSON / YAML 格式化（可选）
"""

import subprocess
import sys
import os
import shutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(
    cmd_str: str, label: str, check_only: bool, verbose: bool, timeout: int = 120
) -> int:
    """运行格式化命令，返回非零表示有不合规文件。"""
    if check_only:
        cmd_str = cmd_str.replace("--write ", "--check ")
    print(f"  [{label}] {cmd_str}")
    try:
        result = subprocess.run(
            cmd_str,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            shell=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"  [{label}] 超时 ({timeout}s)，已跳过", file=sys.stderr)
        return 1
    if result.returncode != 0 or verbose:
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if out:
            print(out)
        if err and result.returncode != 0:
            print(err, file=sys.stderr)
    return result.returncode


def main():
    check_only = "--check" in sys.argv
    verbose = "--verbose" in sys.argv
    has_errors = 0

    print("=" * 50)
    print("Dualign 批量格式化")
    print(f"  模式: {'仅检查' if check_only else '格式化'}")
    print("=" * 50)

    # ── 1. Python — Black ──
    black_exe = shutil.which("black")
    if black_exe:
        print("\n📐 Python (black)")
        has_errors += run(
            f'"{black_exe}" src/ tests/ scripts/ demo/ main.py --quiet',
            "black",
            check_only,
            verbose,
        )
    else:
        print("\n⚠️  black 未安装，跳过 Python 格式化。")
        print("   安装: pip install black")

    # ── 2. Markdown / JSON / YAML — Prettier ──
    npx_exe = shutil.which("npx")
    prettier_ok = False
    if npx_exe:
        # 检查 prettier 是否可用（--yes 避免交互式安装提示卡死）
        try:
            r = subprocess.run(
                f'"{npx_exe}" --yes prettier --version',
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode == 0:
                prettier_ok = True
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass
    if prettier_ok:
        print("\n📝 Markdown / JSON / YAML (prettier)")
        has_errors += run(
            f'"{npx_exe}" --yes prettier --write "**/*.md" "**/*.json" "**/*.yaml" "**/*.yml"'
            " --ignore-path .gitignore --no-error-on-unmatched-pattern",
            "prettier",
            check_only,
            verbose,
        )
    else:
        print("\n⚠️  prettier 未安装，跳过文档格式化。")
        print("   安装: npm install -g prettier")

    # ── 汇总 ──
    print()
    if has_errors:
        print(f"❌ 发现 {has_errors} 个不合规文件。")
        if not check_only:
            print("   已重新运行带 --check 参数可确认。")
        sys.exit(1)
    else:
        print("✅ 全部合规。" if check_only else "✅ 格式化完成。")
        sys.exit(0)


if __name__ == "__main__":
    main()
