"""Internal pairwise alignment data model.

An :class:`AlignmentPair` connects exactly two text documents.  The neutral
``a`` and ``b`` positions are local to one alignment file; they do not imply
source/translation direction or textual authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

CONTENT_LINE_SEGMENTATION = "content-line"
LINK_STATES = frozenset({"suggested", "confirmed", "rejected"})


class AlignmentPairValidationError(ValueError):
    """Raised when an internal alignment graph is invalid."""


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AlignmentPairValidationError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _indices(value: Iterable[int], field_name: str) -> tuple[int, ...]:
    try:
        result = tuple(value)
    except TypeError as exc:
        raise AlignmentPairValidationError(f"{field_name} 必须是整数数组") from exc
    if any(isinstance(index, bool) or not isinstance(index, int) for index in result):
        raise AlignmentPairValidationError(f"{field_name} 只能包含整数")
    if any(index < 1 for index in result):
        raise AlignmentPairValidationError(f"{field_name} 的块编号必须从 1 开始")
    if tuple(sorted(set(result))) != result:
        raise AlignmentPairValidationError(f"{field_name} 必须严格递增且不能重复")
    return result


@dataclass(frozen=True)
class DocumentReference:
    """A concrete text document referenced by one alignment pair."""

    id: str
    path: str
    language: str = ""
    sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "document.id"))
        object.__setattr__(self, "path", _required_text(self.path, "document.path"))
        if not isinstance(self.language, str):
            raise AlignmentPairValidationError("document.language 必须是字符串")
        if not isinstance(self.sha256, str):
            raise AlignmentPairValidationError("document.sha256 必须是字符串")
        digest = self.sha256.lower()
        if digest and (
            len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            raise AlignmentPairValidationError(
                "document.sha256 必须是 64 位十六进制摘要"
            )
        object.__setattr__(self, "sha256", digest)


@dataclass(frozen=True)
class AlignmentLink:
    """A direct correspondence between blocks in document A and document B."""

    id: str
    document_a: tuple[int, ...]
    document_b: tuple[int, ...]
    state: str = "confirmed"
    confidence: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "link.id"))
        object.__setattr__(
            self, "document_a", _indices(self.document_a, f"link {self.id}.a")
        )
        object.__setattr__(
            self, "document_b", _indices(self.document_b, f"link {self.id}.b")
        )
        if not self.document_a and not self.document_b:
            raise AlignmentPairValidationError(f"link {self.id} 的 a 和 b 不能同时为空")
        if self.state not in LINK_STATES:
            allowed = ", ".join(sorted(LINK_STATES))
            raise AlignmentPairValidationError(
                f"link {self.id}.state 必须是 {allowed} 之一"
            )
        if self.confidence is not None:
            confidence = float(self.confidence)
            if not 0.0 <= confidence <= 1.0:
                raise AlignmentPairValidationError(
                    f"link {self.id}.confidence 必须位于 0 到 1 之间"
                )
            object.__setattr__(self, "confidence", confidence)


@dataclass(frozen=True)
class AlignmentPair:
    """An in-memory editing graph between exactly two documents."""

    id: str
    document_a: DocumentReference
    document_b: DocumentReference
    links: tuple[AlignmentLink, ...]
    segmentation: str = CONTENT_LINE_SEGMENTATION
    provenance: Mapping[str, Any] = field(default_factory=dict)
    workspace: Mapping[str, Any] = field(default_factory=dict)
    history: tuple[Mapping[str, Any], ...] = ()
    analysis: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "alignment.id"))
        if not isinstance(self.document_a, DocumentReference) or not isinstance(
            self.document_b, DocumentReference
        ):
            raise AlignmentPairValidationError("alignment 必须恰好引用文档 a 和 b")
        if self.document_a.id == self.document_b.id:
            raise AlignmentPairValidationError("文档 a 和 b 必须使用不同 ID")
        object.__setattr__(
            self, "segmentation", _required_text(self.segmentation, "segmentation.kind")
        )
        object.__setattr__(self, "links", tuple(self.links))
        if not all(isinstance(link, AlignmentLink) for link in self.links):
            raise AlignmentPairValidationError("links 只能包含 AlignmentLink")
        if len({link.id for link in self.links}) != len(self.links):
            raise AlignmentPairValidationError("link.id 不能重复")
        if not isinstance(self.provenance, Mapping):
            raise AlignmentPairValidationError("provenance 必须是映射")
        object.__setattr__(self, "provenance", dict(self.provenance))
        if not isinstance(self.workspace, Mapping):
            raise AlignmentPairValidationError("workspace 必须是映射")
        object.__setattr__(self, "workspace", dict(self.workspace))
        if not isinstance(self.history, (list, tuple)) or not all(
            isinstance(item, Mapping) for item in self.history
        ):
            raise AlignmentPairValidationError("history 必须是映射数组")
        object.__setattr__(self, "history", tuple(dict(item) for item in self.history))
        if not isinstance(self.analysis, Mapping):
            raise AlignmentPairValidationError("analysis 必须是映射")
        object.__setattr__(self, "analysis", dict(self.analysis))
        self._validate_confirmed_coverage()

    def _validate_confirmed_coverage(self) -> None:
        used_a: dict[int, str] = {}
        used_b: dict[int, str] = {}
        for link in self.links:
            if link.state != "confirmed":
                continue
            for side, indices, used in (
                ("a", link.document_a, used_a),
                ("b", link.document_b, used_b),
            ):
                for index in indices:
                    previous = used.get(index)
                    if previous is not None:
                        raise AlignmentPairValidationError(
                            f"文档 {side} 的块 {index} 同时出现在已确认链接 "
                            f"{previous} 和 {link.id} 中"
                        )
                    used[index] = link.id

    @classmethod
    def from_alignment_ops(
        cls,
        *,
        id: str,
        document_a: DocumentReference,
        document_b: DocumentReference,
        operations: Iterable[tuple[Iterable[int], Iterable[int], float]],
        segmentation: str = CONTENT_LINE_SEGMENTATION,
        provenance: Mapping[str, Any] | None = None,
        state: str = "suggested",
    ) -> "AlignmentPair":
        """Build an editing graph from Dualign's zero-based alignment operations."""

        links = []
        for index, (side_a, side_b, score) in enumerate(operations, start=1):
            numeric_score = float(score)
            confidence = numeric_score if 0.0 <= numeric_score <= 1.0 else None
            links.append(
                AlignmentLink(
                    id=f"L{index:06d}",
                    document_a=tuple(value + 1 for value in side_a),
                    document_b=tuple(value + 1 for value in side_b),
                    confidence=confidence,
                    state=state,
                )
            )
        return cls(
            id=id,
            document_a=document_a,
            document_b=document_b,
            links=tuple(links),
            segmentation=segmentation,
            provenance=provenance or {},
        )
