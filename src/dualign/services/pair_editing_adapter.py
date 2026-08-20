"""Compatibility bridge from legacy RepairAction logs to native pair state."""

from __future__ import annotations

from collections.abc import Iterable

from dualign.models.action import RepairAction
from dualign.models.pair_editing import PairEditingState


def link_id_for_operation(operation_index: int) -> str:
    return f"L{operation_index + 1:06d}"


def _existing_link_ids(
    state: PairEditingState, operation_indices: Iterable[int]
) -> list[str]:
    available = {link.id for link in state.links}
    return [
        link_id_for_operation(int(index))
        for index in operation_indices
        if link_id_for_operation(int(index)) in available
    ]


def apply_repair_log_to_pair_state(
    base: PairEditingState, repair_log: Iterable[RepairAction]
) -> PairEditingState:
    """Replay accepted legacy actions without reviving equal-row semantics.

    ``edit`` and ``split`` carry explicit replacement text and therefore update
    natural documents.  Legacy ``merge`` and placeholders only existed to
    materialize equal rows, so native state leaves document content untouched.
    ``delete`` rejects a relation but does not silently delete canonical text.
    """

    state = base
    for action in repair_log:
        link_id = link_id_for_operation(action.op_index)
        available = {link.id for link in state.links}

        if action.kind in {"edit", "split"}:
            original_operations = action.data.get("orig_snaps") or [action.op_index]
            related = _existing_link_ids(state, original_operations)
            if len(related) > 1:
                state = state.merge_links(related)
                link_id = related[0]
            elif related:
                link_id = related[0]
            if link_id not in {link.id for link in state.links}:
                continue
            replacement_a = (
                action.data["new_src_lines"] if "new_src_lines" in action.data else None
            )
            replacement_b = (
                action.data["new_tgt_lines"] if "new_tgt_lines" in action.data else None
            )
            state = state.edit_link_content(
                link_id,
                document_a=replacement_a,
                document_b=replacement_b,
            )
        elif action.kind == "ok" and link_id in available:
            state = state.confirm_link(link_id)
        elif action.kind == "delete" and link_id in available:
            state = state.reject_link(link_id)
        # merge / placeholder / flag are legacy presentation or review state;
        # they intentionally do not rewrite native document content.
    return state
