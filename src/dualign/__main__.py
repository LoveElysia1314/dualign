"""
Dualign — CLI 入口

用法:
  python -m dualign [-h]
  python -m dualign gui [--src A.md --tgt B.md]
  python -m dualign align --src A.md --tgt B.md [--out DIR] [--strategy src|tgt|minimal]
  python -m dualign auto --src A.md --tgt B.md --out DIR [--strategy src|tgt|minimal]
  python -m dualign refresh --report A.report.json [-k 2.5] [-o B.report.json]

alias 快捷命令:
  dualign -s 源.md -t 目标.md               对齐+自动修复+导出
"""

from __future__ import annotations

import sys
import os
import argparse
from pathlib import Path


def main_gui(src_path: str = "", tgt_path: str = ""):
    """启动 GUI。"""
    # ── Windows: 标记独立 AppUserModelID，确保任务栏显示自定义图标 ──
    if sys.platform == "win32":
        try:
            import ctypes as _ctypes

            _ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Dualign.DualignStudio.v1"
            )
        except Exception:
            pass  # 非致命：退回到 python.exe 默认图标

    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ── 设置应用图标 ──
    from dualign.resources import load_app_icon

    _icon = load_app_icon()
    if _icon is not None:
        app.setWindowIcon(_icon)

    from dualign.gui.theme import T

    T.apply_to_app(app)

    from dualign.gui.window import DualignWindow

    # ── 全局未捕获异常钩子 ──
    def _global_exception_hook(exc_type, exc_value, exc_tb):
        import traceback as _tb

        tb_str = "".join(_tb.format_exception(exc_type, exc_value, exc_tb))
        print(f"\n{'='*60}", file=sys.stderr)
        print("[全局异常钩子] Qt 事件循环中未捕获的异常:", file=sys.stderr)
        print(tb_str, file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)
        QMessageBox.critical(
            None,
            "未捕获的异常",
            f"{exc_type.__name__}: {exc_value}\n\n完整 traceback 已输出到终端。",
        )

    sys.excepthook = _global_exception_hook

    window = DualignWindow()

    if src_path and tgt_path:
        window.load_file_pair(src_path, tgt_path, label=os.path.basename(src_path))

    window.show()
    sys.exit(app.exec())


def main_align(src_path: str, tgt_path: str, out_dir: str = "", strategy: str = "src"):
    """CLI 对齐 + 自动修复 + 导出。"""
    if not os.path.isfile(src_path):
        print(f"错误: 源文件不存在: {src_path}")
        return 1
    if not os.path.isfile(tgt_path):
        print(f"错误: 目标文件不存在: {tgt_path}")
        return 1

    try:
        from dualign.services.cli_pipeline import align_chapter
        from dualign.core import AlignConfig

        print(f"源文档: {src_path}")
        print(f"目标文档: {tgt_path}")
        print(f"修复策略: {strategy}")

        repaired_dir = out_dir or ""
        output_dir = out_dir or os.getcwd()

        config = AlignConfig()
        result = align_chapter(
            src_path,
            tgt_path,
            repaired_dir,
            config=config,
            strategy=strategy,
            output_dir=output_dir,
        )

        if not result.get("success"):
            print(f"对齐失败: {result.get('error', '未知错误')}")
            return 1

        n_ops = len(result["ops"])
    except RuntimeError as e:
        print(f"\n{'='*50}")
        print(str(e))
        print(f"{'='*50}\n")
        return 1
    except Exception as e:
        print(f"\n❌ 对齐过程中发生错误: {e}")
        import traceback

        traceback.print_exc()
        return 1

    # 直接在输出语句中使用 align_chapter 的返回路径
    src_out = result.get("src_path", "")
    tgt_out = result.get("tgt_path", "")
    report = result.get("report_path", "")

    print("\n✅ 对齐完成")
    if src_out:
        # 统计输出行数
        try:
            n_src = len(open(src_out, encoding="utf-8").read().strip().splitlines())
            n_tgt = len(open(tgt_out, encoding="utf-8").read().strip().splitlines())
        except Exception:
            n_src = n_tgt = 0
        print(f"   输出源文: {src_out} ({n_src} 行)")
        print(f"   输出译文: {tgt_out} ({n_tgt} 行)")
    if report:
        print(f"   报告: {report}")
    print(f"   自动修复 ({strategy}): 已处理 {n_ops} 个文本对")

    return 0


