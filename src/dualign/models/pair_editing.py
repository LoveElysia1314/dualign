"""Native editing state for two natural documents and their direct links.

The legacy ``RepairState`` materializes every operation as equal paired rows.
This module keeps document content and alignment relations independent:
documents own stable block IDs, while links refer to those IDs.  Positional
indices are produced only when serializing an :class:`AlignmentPair`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Iterable, Sequence

from dualign.models.alignment_pair import (
    AlignmentLink,
    AlignmentPair,
    AlignmentPairValidationError,
    DocumentReference,
)


def _normalize_text(text: str) -> str:
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _text_hash(text: str) -> str:
    return hashlib.sha256(_normalize_text(text).encode("utf-8")).hexdigest()


def _replacement_lines(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("正文块必须是字符串")
        for line in _normalize_text(value).split("\n"):
            if line.strip():
                result.append(line)
    return tuple(result)


@dataclass(frozen=True)
class EditableDocument:
    """A Markdown document that preserves physical lines and stable block IDs."""

    id: str
    lines: tuple[str, ...]
    block_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        content_count = sum(1 for line in self.lines if line.strip())
        if content_count != len(self.block_ids):
            raise ValueError("block_ids 数量必须与非空物理行数量一致")
        if len(set(self.block_ids)) != len(self.block_ids):
            raise ValueError("文档块 ID 不能重复")

    @classmethod
    def from_text(cls, document_id: str, text: str, side: str) -> "EditableDocument":
        lines = tuple(_normalize_text(text).split("\n"))
        block_ids = tuple(
            f"{side}{index:06d}"
            for index, line in enumerate(
                (line for line in lines if line.strip()), start=1
            )
        )
        return cls(id=document_id, lines=lines, block_ids=block_ids)

    @property
    def blocks(self) -> tuple[str, ...]:
        return tuple(line for line in self.lines if line.strip())

    def render_text(self) -> str:
        return "\n".join(self.lines)

    def block_index(self, block_id: str) -> int:
        try:
            return self.block_ids.index(block_id)
        except ValueError as exc:
            raise ValueError(f"文档 {self.id} 中不存在块 {block_id}") from exc

    def _physical_positions(self) -> tuple[int, ...]:
        return tuple(index for index, line in enumerate(self.lines) if line.strip())

    def _preferred_separator(self) -> tuple[str, ...]:
        positions = self._physical_positions()
        if any(right - left > 1 for left, right in zip(positions, positions[1:])):
            return ("",)
        return ()

    @staticmethod
    def _collapse_blank_runs(lines: Sequence[str]) -> tuple[str, ...]:
        result: list[str] = []
        for line in lines:
            if not line and result and not result[-1]:
                continue
            result.append(line)
        return tuple(result)

    def replace_blocks(
        self,
        selected_ids: Iterable[str],
        replacement: Iterable[str],
        *,
        id_prefix: str,
    ) -> tuple["EditableDocument", tuple[str, ...]]:
        """Replace one contiguous block range and return its resulting IDs."""

        selected = tuple(selected_ids)
        if not selected:
            raise ValueError("replace_blocks 需要至少一个现有块")
        ordinals = tuple(self.block_index(block_id) for block_id in selected)
        expected = tuple(range(min(ordinals), max(ordinals) + 1))
        if ordinals != expected:
            raise ValueError("只能一次替换文档中的连续块")

        new_texts = _replacement_lines(replacement)
        positions = self._physical_positions()
        first_ordinal, last_ordinal = ordinals[0], ordinals[-1]

        if len(new_texts) == len(selected):
            lines = list(self.lines)
            for ordinal, text in zip(ordinals, new_texts):
                lines[positions[ordinal]] = text
            return replace(self, lines=tuple(lines)), selected

        physical_start = positions[first_ordinal]
        physical_end = positions[last_ordinal] + 1
        separator = self._preferred_separator()
        payload: list[str] = []
        for index, text in enumerate(new_texts):
            if index:
                payload.extend(separator)
            payload.append(text)
        lines = self._collapse_blank_runs(
            (*self.lines[:physical_start], *payload, *self.lines[physical_end:])
        )
        new_ids = tuple(
            f"{id_prefix}.{index:03d}" for index in range(1, len(new_texts) + 1)
        )
        block_ids = (
            *self.block_ids[:first_ordinal],
            *new_ids,
            *self.block_ids[last_ordinal + 1 :],
        )
        return replace(self, lines=lines, block_ids=tuple(block_ids)), new_ids

    def insert_blocks_after(
        self,
        after_block_id: str | None,
        values: Iterable[str],
        *,
        id_prefix: str,
    ) -> tuple["EditableDocument", tuple[str, ...]]:
        """Insert blocks at a relation gap while preserving surrounding layout."""

        new_texts = _replacement_lines(values)
        if not new_texts:
            return self, ()
        insert_ordinal = (
            self.block_index(after_block_id) + 1 if after_block_id is not None else 0
        )
        positions = self._physical_positions()
        separator = self._preferred_separator() or (("",) if self.block_ids else ())

        payload: list[str] = []
        for index, text in enumerate(new_texts):
            if index:
                payload.extend(separator)
            payload.append(text)
        if insert_ordinal < len(positions):
            physical_insert = positions[insert_ordinal]
            payload.extend(separator)
        elif positions:
            physical_insert = positions[-1] + 1
            payload = [*separator, *payload]
        else:
            physical_insert = 0

        lines = (
            *self.lines[:physical_insert],
            *payload,
            *self.lines[physical_insert:],
        )
        new_ids = tuple(
            f"{id_prefix}.{index:03d}" for index in range(1, len(new_texts) + 1)
        )
        block_ids = (
            *self.block_ids[:insert_ordinal],
            *new_ids,
            *self.block_ids[insert_ordinal:],
        )
        return replace(self, lines=tuple(lines), block_ids=tuple(block_ids)), new_ids


@dataclass(frozen=True)
class BlockLink:
    id: str
    document_a: tuple[str, ...]
    document_b: tuple[str, ...]
    state: str = "suggested"
    confidence: float | None = None


@dataclass(frozen=True)
class PairEditingState:
    """Immutable native state for content editing and relation editing."""

    pair_id: str
    document_a: EditableDocument
    document_b: EditableDocument
    document_a_ref: DocumentReference
    document_b_ref: DocumentReference
    links: tuple[BlockLink, ...]
    segmentation: str = "content-line"
    provenance: dict | None = None
    workspace: dict | None = None
    history: tuple[dict, ...] = ()
    analysis: dict | None = None

    def __post_init__(self) -> None:
        if len({link.id for link in self.links}) != len(self.links):
            raise ValueError("关系 ID 不能重复")
        available_a = set(self.document_a.block_ids)
        available_b = set(self.document_b.block_ids)
        for link in self.links:
            if not set(link.document_a) <= available_a:
                raise ValueError(f"关系 {link.id} 引用了文档 A 中不存在的块")
            if not set(link.document_b) <= available_b:
                raise ValueError(f"关系 {link.id} 引用了文档 B 中不存在的块")

    @classmethod
    def from_alignment_pair(
        cls, pair: AlignmentPair, document_a_text: str, document_b_text: str
    ) -> "PairEditingState":
        document_a = EditableDocument.from_text(
            pair.document_a.id, document_a_text, "a"
        )
        document_b = EditableDocument.from_text(
            pair.document_b.id, document_b_text, "b"
        )
        links: list[BlockLink] = []
        for link in pair.links:
            try:
                links.append(
                    BlockLink(
                        id=link.id,
                        document_a=tuple(
                            document_a.block_ids[index - 1] for index in link.document_a
                        ),
                        document_b=tuple(
                            document_b.block_ids[index - 1] for index in link.document_b
                        ),
                        state=link.state,
                        confidence=link.confidence,
                    )
                )
            except IndexError as exc:
                raise AlignmentPairValidationError(
                    f"关系 {link.id} 引用了不存在的正文块"
                ) from exc
        return cls(
            pair_id=pair.id,
            document_a=document_a,
            document_b=document_b,
            document_a_ref=pair.document_a,
            document_b_ref=pair.document_b,
            links=tuple(links),
            segmentation=pair.segmentation,
            provenance=dict(pair.provenance),
            workspace=dict(pair.workspace),
            history=tuple(dict(item) for item in pair.history),
            analysis=dict(pair.analysis),
        )

    def link(self, link_id: str) -> BlockLink:
        for link in self.links:
            if link.id == link_id:
                return link
        raise ValueError(f"不存在关系 {link_id}")

    def confirm_link(self, link_id: str) -> "PairEditingState":
        self.link(link_id)
        return replace(
            self,
            links=tuple(
                replace(link, state="confirmed") if link.id == link_id else link
                for link in self.links
            ),
        )

    def reject_link(self, link_id: str) -> "PairEditingState":
        self.link(link_id)
        return replace(
            self,
            links=tuple(
                replace(link, state="rejected") if link.id == link_id else link
                for link in self.links
            ),
        )

    def _previous_block(self, link_id: str, side: str) -> str | None:
        previous: str | None = None
        for link in self.links:
            if link.id == link_id:
                return previous
            values = link.document_a if side == "a" else link.document_b
            if values:
                previous = values[-1]
        raise ValueError(f"不存在关系 {link_id}")

    def edit_link_content(
        self,
        link_id: str,
        *,
        document_a: Iterable[str] | None = None,
        document_b: Iterable[str] | None = None,
    ) -> "PairEditingState":
        """Edit either side without forcing both sides to have equal counts."""

        target = self.link(link_id)
        new_document_a = self.document_a
        new_document_b = self.document_b
        ids_a = target.document_a
        ids_b = target.document_b

        if document_a is not None:
            values_a = tuple(document_a)
            if ids_a:
                new_document_a, ids_a = new_document_a.replace_blocks(
                    ids_a, values_a, id_prefix=f"{link_id}.a"
                )
            else:
                new_document_a, ids_a = new_document_a.insert_blocks_after(
                    self._previous_block(link_id, "a"),
                    values_a,
                    id_prefix=f"{link_id}.a",
                )
        if document_b is not None:
            values_b = tuple(document_b)
            if ids_b:
                new_document_b, ids_b = new_document_b.replace_blocks(
                    ids_b, values_b, id_prefix=f"{link_id}.b"
                )
            else:
                new_document_b, ids_b = new_document_b.insert_blocks_after(
                    self._previous_block(link_id, "b"),
                    values_b,
                    id_prefix=f"{link_id}.b",
                )

        updated = replace(target, document_a=ids_a, document_b=ids_b)
        return replace(
            self,
            document_a=new_document_a,
            document_b=new_document_b,
            links=tuple(updated if link.id == link_id else link for link in self.links),
        )

    def merge_links(self, link_ids: Sequence[str]) -> "PairEditingState":
        selected = tuple(link_ids)
        if len(selected) < 2:
            raise ValueError("合并关系至少需要两个链接")
        positions = tuple(
            next(index for index, link in enumerate(self.links) if link.id == link_id)
            for link_id in selected
        )
        if positions != tuple(range(min(positions), max(positions) + 1)):
            raise ValueError("只能合并连续关系")
        chosen = [self.links[index] for index in positions]

        def ordered_unique(values: Iterable[str], document: EditableDocument):
            selected_ids = set(values)
            return tuple(
                block_id for block_id in document.block_ids if block_id in selected_ids
            )

        merged = BlockLink(
            id=chosen[0].id,
            document_a=ordered_unique(
                (value for link in chosen for value in link.document_a), self.document_a
            ),
            document_b=ordered_unique(
                (value for link in chosen for value in link.document_b), self.document_b
            ),
            state=(
                "confirmed"
                if all(link.state == "confirmed" for link in chosen)
                else "suggested"
            ),
            confidence=min(
                (link.confidence for link in chosen if link.confidence is not None),
                default=None,
            ),
        )
        first = positions[0]
        selected_set = set(selected)
        links = [link for link in self.links if link.id not in selected_set]
        links.insert(first, merged)
        return replace(self, links=tuple(links))

    def split_link(
        self,
        link_id: str,
        groups: Sequence[tuple[Sequence[str], Sequence[str]]],
    ) -> "PairEditingState":
        target = self.link(link_id)
        if len(groups) < 2:
            raise ValueError("拆分关系至少需要两个分组")
        flat_a = tuple(value for side_a, _side_b in groups for value in side_a)
        flat_b = tuple(value for _side_a, side_b in groups for value in side_b)
        if set(flat_a) != set(target.document_a) or len(flat_a) != len(set(flat_a)):
            raise ValueError("拆分分组必须恰好覆盖原关系的文档 A 块")
        if set(flat_b) != set(target.document_b) or len(flat_b) != len(set(flat_b)):
            raise ValueError("拆分分组必须恰好覆盖原关系的文档 B 块")
        replacements = tuple(
            BlockLink(
                id=f"{link_id}.{index}",
                document_a=tuple(side_a),
                document_b=tuple(side_b),
                state=target.state,
                confidence=target.confidence,
            )
            for index, (side_a, side_b) in enumerate(groups, start=1)
        )
        position = self.links.index(target)
        links = (*self.links[:position], *replacements, *self.links[position + 1 :])
        return replace(self, links=tuple(links))

    def to_alignment_pair(self) -> AlignmentPair:
        positions_a = {
            block_id: index
            for index, block_id in enumerate(self.document_a.block_ids, start=1)
        }
        positions_b = {
            block_id: index
            for index, block_id in enumerate(self.document_b.block_ids, start=1)
        }
        links = tuple(
            AlignmentLink(
                id=link.id,
                document_a=tuple(positions_a[value] for value in link.document_a),
                document_b=tuple(positions_b[value] for value in link.document_b),
                state=link.state,
                confidence=link.confidence,
            )
            for link in self.links
        )
        return AlignmentPair(
            id=self.pair_id,
            document_a=replace(
                self.document_a_ref,
                sha256=_text_hash(self.document_a.render_text()),
            ),
            document_b=replace(
                self.document_b_ref,
                sha256=_text_hash(self.document_b.render_text()),
            ),
            links=links,
            segmentation=self.segmentation,
            provenance=dict(self.provenance or {}),
            workspace=dict(self.workspace or {}),
            history=tuple(dict(item) for item in self.history),
            analysis=dict(self.analysis or {}),
        )
