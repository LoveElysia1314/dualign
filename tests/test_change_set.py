from dualign.models.action import RepairAction
from dualign.models.alignment_pair import (
    AlignmentLink,
    AlignmentPair,
    DocumentReference,
)
from dualign.models.pair_editing import PairEditingState
from dualign.services.change_set import build_pair_change_set


def _baseline():
    pair = AlignmentPair(
        id="pair",
        document_a=DocumentReference("a", "a.md"),
        document_b=DocumentReference("b", "b.md"),
        links=(
            AlignmentLink("L000001", (1, 2), (1,), state="suggested"),
            AlignmentLink("L000002", (3,), (2,), state="suggested"),
        ),
    )
    return PairEditingState.from_alignment_pair(pair, "甲\n乙\n丙\n", "A\nB\n")


def test_edit_change_set_exposes_diffs():
    action = RepairAction.make_edit(
        0,
        source="user",
        new_src_lines=["甲。", "乙。"],
        new_tgt_lines=["A edited"],
    )

    changes = build_pair_change_set(_baseline(), [action])

    assert changes.has_content_changes
    assert changes.can_apply
    assert "甲。" in changes.document_a_diff()


def test_automatic_edit_is_applicable_without_manual_ok():
    automatic = RepairAction.make_edit(
        0,
        source="auto",
        new_src_lines=["甲自动", "乙自动"],
        new_tgt_lines=["A auto"],
    )

    changes = build_pair_change_set(_baseline(), [automatic])

    assert changes.has_content_changes
    assert changes.can_apply
    assert "甲自动" in changes.document_a_diff()


def test_relation_only_change_can_be_saved_without_source_application():
    approval = RepairAction.make_ok(0)
    approval.source = "user"

    changes = build_pair_change_set(_baseline(), [approval])

    assert not changes.has_content_changes
    assert changes.relation_changed
    assert not changes.can_apply
    assert changes.relation_diff()
