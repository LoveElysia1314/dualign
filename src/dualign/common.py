"""
Dualign — 公共工具函数

I/O 工具、数据结构、格式化、晋升逻辑。
配置常量及缓存路径管理见 dualign.config。
"""

from __future__ import annotations

import os
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from dualign.config import (
    _ui_session_cache_path,
)

# ═══════════════════════════════════════════════════════════════
# 哈希与缓存工具
# ═══════════════════════════════════════════════════════════════


def content_hash(lines: list) -> str:
    """计算文本行列表的 SHA256 内容哈希。

    将行用换行符拼接后整体哈希，确保不同行数/内容产生不同摘要。
    所有缓存验证统一使用此函数。
    """
    combined = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def instruction_hash(instruction: str) -> str:
    """计算 Instruction 文本的 SHA256 哈希（前 16 位）。

    用于嵌入缓存校验：Instruction 变化 → 缓存自动失效 → 重新编码。
    """
    return hashlib.sha256(instruction.encode("utf-8")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════
# 3. FileListProvider — 文件列表抽象
# ═══════════════════════════════════════════════════════════════


@dataclass
class FilePair:
    """一个待对齐的文件对。"""

    entry_id: str
    label: str
    source_path: str
    target_path: str
    repaired_dir: str
    report_path: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def repaired_source_path(self) -> str:
        return str(Path(self.repaired_dir) / f"{self.entry_id}.source.md")

    @property
    def repaired_target_path(self) -> str:
        return str(Path(self.repaired_dir) / f"{self.entry_id}.target.md")


class FileListProvider:
    """文件对列表提供者 — DualignWindow 消费的唯一入口。"""

    def list_entries(self) -> List[FilePair]:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════
# 5. 输出格式化
# ═══════════════════════════════════════════════════════════════


def load_text_lines(path: str) -> list:
    """加载文本文件为行列表。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except (FileNotFoundError, OSError):
        return []


def format_markdown_output(lines: list[str]) -> str:
    """将行列表格式化为 Markdown 文本。

    保留所有行（含空行和 ⟢MISSING⟣ 占位符），用 \\n\\n 分隔。末尾加一个 \\n。
    不作过滤——过滤会导致 src/tgt 两侧行数不对称，破坏对齐结构。
    MISSING 占位符在消费端应显示为可见标记而非被删除。
    """
    if not lines:
        return ""
    return "\n\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════
# 5. promote_repaired — 修复结果晋升置换
# ═══════════════════════════════════════════════════════════════


def promote_repaired(
    entry_id: str,
    src_path: str,
    tgt_path: str,
    repaired_dir: str,
    dry_run: bool = False,
    strategy: str = "",
) -> Dict[str, Any]:
    """用修复后的文件置换源文档对（晋升操作）。

    步骤:
      1. 备份原始文件（加 .bak 后缀）
      2. 拷贝 repaired 文件覆盖原文件
      3. 清除过期的会话缓存
      4. 在 report.json 中清除旧对齐/AI 审校数据

    嵌入缓存（SQLite vecs.db）通过 content_hash 自验证，
    内容变更后自动失效，无需主动删除。

    Args:
        entry_id:    章节唯一标识
        src_path:    原始原文文件路径（将被覆盖）
        tgt_path:    原始译文文件路径（将被覆盖）
        repaired_dir: repaired 输出目录
        dry_run:     仅模拟，不实际执行
        strategy:    晋升筛选策略。""(无条件) / "src"(仅原文未变时晋升)
                     / "tgt"(仅译文未变时晋升)。通过 content_hash 比对
                     repaired 与 raw 的对应侧文本，一致时方可晋升。

    Returns:
        dict: 操作结果，含以下键:
          success: bool
          message: str
          src_backup: str | None
          tgt_backup: str | None
          cache_paths_cleared: list[str]
          report_updated: bool
          src_count: int
          tgt_count: int
    """
    import shutil
    import json as _json

    result: Dict[str, Any] = {
        "success": False,
        "message": "",
        "src_backup": None,
        "tgt_backup": None,
        "cache_paths_cleared": [],
        "report_updated": False,
        "src_count": 0,
        "tgt_count": 0,
    }

    src_path = os.path.normpath(src_path)
    tgt_path = os.path.normpath(tgt_path)
    repaired_dir = os.path.normpath(repaired_dir)

    if not os.path.isfile(src_path):
        result["message"] = f"源文件不存在: {src_path}"
        return result
    if not os.path.isfile(tgt_path):
        result["message"] = f"目标文件不存在: {tgt_path}"
        return result

    repaired_src = os.path.join(repaired_dir, f"{entry_id}.source.md")
    repaired_tgt = os.path.join(repaired_dir, f"{entry_id}.target.md")

    missing = []
    if not os.path.isfile(repaired_src):
        missing.append(repaired_src)
    if not os.path.isfile(repaired_tgt):
        missing.append(repaired_tgt)
    if missing:
        result["message"] = f"找不到 repaired 文件: {missing}"
        return result

    # ── report.json 路径 ──
    report_path = os.path.join(repaired_dir, f"{entry_id}.report.json")

    # ── 晋升前：从 report.json 重建并重导出 repaired 文件 ──
    # 确保 AI 校订结果已正确应用到 repaired 输出，即使磁盘上的
    # repaired 文件是旧的也能正确晋升。此操作须在 strategy 检查
    # 之前执行，否则 strategy 会基于旧文件做判断。
    refresh_repaired_from_report(
        report_path, repaired_src, repaired_tgt, src_path, tgt_path
    )

    # ── strategy 筛选：通过 content_hash 比对 repaired 与 raw 的对应侧 ──
    if strategy:
        _strategy_ok = True
        _reason = ""
        repaired_src_lines = load_text_lines(repaired_src)
        repaired_tgt_lines = load_text_lines(repaired_tgt)
        raw_src_lines = load_text_lines(src_path)
        raw_tgt_lines = load_text_lines(tgt_path)

        if strategy == "src":
            # 仅原文侧未变时才晋升：repaired.src ≈ raw.src（hash 一致）
            if content_hash(repaired_src_lines) != content_hash(raw_src_lines):
                _strategy_ok = False
                _reason = "原文内容已变化（strategy=src 时仅原文未变才允许晋升）"
        elif strategy == "tgt":
            if content_hash(repaired_tgt_lines) != content_hash(raw_tgt_lines):
                _strategy_ok = False
                _reason = "译文内容已变化（strategy=tgt 时仅译文未变才允许晋升）"

        if not _strategy_ok:
            result["message"] = f"策略拒绝晋升: {_reason}"
            return result

    report_exists = os.path.isfile(report_path)

    # ── 会话缓存：{cache_root}/session/{entry_id}.json ──
    session_path = _ui_session_cache_path(entry_id)

    if dry_run:
        result["src_backup"] = src_path + ".bak"
        result["tgt_backup"] = tgt_path + ".bak"
        _dry_cache = []
        if os.path.isfile(session_path):
            _dry_cache.append(session_path)
        result["cache_paths_cleared"] = _dry_cache
        if report_exists:
            result["message"] = "模拟模式，将清除 report.json 中的 ai_review"
        else:
            result["message"] = "模拟模式，未执行任何修改"
        result["success"] = True
        return result

    # ── 备份原始文件 ──
    shutil.copy2(src_path, src_path + ".bak")
    shutil.copy2(tgt_path, tgt_path + ".bak")
    result["src_backup"] = src_path + ".bak"
    result["tgt_backup"] = tgt_path + ".bak"

    # ── 拷贝 repaired → 覆盖原始 ──
    shutil.copy2(repaired_src, src_path)
    shutil.copy2(repaired_tgt, tgt_path)

    # ── 清除会话缓存（UI 状态过期）──
    # 编码缓存（align_emb_cache.npz）通过 content_hash 自验证，保留。
    cleared: List[str] = []
    if os.path.isfile(session_path):
        try:
            os.remove(session_path)
            cleared.append(session_path)
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(
                f"清除会话缓存失败: {session_path}: {e}"
            )
    result["cache_paths_cleared"] = cleared

    # ── 更新 report.json：清空 ai_review ──
    if report_exists:
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                report_data = _json.load(f)
            dirty = False
            if "ai_review" in report_data:
                del report_data["ai_review"]
                dirty = True
            if dirty:
                with open(report_path, "w", encoding="utf-8") as f:
                    _json.dump(
                        report_data, f, ensure_ascii=False, separators=(",", ":")
                    )
                result["report_updated"] = True
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(
                f"更新 report.json 失败: {report_path}: {e}"
            )

    # ── 新文件行数统计 ──
    with open(src_path, encoding="utf-8") as f:
        result["src_count"] = len(f.readlines())
    with open(tgt_path, encoding="utf-8") as f:
        result["tgt_count"] = len(f.readlines())

    result["success"] = True
    result["message"] = "置换完成。原始文件已备份为 .bak。"
    return result


def refresh_repaired_from_report(
    report_path: str,
    repaired_src: str,
    repaired_tgt: str,
    raw_src: str,
    raw_tgt: str,
):
    """从 report.json 重建 repaired 文件（含 AI 校订结果）。

    仅当 report.json 存在且含非空 repair_log 时才执行重导出。
    重导出使用 raw 文件作为原始文本基准，report.json 中的 ops
    和 repair_log 描述了对齐及所有修复操作。

    此函数供两方面使用：
      1. promote_repaired() —— 晋升前确保 AI 校订已反映到导出文件
      2. 消费端 ai_repair_chapter() —— AI 校订完成后即时更新导出文件
    """
    if not os.path.isfile(report_path):
        return
    try:
        import json as _json

        with open(report_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except Exception:
        return

    ops_raw = data.get("ops", [])
    log_raw = data.get("repair_log", [])
    if not ops_raw or not log_raw:
        return

    # 读取 raw 文件作为基线
    if not os.path.isfile(raw_src) or not os.path.isfile(raw_tgt):
        return
    sl = load_text_lines(raw_src)
    tl = load_text_lines(raw_tgt)

    try:
        from dualign.services.repair import RepairState, RepairService
        from dualign.models.state import AlignmentSnapshot
        from dualign.models.action import RepairAction

        ops = [
            (
                tuple(o["s"]),
                tuple(o["t"]),
                float(o["sc"]),
            )
            for o in ops_raw
        ]
        snap = AlignmentSnapshot.from_alignment(ops, sl, tl)
        log = [RepairAction.from_dict(a) for a in log_raw]
        state = RepairState(snap, log)

        src_out, tgt_out = RepairService.render_rows(state)
        os.makedirs(os.path.dirname(repaired_src), exist_ok=True)
        with open(repaired_src, "w", encoding="utf-8") as f:
            f.write(format_markdown_output(src_out))
        with open(repaired_tgt, "w", encoding="utf-8") as f:
            f.write(format_markdown_output(tgt_out))

        # 写入 AI 审校完成状态
        from dualign.common import set_ai_review

        set_ai_review(report_path, "completed", "")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# Report I/O — report.json 读写
# ═══════════════════════════════════════════════════════════════


def save_report(report_data: dict, path: str) -> None:
    """写入 report.json。"""
    import json as _json

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(report_data, f, ensure_ascii=False, separators=(",", ":"))


def load_report(path: str) -> Optional[dict]:
    """读取 report.json。"""
    import json as _json

    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return _json.load(f)


def set_ai_review(path: str, status: str, note: str = ""):
    """写入 AI 审校状态到 report.json 的 ai_review 字段。

    Args:
        path: report.json 文件路径
        status: "completed" | "skipped" | "error"
        note: 备注文字（如跳过原因、错误信息）
    """
    import time as _time
    import json as _json

    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            report = _json.load(f)
    except Exception:
        return
    report["ai_review"] = {
        "status": status,
        "note": note,
        "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(report, f, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        pass
