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


def test_manual_unequal_edit_is_immediately_applicable():
    action = RepairAction.make_edit(
        0,
        source="user",
        new_src_lines=["甲。", "乙。"],
        new_tgt_lines=["A edited"],
    )
    action.data["approvals"] = {"manual"}

    changes = build_pair_change_set(_baseline(), [action])

    assert changes.has_content_changes
    assert changes.can_apply
    assert changes.unreviewed_content_operations == ()
    assert "甲。" in changes.document_a_diff()


def test_automatic_content_edit_requires_later_manual_ok():
    automatic = RepairAction.make_edit(
        0,
        source="auto",
        new_src_lines=["甲自动", "乙自动"],
        new_tgt_lines=["A auto"],
    )
    automatic.data["approvals"] = {"auto"}
    blocked = build_pair_change_set(_baseline(), [automatic])

    assert blocked.unreviewed_content_operations == (0,)
    assert not blocked.can_apply

    approval = RepairAction.make_ok(0)
    approval.source = "user"
    approval.data["approvals"] = {"manual"}
    reviewed = build_pair_change_set(_baseline(), [automatic, approval])

    assert reviewed.can_apply


def test_relation_only_change_can_be_saved_without_source_application():
    approval = RepairAction.make_ok(0)
    approval.source = "user"
    approval.data["approvals"] = {"manual"}

    changes = build_pair_change_set(_baseline(), [approval])

    assert not changes.has_content_changes
    assert changes.relation_changed
    assert not changes.can_apply
    assert changes.relation_diff()
