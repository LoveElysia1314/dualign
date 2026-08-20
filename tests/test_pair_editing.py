import pytest

from dualign.models.alignment_pair import (
    AlignmentLink,
    AlignmentPair,
    DocumentReference,
)
from dualign.models.pair_editing import EditableDocument, PairEditingState
from dualign.services.alignment_io import document_sha256_from_text


def _pair() -> AlignmentPair:
    return AlignmentPair(
        id="sample",
        document_a=DocumentReference("a", "a.md"),
        document_b=DocumentReference("b", "b.md"),
        links=(
            AlignmentLink("L1", (1,), (1,), state="confirmed", confidence=0.9),
            AlignmentLink("L2", (2, 3), (2,), confidence=0.8),
            AlignmentLink("L3", (4,), (), confidence=0.7),
        ),
    )


def _state() -> PairEditingState:
    return PairEditingState.from_alignment_pair(
        _pair(), "甲\n\n乙\n\n丙\n\n丁\n", "A\n\nB\n"
    )


def test_round_trip_preserves_natural_documents_and_relations():
    state = _state()

    assert state.document_a.render_text() == "甲\n\n乙\n\n丙\n\n丁\n"
    assert state.document_b.render_text() == "A\n\nB\n"
    assert state.to_alignment_pair() == AlignmentPair(
        id="sample",
        document_a=DocumentReference(
            "a", "a.md", sha256=document_sha256_from_text("甲\n\n乙\n\n丙\n\n丁\n")
        ),
        document_b=DocumentReference(
            "b", "b.md", sha256=document_sha256_from_text("A\n\nB\n")
        ),
        links=_pair().links,
    )


def test_editing_two_to_one_does_not_force_equal_block_counts():
    state = _state()
    edited = state.edit_link_content(
        "L2", document_a=["乙（校订）", "丙（校订）"], document_b=["B edited"]
    )

    assert edited.document_a.blocks == ("甲", "乙（校订）", "丙（校订）", "丁")
    assert edited.document_b.blocks == ("A", "B edited")
    relation = edited.to_alignment_pair().links[1]
    assert relation.document_a == (2, 3)
    assert relation.document_b == (2,)


def test_structural_content_edit_reindexes_following_links():
    state = _state()
    edited = state.edit_link_content("L2", document_a=["乙丙合并"])
    pair = edited.to_alignment_pair()

    assert edited.document_a.blocks == ("甲", "乙丙合并", "丁")
    assert pair.links[1].document_a == (2,)
    assert pair.links[2].document_a == (3,)
    assert "甲\n\n乙丙合并\n\n丁" in edited.document_a.render_text()


def test_filling_a_gap_inserts_only_the_missing_side():
    state = _state()
    edited = state.edit_link_content("L3", document_b=["D"])

    assert edited.document_b.blocks == ("A", "B", "D")
    assert edited.to_alignment_pair().links[2].document_b == (3,)
    assert edited.document_a.blocks == state.document_a.blocks


def test_confirm_reject_merge_and_split_change_relations_not_text():
    state = _state()
    original_a = state.document_a.render_text()
    original_b = state.document_b.render_text()

    confirmed = state.confirm_link("L2")
    rejected = confirmed.reject_link("L3")
    merged = rejected.merge_links(("L1", "L2"))
    merged_link = merged.links[0]
    split = merged.split_link(
        "L1",
        (
            ((merged_link.document_a[0],), (merged_link.document_b[0],)),
            (merged_link.document_a[1:], merged_link.document_b[1:]),
        ),
    )

    assert [link.id for link in split.links] == ["L1.1", "L1.2", "L3"]
    assert split.document_a.render_text() == original_a
    assert split.document_b.render_text() == original_b


def test_replace_blocks_requires_a_contiguous_range():
    document = EditableDocument.from_text("a", "一\n二\n三\n", "a")

    with pytest.raises(ValueError, match="连续块"):
        document.replace_blocks(
            (document.block_ids[0], document.block_ids[2]),
            ("新一", "新三"),
            id_prefix="edit",
        )
