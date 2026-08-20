"""
Dualign — CLI 入口

用法:
  python -m dualign [-h]
  python -m dualign gui [--document-a A.md --document-b B.md]
  python -m dualign align --document-a A.md --document-b B.md [-o pair.report.json]
"""

from __future__ import annotations

import sys
import os
import json
import argparse
from pathlib import Path


def _load_gui_entries(entries_file: str):
    """读取集成方传入的章节清单，并转换为 Dualign 的 FilePair。"""
    if not entries_file:
        return None
    from dualign.common import FilePair

    with open(entries_file, encoding="utf-8") as manifest_file:
        items = json.load(manifest_file)
    if not isinstance(items, list):
        raise ValueError("GUI entries manifest 必须是 JSON 数组")
    entries = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("GUI entries manifest 中的章节必须是对象")
        entries.append(
            FilePair(
                entry_id=str(item.get("entry_id", "")),
                label=str(item.get("label", "")),
                document_a_path=str(item.get("document_a_path", "")),
                document_b_path=str(item.get("document_b_path", "")),
                report_path=str(item.get("report_path", "")),
                document_a_id=str(item.get("document_a_id", "")),
                document_b_id=str(item.get("document_b_id", "")),
                language_a=str(item.get("language_a", "")),
                language_b=str(item.get("language_b", "")),
                metadata=dict(item.get("metadata") or {}),
            )
        )
    return entries


def main_gui(src_path: str = "", tgt_path: str = "", entries_file: str = ""):
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

    entries = _load_gui_entries(entries_file)
    window = DualignWindow(file_entries=entries)

    if entries is None and src_path and tgt_path:
        window.load_file_pair(src_path, tgt_path, label=os.path.basename(src_path))

    window.show()
    sys.exit(app.exec())


def main_align(document_a: str, document_b: str, output: str = ""):
    """Create a replayable work report without rewriting either document."""
    from dualign.services.cli_pipeline import align_documents, default_report_path

    output_path = Path(output) if output else default_report_path(document_a)
    if output and output_path.suffix.lower() != ".json":
        output_path = output_path / default_report_path(document_a).name
    print(f"文档 A: {document_a}")
    print(f"文档 B: {document_b}")
    result = align_documents(document_a, document_b, str(output_path))
    if not result.get("success"):
        print(f"对齐失败: {result.get('error', '未知错误')}")
        return 1
    print("\n[OK] 对齐完成")
    print(f"   工作报告: {result['report_path']}")
    print(f"   关系数量: {len(result.get('ops', []))}")
    print("   两份输入文档未被改写。")
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
        "  dualign align -a a.md -b b.md     生成可恢复的 JSON 报告\n"
        "  dualign gui                     启动图形界面",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    sub = parser.add_subparsers(dest="command", required=False)

    # ── gui ──
    p_gui = sub.add_parser("gui", help="启动图形界面")
    p_gui.add_argument("--document-a", dest="src", default="", help="文档 A 路径")
    p_gui.add_argument("--document-b", dest="tgt", default="", help="文档 B 路径")
    p_gui.add_argument(
        "--entries-file",
        default="",
        help="由集成方提供的章节清单 JSON（支持多章及独立报告目录）",
    )

    # ── align ──
    p_align = sub.add_parser("align", help="生成对齐与校订工作报告")
    p_align.add_argument(
        "-a",
        "--document-a",
        dest="document_a",
        required=True,
        help="文档 A 路径",
    )
    p_align.add_argument(
        "-b",
        "--document-b",
        dest="document_b",
        required=True,
        help="文档 B 路径",
    )
    p_align.add_argument(
        "-o",
        "--output",
        "--out",
        default="",
        help="*.report.json 路径；传目录时使用默认文件名",
    )

    # ── check ──
    sub.add_parser("check", help="环境健康检查")

    # ── models ──
    sub.add_parser("models", help="列出可用模型")

    args = parser.parse_args()

    if args.command == "gui":
        main_gui(
            src_path=args.src,
            tgt_path=args.tgt,
            entries_file=args.entries_file,
        )
    elif args.command == "align":
        return main_align(args.document_a, args.document_b, args.output)
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
        print(f"  Ollama API:  [{OK if ok else NO}] {detail.lstrip('✓ ')}")
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
            print(f"    - {m}")

    print()
    return 0


def _cmd_models():
    """列出嵌入编码可用模型。"""
    from dualign.providers import ProviderManager

    ProviderManager.load()
    cfg = ProviderManager.get("ollama")
    if cfg is None or not cfg.base_url:
        print("[NO] Ollama 未配置")
        return 1

    ok, detail, models = ProviderManager.health_check(cfg)
    if not ok and "已连接" not in detail:
        print(f"[NO] {detail.lstrip('✓ ')}")
        return 1

    if not models:
        print("[--] 未找到任何模型")
        return 0

    print(f"Ollama 可用模型 ({len(models)}):")
    for m in sorted(models):
        active = ProviderManager.active()
        mark = " ← 当前" if active and active.model_name in m else ""
        print(f"  {m}{mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
