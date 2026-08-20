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

from dualign.config import get_cache_root

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


def _render_logged_repairs(
    report_path: str, raw_src: str, raw_tgt: str
) -> Optional[tuple[list[str], list[str]]]:
    """从报告重放校订日志；无校订日志时返回 ``None``。

    报告一旦声明存在 repair_log，它就是待晋升文本的权威来源。解析或
    重放失败必须向上传递，禁止悄悄退回可能过期的 repaired 文件。
    """
    if not os.path.isfile(report_path):
        return None

    import json as _json

    with open(report_path, "r", encoding="utf-8") as report_file:
        data = _json.load(report_file)
    log_raw = data.get("repair_log", [])
    if not log_raw:
        return None

    ops_raw = data.get("ops", [])
    if not ops_raw:
        raise ValueError("报告包含 repair_log，但缺少可重放的 ops")
    if not os.path.isfile(raw_src) or not os.path.isfile(raw_tgt):
        raise FileNotFoundError("校订日志重放所需的原始文件不存在")

    from dualign.models.action import RepairAction
    from dualign.models.state import AlignmentSnapshot
    from dualign.services.repair import RepairService, RepairState

    ops = [(tuple(item["s"]), tuple(item["t"]), float(item["sc"])) for item in ops_raw]
    snapshot = AlignmentSnapshot.from_alignment(
        ops, load_text_lines(raw_src), load_text_lines(raw_tgt)
    )
    actions = [RepairAction.from_dict(item) for item in log_raw]
    return RepairService.render_rows(RepairState(snapshot, actions))


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
      1. 从 report.json 重放当前校订日志（若存在）
      2. 备份原始文件（加 .bak 后缀）并原子置换
      3. 将旧报告归档为 .pre-promote.bak
      4. 删除依赖旧文本基线的 repaired/会话/相似度产物

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
          report_backup: str | None
          artifacts_invalidated: list[str]
          src_count: int
          tgt_count: int
    """
    import shutil
    import tempfile

    result: Dict[str, Any] = {
        "success": False,
        "message": "",
        "src_backup": None,
        "tgt_backup": None,
        "cache_paths_cleared": [],
        "report_backup": None,
        "artifacts_invalidated": [],
        "src_count": 0,
        "tgt_count": 0,
    }

    src_path = os.path.normpath(src_path)
    tgt_path = os.path.normpath(tgt_path)
    repaired_dir = os.path.normpath(repaired_dir)

    if strategy not in {"", "src", "tgt"}:
        result["message"] = f"未知晋升策略: {strategy}"
        return result

    if not os.path.isfile(src_path):
        result["message"] = f"源文件不存在: {src_path}"
        return result
    if not os.path.isfile(tgt_path):
        result["message"] = f"目标文件不存在: {tgt_path}"
        return result

    repaired_src = os.path.join(repaired_dir, f"{entry_id}.source.md")
    repaired_tgt = os.path.join(repaired_dir, f"{entry_id}.target.md")
    distinct_paths = {
        os.path.normcase(os.path.abspath(path))
        for path in (src_path, tgt_path, repaired_src, repaired_tgt)
    }
    if len(distinct_paths) != 4:
        result["message"] = "原始文件与 repaired 文件路径发生重叠，拒绝晋升"
        return result

    missing = []
    if not os.path.isfile(repaired_src):
        missing.append(repaired_src)
    if not os.path.isfile(repaired_tgt):
        missing.append(repaired_tgt)
    if missing:
        result["message"] = f"找不到 repaired 文件: {missing}"
        return result

    # ── 计算实际待晋升内容 ──
    report_path = os.path.join(repaired_dir, f"{entry_id}.report.json")
    try:
        rendered = _render_logged_repairs(report_path, src_path, tgt_path)
        if rendered is None:
            promoted_src_lines = load_text_lines(repaired_src)
            promoted_tgt_lines = load_text_lines(repaired_tgt)
            src_payload = Path(repaired_src).read_bytes()
            tgt_payload = Path(repaired_tgt).read_bytes()
        else:
            promoted_src_lines, promoted_tgt_lines = rendered
            src_payload = format_markdown_output(promoted_src_lines).encode("utf-8")
            tgt_payload = format_markdown_output(promoted_tgt_lines).encode("utf-8")
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        result["message"] = f"无法准备校订结果: {exc}"
        return result

    if len(promoted_src_lines) != len(promoted_tgt_lines):
        result["message"] = (
            "校订结果行数不一致: "
            f"src={len(promoted_src_lines)}, tgt={len(promoted_tgt_lines)}"
        )
        return result

    result["src_count"] = len(promoted_src_lines)
    result["tgt_count"] = len(promoted_tgt_lines)

    # ── strategy 筛选：通过 content_hash 比对 repaired 与 raw 的对应侧 ──
    if strategy:
        _strategy_ok = True
        _reason = ""
        raw_src_lines = load_text_lines(src_path)
        raw_tgt_lines = load_text_lines(tgt_path)

        if strategy == "src":
            # 仅原文侧未变时才晋升：repaired.src ≈ raw.src（hash 一致）
            if content_hash(promoted_src_lines) != content_hash(raw_src_lines):
                _strategy_ok = False
                _reason = "原文内容已变化（strategy=src 时仅原文未变才允许晋升）"
        elif strategy == "tgt":
            if content_hash(promoted_tgt_lines) != content_hash(raw_tgt_lines):
                _strategy_ok = False
                _reason = "译文内容已变化（strategy=tgt 时仅译文未变才允许晋升）"

        if not _strategy_ok:
            result["message"] = f"策略拒绝晋升: {_reason}"
            return result

    report_backup = report_path + ".pre-promote.bak"
    sim_path = report_path.replace(".report.json", ".sim.npy")
    session_path = os.path.join(get_cache_root(), "session", f"{entry_id}.json")
    derived_paths = [repaired_src, repaired_tgt, report_path, sim_path, session_path]

    if dry_run:
        result["src_backup"] = src_path + ".bak"
        result["tgt_backup"] = tgt_path + ".bak"
        if os.path.isfile(report_path):
            result["report_backup"] = report_backup
        result["artifacts_invalidated"] = [
            path for path in derived_paths if os.path.isfile(path)
        ]
        result["cache_paths_cleared"] = [
            path for path in (sim_path, session_path) if os.path.isfile(path)
        ]
        result["message"] = "模拟模式，未执行任何修改"
        result["success"] = True
        return result

    # ── 同目录临时文件 + os.replace，避免留下半写入的 raw 文件 ──
    temp_paths: list[str] = []
    try:
        for destination, payload in (
            (src_path, src_payload),
            (tgt_path, tgt_payload),
        ):
            fd, temp_path = tempfile.mkstemp(
                prefix=f".{entry_id}.promote-", dir=os.path.dirname(destination)
            )
            temp_paths.append(temp_path)
            with os.fdopen(fd, "wb") as temp_file:
                temp_file.write(payload)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            shutil.copystat(destination, temp_path)

        result["src_backup"] = src_path + ".bak"
        result["tgt_backup"] = tgt_path + ".bak"
        shutil.copy2(src_path, result["src_backup"])
        shutil.copy2(tgt_path, result["tgt_backup"])

        os.replace(temp_paths[0], src_path)
        temp_paths.pop(0)
        try:
            os.replace(temp_paths[0], tgt_path)
            temp_paths.pop(0)
        except OSError:
            shutil.copy2(result["src_backup"], src_path)
            raise

        # 报告保存完整审计记录，但移出活动路径，确保下次真正重新对齐。
        if os.path.isfile(report_path):
            try:
                os.replace(report_path, report_backup)
                result["report_backup"] = report_backup
            except OSError:
                shutil.copy2(result["src_backup"], src_path)
                shutil.copy2(result["tgt_backup"], tgt_path)
                raise
    except OSError as exc:
        result["message"] = f"晋升置换失败，原始文件已回滚: {exc}"
        return result
    finally:
        for temp_path in temp_paths:
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass

    # report 已归档，余下派生产物即使个别清理失败也不会被当作有效对齐。
    invalidated = [report_path] if result["report_backup"] else []
    cleanup_errors = []
    cleared = []
    for path in (repaired_src, repaired_tgt, sim_path, session_path):
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            invalidated.append(path)
            if path in {sim_path, session_path}:
                cleared.append(path)
        except OSError as exc:
            cleanup_errors.append(f"{path}: {exc}")
    result["artifacts_invalidated"] = invalidated
    result["cache_paths_cleared"] = cleared

    result["success"] = True
    result["message"] = "置换完成；旧对齐与校订产物已失效，请重新对齐。"
    if cleanup_errors:
        result["message"] += " 未能清理: " + "; ".join(cleanup_errors)
    return result


def refresh_repaired_from_report(
    report_path: str,
    repaired_src: str,
    repaired_tgt: str,
    raw_src: str,
    raw_tgt: str,
) -> bool:
    """从 report.json 重建 repaired 文件（含 AI 校订结果）。

    仅当 report.json 存在且含非空 repair_log 时才执行重导出。
    重导出使用 raw 文件作为原始文本基准，report.json 中的 ops
    和 repair_log 描述了对齐及所有修复操作。

    消费端在 AI 校订完成后调用本函数即时更新导出文件；晋升操作复用
    同一套日志重放逻辑，但直接生成原子置换所需的内存载荷。
    """
    rendered = _render_logged_repairs(report_path, raw_src, raw_tgt)
    if rendered is None:
        return False

    src_out, tgt_out = rendered
    os.makedirs(os.path.dirname(repaired_src), exist_ok=True)
    with open(repaired_src, "w", encoding="utf-8") as src_file:
        src_file.write(format_markdown_output(src_out))
    with open(repaired_tgt, "w", encoding="utf-8") as tgt_file:
        tgt_file.write(format_markdown_output(tgt_out))

    set_ai_review(report_path, "completed", "")
    return True


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
