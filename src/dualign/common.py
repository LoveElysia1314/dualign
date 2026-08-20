"""Small dependency-free helpers shared by Dualign front ends."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, List, Optional


def content_hash(lines: list) -> str:
    """Hash an already segmented line sequence."""

    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def instruction_hash(instruction: str) -> str:
    return hashlib.sha256(instruction.encode("utf-8")).hexdigest()[:16]


@dataclass
class FilePair:
    """One neutral two-document entry for the GUI."""

    entry_id: str
    label: str
    document_a_path: str
    document_b_path: str
    report_path: str = ""
    document_a_id: str = ""
    document_b_id: str = ""
    language_a: str = ""
    language_b: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def source_path(self) -> str:
        return self.document_a_path

    @property
    def target_path(self) -> str:
        return self.document_b_path

    @property
    def alignment_path(self) -> str:
        """Internal GUI alias for the sole report path."""

        return self.report_path


class FileListProvider:
    def list_entries(self) -> List[FilePair]:
        raise NotImplementedError


def load_text_lines(path: str) -> list[str]:
    """Load non-empty content lines, matching the current Snap segmentation."""

    try:
        with open(path, "r", encoding="utf-8-sig") as stream:
            return [line.strip() for line in stream if line.strip()]
    except (FileNotFoundError, OSError):
        return []


def format_markdown_output(lines: list[str]) -> str:
    """Serialize logical reader rows with unambiguous blank separators."""

    return "\n\n".join(lines) + ("\n" if lines else "")


def save_report(report_data: dict, path: str) -> None:
    from dualign.services.report_io import save_report as _save_report

    _save_report(report_data, path)


def load_report(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    from dualign.services.report_io import load_report as _load_report

    return _load_report(path)


def set_ai_review(path: str, status: str, note: str = ""):
    if not os.path.isfile(path):
        return None
    from dualign.services.report_io import set_ai_review as _set_ai_review

    return _set_ai_review(path, status, note)