def main():
    from dualign import __version__

    parser = argparse.ArgumentParser(
        prog="dualign",
        description="双语平行文档对齐与辅助校验工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
        "  dualign check                   环境健康检查\n"
        "  dualign models                  列出可用模型\n"
        "  dualign align -s src.md -t tgt.md  对齐+修复+导出\n"
        "  dualign gui                     启动图形界面",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    sub = parser.add_subparsers(dest="command", required=False)

    # ── gui ──
    p_gui = sub.add_parser("gui", help="启动图形界面")
    p_gui.add_argument("--src", default="", help="原文路径")
    p_gui.add_argument("--tgt", default="", help="译文路径")

    # ── align ──
    p_align = sub.add_parser("align", help="对齐 + 自动修复 + 导出")
    p_align.add_argument("-s", "--src", required=True, help="原文路径")
    p_align.add_argument(
        "-t", "--tgt", "--tar", dest="tgt", required=True, help="译文路径"
    )
    p_align.add_argument("-o", "--out", default="", help="输出目录")
    p_align.add_argument(
        "--strategy",
        default="src",
        choices=["src", "tgt", "minimal"],
        help="自动修复策略 (默认: src)",
    )

    # ── promote ──
    p_promote = sub.add_parser(
        "promote",
        help="用修复后的文件置换源文档对（等效确认修复结果）",
    )
    p_promote.add_argument(
        "-s",
        "--src",
        required=True,
        help="原始原文文件路径 (将被覆盖)",
    )
    p_promote.add_argument(
        "-t",
        "--tgt",
        "--tar",
        dest="tgt",
        required=True,
        help="原始译文文件路径 (将被覆盖)",
    )
    p_promote.add_argument(
        "-r",
        "--repaired-dir",
        default="",
        help="repaired 目录路径（默认 {src_parent_dir}/repaired）",
    )
    p_promote.add_argument(
        "--dry-run",
        action="store_true",
        help="仅模拟，不实际执行替换",
    )
    p_promote.add_argument(
        "--strategy",
        default="",
        choices=["", "src", "tgt"],
        help='晋升筛选: ""(无条件) / "src"(仅原文未变) / "tgt"(仅译文未变)',
    )

    # ── check ──
    sub.add_parser("check", help="环境健康检查")

    # ── models ──
    sub.add_parser("models", help="列出可用模型")

    args = parser.parse_args()

    # 快捷模式: dualign -s A.md -t B.md (无子命令但提供了 -s -t)
    if not args.command and hasattr(args, "src") and args.src and args.tgt:
        return main_align(args.src, args.tgt, args.out, args.strategy)

    if args.command == "gui":
        main_gui(src_path=args.src, tgt_path=args.tgt)
    elif args.command == "align":
        return main_align(args.src, args.tgt, args.out, args.strategy)
    elif args.command == "promote":
        return _cmd_promote(
            args.src, args.tgt, args.repaired_dir, args.dry_run, args.strategy
        )
    elif args.command == "check":
        return _cmd_check()
    elif args.command == "models":
        return _cmd_models()
    elif args.command is None:
        # ⭐ 无任何参数 → 默认启动 GUI（双击 exe 的预期行为）
        main_gui()
    else:
        parser.print_help()

    return 0


def _cmd_promote(
    src_path: str, tgt_path: str, repaired_dir: str, dry_run: bool, strategy: str = ""
):
    """用修复后的文件置换源文档对。"""
    from dualign.common import promote_repaired

    src_path = os.path.normpath(src_path)
    tgt_path = os.path.normpath(tgt_path)

    if not os.path.isfile(src_path):
        print(f"错误: 源文件不存在: {src_path}")
        return 1
    if not os.path.isfile(tgt_path):
        print(f"错误: 目标文件不存在: {tgt_path}")
        return 1

    # ── 推导 entry_id ──
    src_name = Path(src_path).name
    entry_id = src_name
    for suffix in (".source.md", ".target.md"):
        if src_name.endswith(suffix):
            entry_id = src_name[: -len(suffix)]
            break
    else:
        entry_id = Path(src_path).stem

    # ── 定位 repaired_dir ──
    if not repaired_dir:
        repaired_dir = str(Path(src_path).parent / "repaired")

    result = promote_repaired(
        entry_id,
        src_path,
        tgt_path,
        repaired_dir,
        dry_run=dry_run,
        strategy=strategy,
    )
    if not result["success"]:
        print(f"错误: {result['message']}")
        return 1

    if dry_run:
        strategy_desc = {"src": "仅原文未变", "tgt": "仅译文未变", "": "无条件"}.get(
            strategy, ""
        )
        print(f"[模拟]  晋升策略: {strategy_desc}")
        print(f"[模拟]  源文件: {src_path}")
        print(
            f"         → 将被替换为: {os.path.join(repaired_dir, f'{entry_id}.source.md')}"
        )
        print(f"        原始文件备份: {result['src_backup']}")
        print(f"[模拟]  目标文件: {tgt_path}")
        print(
            f"         → 将被替换为: {os.path.join(repaired_dir, f'{entry_id}.target.md')}"
        )
        print(f"        原始文件备份: {result['tgt_backup']}")
        for cp in result.get("cache_paths_cleared", []):
            print(f"[模拟]  将清除缓存: {cp}")
        report_path = os.path.join(repaired_dir, f"{entry_id}.report.json")
        print(f"[模拟]  报告文件: {report_path} → 清除 ai_review")
        print()
        print("✅ 模拟完成，未执行任何修改。去掉 --dry-run 后实际执行。")
        return 0

    # ── 实际执行 ──
    print(f"  ✓ 原始文件已备份: {result['src_backup']} / {result['tgt_backup']}")
    print(
        f"  ✓ 源文件已替换: {os.path.join(repaired_dir, f'{entry_id}.source.md')} → {src_path}"
    )
    print(
        f"  ✓ 目标文件已替换: {os.path.join(repaired_dir, f'{entry_id}.target.md')} → {tgt_path}"
    )
    for cp in result.get("cache_paths_cleared", []):
        print(f"  ✓ 缓存已清除: {cp}")
    if result.get("report_updated"):
        print("  ✓ report.json 已清除过期元数据")
    print(f"  替换后文件行数: src={result['src_count']}, tgt={result['tgt_count']}")
    print()
    print("✅ 置换完成。编码缓存保持不动（自验证命中）。")
    print("   下次 `dualign align` 会自动重新编码并创建新缓存。")
    return 0


def _cmd_check():
    """环境健康检查子命令。"""
    from dualign.providers import (
        ProviderManager,
        active_repair_agent,
        detect_ollama_cli,
    )

    ProviderManager.load()

    print("═" * 40)
    print("Dualign 环境检查")
    print("═" * 40)

    OK = "OK"
    NO = "NO"
    cli_found, cli_ver = detect_ollama_cli()
    print(f"\n  Ollama CLI:  [{OK if cli_found else NO}] {cli_ver}")

    cfg = ProviderManager.get("ollama")
    if cfg and cfg.base_url:
        ok, detail, models = ProviderManager.health_check(cfg)
        print(f"  Ollama API:  [{OK if ok else NO}] {detail}")
    else:
        print("  Ollama API:  [NO] 未配置")
        models = []

    active = ProviderManager.active()
    if active:
        ok, detail, m_list = ProviderManager.health_check(active)
        model_found = any(active.model_name in m for m in m_list)
        print(
            f"  嵌入模型:   [{OK if (ok and model_found) else NO}] {active.model_name}"
        )
    else:
        print("  嵌入模型:   [NO] 未配置")

    agent = active_repair_agent()
    if agent:
        print(f"  AI Agent:   [{OK}] {agent.label} ({agent.model_name})")
    else:
        print("  AI Agent:   [--] 未配置 (可选)")

    # 可用模型
    if models:
        print(f"\n  可用模型 ({len(models)}):")
        for m in sorted(models)[:20]:
            print(f"    • {m}")

    print()
    return 0


def _cmd_models():
    """列出嵌入编码可用模型。"""
    from dualign.providers import ProviderManager

    ProviderManager.load()
    cfg = ProviderManager.get("ollama")
    if cfg is None or not cfg.base_url:
        print("❌ Ollama 未配置")
        return 1

    ok, detail, models = ProviderManager.health_check(cfg)
    if not ok and "已连接" not in detail:
        print(f"❌ {detail}")
        return 1

    if not models:
        print("⚠ 未找到任何模型")
        return 0

    print(f"Ollama 可用模型 ({len(models)}):")
    for m in sorted(models):
        active = ProviderManager.active()
        mark = " ← 当前" if active and active.model_name in m else ""
        print(f"  {m}{mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
