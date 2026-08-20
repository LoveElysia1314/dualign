"""Document hashing and internal pair-graph construction helpers.

This module deliberately contains no persisted alignment-file format. The
public durable representation is the JSON work report in ``report_io``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from dualign.models.alignment_pair import AlignmentPair, DocumentReference


def normalize_document_text(text: str) -> str:
    """Normalize transport details without changing Markdown content."""

    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def document_sha256_from_text(text: str) -> str:
    normalized = normalize_document_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def document_sha256(path: str | Path) -> str:
    return document_sha256_from_text(Path(path).read_text(encoding="utf-8-sig"))


def segment_content_lines(text: str) -> tuple[str, ...]:
    normalized = normalize_document_text(text)
    return tuple(line for line in normalized.split("\n") if line.strip())


def relative_document_path(document_path: Path, report_path: Path) -> str:
    try:
        relative = os.path.relpath(
            document_path.resolve(), report_path.parent.resolve()
        )
        return Path(relative).as_posix()
    except ValueError:
        return document_path.resolve().as_posix()


def _filename_token(path: Path, include_parent: bool) -> str:
    stem = path.stem
    if stem.endswith(".source"):
        stem = stem[: -len(".source")]
    elif stem.endswith(".target"):
        stem = stem[: -len(".target")]
    raw = f"{path.parent.name}-{stem}" if include_parent else stem
    return re.sub(r"[^\w.-]+", "-", raw, flags=re.UNICODE).strip("-.") or "document"


def _default_document_ids(path_a: Path, path_b: Path) -> tuple[str, str]:
    same_stem = path_a.stem == path_b.stem
    id_a = _filename_token(path_a, include_parent=same_stem)
    id_b = _filename_token(path_b, include_parent=same_stem)
    if id_a == id_b:
        return f"{id_a}-a", f"{id_b}-b"
    return id_a, id_b


def build_alignment_provenance(
    *,
    tool_version: str = "",
    algorithm_version: str = "",
    alignment_origin: str = "algorithm",
    align_config: object | Mapping[str, Any] | None = None,
    embedding_provider: str = "",
    embedding_model: str = "",
    embedding_instruction: str = "",
) -> dict[str, Any]:
    """Build non-sensitive reproducibility metadata for the editing graph."""

    if align_config is None:
        config_data: dict[str, Any] = {}
    elif isinstance(align_config, Mapping):
        config_data = dict(align_config)
    else:
        config_data = dict(vars(align_config))
    safe_config = {
        str(key): value
        for key, value in config_data.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }
    config_payload = json.dumps(
        safe_config, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    result: dict[str, Any] = {
        "tool": "dualign",
        "alignment_origin": alignment_origin,
    }
    if tool_version:
        result["tool_version"] = tool_version
    if algorithm_version:
        result["algorithm"] = {
            "name": "dualign-pairwise",
            "revision": algorithm_version,
            "configuration_sha256": hashlib.sha256(
                config_payload.encode("utf-8")
            ).hexdigest(),
        }
    if embedding_provider or embedding_model or embedding_instruction:
        embedding: dict[str, str] = {}
        if embedding_provider:
            embedding["provider"] = embedding_provider
        if embedding_model:
            embedding["model"] = embedding_model
        if embedding_instruction:
            embedding["instruction_sha256"] = hashlib.sha256(
                embedding_instruction.encode("utf-8")
            ).hexdigest()
        result["embedding"] = embedding
    return result


def create_alignment_pair(
    *,
    pair_id: str,
    document_a_path: str | Path,
    document_b_path: str | Path,
    alignment_path: str | Path,
    operations: Iterable[tuple[Iterable[int], Iterable[int], float]],
    document_a_id: str = "",
    document_b_id: str = "",
    language_a: str = "",
    language_b: str = "",
    confirmed_operations: Iterable[int] = (),
    tool_version: str = "",
    provenance: Mapping[str, Any] | None = None,
) -> AlignmentPair:
    """Create the in-memory relation graph used by solidification review."""

    path_a = Path(document_a_path)
    path_b = Path(document_b_path)
    report = Path(alignment_path)
    default_a, default_b = _default_document_ids(path_a, path_b)
    ref_a = DocumentReference(
        id=document_a_id or default_a,
        path=relative_document_path(path_a, report),
        language=language_a,
        sha256=document_sha256(path_a),
    )
    ref_b = DocumentReference(
        id=document_b_id or default_b,
        path=relative_document_path(path_b, report),
        language=language_b,
        sha256=document_sha256(path_b),
    )
    pair = AlignmentPair.from_alignment_ops(
        id=pair_id,
        document_a=ref_a,
        document_b=ref_b,
        operations=operations,
        provenance={
            **build_alignment_provenance(tool_version=tool_version),
            **dict(provenance or {}),
        },
    )
    confirmed = set(confirmed_operations)
    links = tuple(
        replace(link, state="confirmed") if index in confirmed else link
        for index, link in enumerate(pair.links)
    )
    return replace(pair, links=links)
