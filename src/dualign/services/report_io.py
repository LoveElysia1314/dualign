"""Durable JSON work reports for one pair of documents.

The report is the only persisted editing state.  Source documents stay
untouched until the user explicitly overwrites them; paired reader rows are
materialized from the report only when a consumer asks for them.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from dualign.models.action import RepairAction
from dualign.models.state import AlignmentSnapshot
from dualign.services.alignment_io import document_sha256
from dualign.services.repair import RepairService, RepairState

REPORT_FORMAT = "dualign-report"


class ReportError(ValueError):
    """Raised when a work report is malformed or no longer matches its inputs."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def operations_payload(operations) -> list[dict[str, Any]]:
    return [
        {"s": list(source), "t": list(target), "sc": round(float(score), 6)}
        for source, target, score in operations
    ]


def operations_from_report(report: Mapping[str, Any]) -> list[tuple]:
    try:
        return [
            (tuple(item["s"]), tuple(item["t"]), float(item["sc"]))
            for item in report["ops"]
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise ReportError("报告中的对齐关系无效") from exc


def build_report(
    *,
    chapter_id: str,
    document_a_path: str | Path,
    document_b_path: str | Path,
    operations,
    stats: Mapping[str, Any],
    quality: Mapping[str, Any],
    provenance: Mapping[str, Any],
    repair_log=(),
    previous: Mapping[str, Any] | None = None,
    document_a_sha256_value: str = "",
    document_b_sha256_value: str = "",
) -> dict[str, Any]:
    """Build a complete report while retaining review data from a valid report."""

    path_a = Path(document_a_path)
    path_b = Path(document_b_path)
    ops = operations_payload(operations)
    documents = {
        "a": {
            "path": path_a.name,
            "sha256": document_a_sha256_value or document_sha256(path_a),
        },
        "b": {
            "path": path_b.name,
            "sha256": document_b_sha256_value or document_sha256(path_b),
        },
    }
    fingerprint = _canonical_sha256(
        {
            "documents": documents,
            "ops": ops,
            "segmentation": "content-line",
            "provenance": provenance,
        }
    )
    old = dict(previous or {})
    created_at = old.get("created_at") or _now()
    report: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "chapter_id": chapter_id,
        "created_at": created_at,
        "updated_at": _now(),
        "documents": documents,
        # Kept at top level because repair replay and manager aggregation scan
        # these fields frequently.
        "src_hash": documents["a"]["sha256"],
        "tgt_hash": documents["b"]["sha256"],
        "segmentation": "content-line",
        "ops": ops,
        "snapshot_fingerprint": fingerprint,
        "provenance": dict(provenance),
        "stats": dict(stats),
        "quality": dict(quality),
        "repair_log": [
            action.to_dict() if isinstance(action, RepairAction) else dict(action)
            for action in repair_log
        ],
        "ai_proposals": old.get("ai_proposals", {}),
        "ai_review": old.get("ai_review", {}),
        "scores": old.get("scores", {}),
        "history": list(old.get("history", [])),
    }
    return report


def save_report(report: Mapping[str, Any], path: str | Path) -> Path:
    """Validate and atomically replace a report."""

    data = deepcopy(dict(report))
    if data.get("format") != REPORT_FORMAT:
        raise ReportError("拒绝写入无法识别的 Dualign 报告")
    operations_from_report(data)
    data["updated_at"] = _now()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def load_report(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"无法读取报告: {path}") from exc
    if not isinstance(data, dict) or data.get("format") != REPORT_FORMAT:
        raise ReportError("报告格式已过时，请重新对齐文档")
    operations_from_report(data)
    return data


def report_matches_documents(
    report: Mapping[str, Any], document_a_path: str | Path, document_b_path: str | Path
) -> bool:
    documents = report.get("documents") or {}
    try:
        return (
            documents["a"]["sha256"] == document_sha256(document_a_path)
            and documents["b"]["sha256"] == document_sha256(document_b_path)
        )
    except (KeyError, OSError, TypeError):
        return False


def report_matches_provenance(
    report: Mapping[str, Any], provenance: Mapping[str, Any]
) -> bool:
    return report.get("provenance") == dict(provenance)


def repair_state_from_report(
    report: Mapping[str, Any], document_a_path: str | Path, document_b_path: str | Path
) -> RepairState:
    if not report_matches_documents(report, document_a_path, document_b_path):
        raise ReportError("源文档已变化，报告中的行索引不再安全")
    from dualign.common import load_text_lines

    snapshot = AlignmentSnapshot.from_alignment(
        operations_from_report(report),
        load_text_lines(str(document_a_path)),
        load_text_lines(str(document_b_path)),
    )
    actions = [RepairAction.from_dict(item) for item in report.get("repair_log", [])]
    return RepairState(snapshot, actions)


def materialize_reader_rows(
    report_path: str | Path,
    document_a_path: str | Path,
    document_b_path: str | Path,
) -> tuple[list[str], list[str]]:
    """Replay a report into equal-row text solely for reader/build consumers."""

    report = load_report(report_path)
    return RepairService.render_rows(
        repair_state_from_report(report, document_a_path, document_b_path)
    )


def update_report(
    path: str | Path, mutator: Callable[[dict[str, Any]], None]
) -> dict[str, Any]:
    report = load_report(path)
    mutator(report)
    save_report(report, path)
    return report


def set_ai_review(path: str | Path, status: str, note: str = "") -> dict[str, Any]:
    def mutate(report: dict[str, Any]) -> None:
        report["ai_review"] = {"status": status, "note": note, "updated_at": _now()}

    return update_report(path, mutate)
