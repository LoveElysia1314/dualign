from dualign.models.action import RepairAction
from dualign.models.alignment_pair import (
    AlignmentLink,
    AlignmentPair,
    DocumentReference,
)
from dualign.models.pair_editing import PairEditingState
from dualign.services.pair_editing_adapter import apply_repair_log_to_pair_state


def _base():
    pair = AlignmentPair(
        id="pair",
        document_a=DocumentReference("a", "a.md"),
        document_b=DocumentReference("b", "b.md"),
        links=(
            AlignmentLink("L000001", (1, 2), (1,)),
            AlignmentLink("L000002", (3,), (2,)),
            AlignmentLink("L000003", (4,), ()),
        ),
    )
    return PairEditingState.from_alignment_pair(
        pair, "甲\n\n乙\n\n丙\n\n丁\n", "A\n\nB\n"
    )


def test_adapter_keeps_legacy_merge_structural_and_applies_explicit_edits():
    state = apply_repair_log_to_pair_state(
        _base(),
        [
            RepairAction.make_merge(0),
            RepairAction.make_edit(
                0,
                new_src_lines=["甲校订", "乙校订"],
                new_tgt_lines=["A edited"],
            ),
            RepairAction.make_ok(0),
        ],
    )

    assert state.document_a.blocks[:2] == ("甲校订", "乙校订")
    assert state.document_b.blocks[0] == "A edited"
    assert state.links[0].document_a == ("a000001", "a000002")
    assert state.links[0].document_b == ("b000001",)
    assert state.links[0].state == "confirmed"


def test_adapter_rejects_link_without_deleting_canonical_text():
    base = _base()
    state = apply_repair_log_to_pair_state(base, [RepairAction.make_delete(2)])

    assert state.links[2].state == "rejected"
    assert state.document_a.render_text() == base.document_a.render_text()


def test_adapter_merges_multi_snap_edit_into_one_native_relation():
    action = RepairAction.make_edit(
        0,
        orig_snaps=[0, 1],
        new_src_lines=["甲乙丙"],
        new_tgt_lines=["AB"],
    )

    state = apply_repair_log_to_pair_state(_base(), [action])

    assert [link.id for link in state.links] == ["L000001", "L000003"]
    assert state.document_a.blocks == ("甲乙丙", "丁")
    assert state.document_b.blocks == ("AB",)
    assert state.to_alignment_pair().links[0].document_a == (1,)
    assert state.to_alignment_pair().links[0].document_b == (1,)


def test_adapter_can_clear_one_side_without_treating_it_as_unchanged():
    action = RepairAction.make_edit(
        0,
        new_src_lines=["甲", "乙"],
        new_tgt_lines=[],
    )

    state = apply_repair_log_to_pair_state(_base(), [action])

    assert state.links[0].document_b == ()
    assert state.document_b.blocks == ("B",)
