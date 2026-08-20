"""Recoverable save for two source documents and their work report."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from dualign.config import get_cache_root
from dualign.models.pair_editing import PairEditingState
from dualign.services.alignment_io import document_sha256, document_sha256_from_text
from dualign.services.report_io import build_report


class PairSaveError(RuntimeError):
    """Base error for a failed multi-file save."""


class PairSaveConflictError(PairSaveError):
    """Raised when a file changed outside Dualign after it was opened."""


@dataclass(frozen=True)
class PairSaveResult:
    document_a_path: Path
    document_b_path: Path
    report_path: Path
    document_a_sha256: str
    document_b_sha256: str
    report_sha256: str


def _file_bytes_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_temp(target: Path, payload: str, transaction_id: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        prefix=f".{target.name}.{transaction_id}.",
        suffix=".tmp",
        dir=target.parent,
        delete=False,
    ) as handle:
        path = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _write_journal(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _install_prepared(temp_path: Path, target_path: Path) -> None:
    """Install one prepared file; isolated so rollback behavior is testable."""

    os.replace(temp_path, target_path)


def _rollback(journal: dict) -> list[str]:
    errors: list[str] = []
    for item in reversed(journal.get("targets", [])):
        target = Path(item["path"])
        backup = Path(item["backup"])
        temporary = Path(item["temporary"])
        try:
            if backup.exists():
                if target.exists():
                    target.unlink()
                os.replace(backup, target)
            elif not item.get("existed", False) and target.exists():
                expected_new = item.get("new_sha256", "")
                if not expected_new or _file_bytes_sha256(target) == expected_new:
                    target.unlink()
            temporary.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{target}: {exc}")
    return errors


def recover_pending_pair_saves(transaction_dir: str | Path | None = None) -> list[str]:
    """Conservatively roll back interrupted saves and return recovery messages."""

    root = Path(transaction_dir or Path(get_cache_root()) / "transactions")
    if not root.is_dir():
        return []
    messages: list[str] = []
    for journal_path in sorted(root.glob("pair-save-*.json")):
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            errors = _rollback(journal)
            if errors:
                messages.append(
                    f"事务 {journal.get('id', journal_path.stem)} 恢复不完整: "
                    + "; ".join(errors)
                )
                continue
            journal_path.unlink(missing_ok=True)
            messages.append(f"已回滚未完成保存: {journal.get('id', journal_path.stem)}")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            messages.append(f"无法恢复事务 {journal_path}: {exc}")
    return messages


def save_pair_transaction(
    state: PairEditingState,
    *,
    document_a_path: str | Path,
    document_b_path: str | Path,
    report_path: str | Path,
    report: dict,
    expected_report_sha256: str = "",
    expected_report_exists: bool | None = None,
    transaction_dir: str | Path | None = None,
) -> PairSaveResult:
    """Save two documents and their rebased report as one transaction."""

    path_a = Path(document_a_path).resolve()
    path_b = Path(document_b_path).resolve()
    report_target = Path(report_path).resolve()
    if len({os.path.normcase(str(path)) for path in (path_a, path_b, report_target)}) != 3:
        raise PairSaveError("两份正文和工作报告必须使用三个不同路径")

    expected_a = state.document_a_ref.sha256
    expected_b = state.document_b_ref.sha256
    conflicts: list[str] = []
    if not path_a.is_file() or (expected_a and document_sha256(path_a) != expected_a):
        conflicts.append("文档 A")
    if not path_b.is_file() or (expected_b and document_sha256(path_b) != expected_b):
        conflicts.append("文档 B")
    if expected_report_exists is False and report_target.exists():
        conflicts.append("工作报告")
    elif expected_report_exists is True and (
        not report_target.is_file()
        or (
            expected_report_sha256
            and _file_bytes_sha256(report_target) != expected_report_sha256
        )
    ):
        conflicts.append("工作报告")
    elif expected_report_sha256 and (
        not report_target.is_file()
        or _file_bytes_sha256(report_target) != expected_report_sha256
    ):
        conflicts.append("工作报告")
    if conflicts:
        raise PairSaveConflictError(
            "以下文件在打开后被外部修改或删除，已拒绝覆盖：" + "、".join(conflicts)
        )

    text_a = state.document_a.render_text()
    text_b = state.document_b.render_text()
    hash_a = document_sha256_from_text(text_a)
    hash_b = document_sha256_from_text(text_b)
    pair = state.to_alignment_pair()
    operations = [
        (
            tuple(index - 1 for index in link.document_a),
            tuple(index - 1 for index in link.document_b),
            float(link.confidence or 0.0),
        )
        for link in pair.links
        if link.state != "rejected"
    ]
    from datetime import datetime

    previous = dict(report)
    history = list(previous.get("history", []))
    history.append(
        {
            "type": "source-overwrite",
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "repair_log": list(previous.get("repair_log", [])),
        }
    )
    previous["history"] = history
    stats = dict(previous.get("stats") or {})
    stats.update(
        {
            "n_source": len(state.document_a.blocks),
            "n_target": len(state.document_b.blocks),
            "n_ops": len(operations),
            "alignment_origin": "source-overwrite",
        }
    )
    rebased_report = build_report(
        chapter_id=str(previous.get("chapter_id") or path_a.stem.split(".")[0]),
        document_a_path=path_a,
        document_b_path=path_b,
        operations=operations,
        stats=stats,
        quality=dict(previous.get("quality") or {}),
        provenance=dict(previous.get("provenance") or {}),
        repair_log=(),
        previous=previous,
        document_a_sha256_value=hash_a,
        document_b_sha256_value=hash_b,
    )
    report_text = json.dumps(rebased_report, ensure_ascii=False, indent=2) + "\n"

    transaction_id = uuid.uuid4().hex
    root = Path(transaction_dir or Path(get_cache_root()) / "transactions")
    journal_path = root / f"pair-save-{transaction_id}.json"
    payloads = ((path_a, text_a), (path_b, text_b), (report_target, report_text))
    targets: list[dict] = []
    try:
        for target, payload in payloads:
            temporary = _write_temp(target, payload, transaction_id)
            backup = target.parent / f".{target.name}.{transaction_id}.rollback"
            targets.append(
                {
                    "path": str(target),
                    "temporary": str(temporary),
                    "backup": str(backup),
                    "existed": target.exists(),
                    "original_sha256": (
                        _file_bytes_sha256(target) if target.exists() else ""
                    ),
                    "new_sha256": _file_bytes_sha256(temporary),
                    "installed": False,
                }
            )
        journal = {"version": 1, "id": transaction_id, "targets": targets}
        _write_journal(journal_path, journal)

        for item in targets:
            target = Path(item["path"])
            temporary = Path(item["temporary"])
            backup = Path(item["backup"])
            if target.exists():
                os.replace(target, backup)
            _install_prepared(temporary, target)
            item["installed"] = True
            _write_journal(journal_path, journal)

        for item in targets:
            Path(item["backup"]).unlink(missing_ok=True)
        journal_path.unlink(missing_ok=True)
    except Exception as exc:
        journal = {"version": 1, "id": transaction_id, "targets": targets}
        rollback_errors = _rollback(journal)
        if not rollback_errors:
            journal_path.unlink(missing_ok=True)
        detail = f"三文件保存失败: {exc}"
        if rollback_errors:
            detail += "；自动回滚不完整: " + "; ".join(rollback_errors)
        raise PairSaveError(detail) from exc

    return PairSaveResult(
        document_a_path=path_a,
        document_b_path=path_b,
        report_path=report_target,
        document_a_sha256=hash_a,
        document_b_sha256=hash_b,
        report_sha256=_file_bytes_sha256(report_target),
    )
