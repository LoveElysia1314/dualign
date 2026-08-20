"""Selective solidification of report-backed edits into natural documents.

Solidification is deliberately separate from saving a work report.  A policy
chooses which effects become the new document baseline; unselected effects are
rebased onto the rebuilt alignment snapshot and remain in ``repair_log``.
"""

from __future__ import annotations

import json
import hashlib
import tomllib
from dataclasses import dataclass
import difflib
from pathlib import Path
from typing import Iterable, Mapping

from dualign.core import _smart_join_lines
from dualign.models.action import RepairAction
from dualign.models.pair_editing import PairEditingState
from dualign.services.alignment_io import create_alignment_pair
from dualign.services.pair_save import PairSaveResult, save_pair_transaction
from dualign.services.report_io import (
    ReportError,
    load_report,
    operations_from_report,
    report_matches_documents,
)

SOLIDIFY_TYPES = (
    "merge_a",
    "split_a",
    "edit_a",
    "merge_b",
    "split_b",
    "edit_b",
    "delete_pair",
)

SOLIDIFY_TYPE_LABELS = {
    "merge_a": "文档 A 合并",
    "split_a": "文档 A 拆分",
    "edit_a": "文档 A 校订",
    "merge_b": "文档 B 合并",
    "split_b": "文档 B 拆分",
    "edit_b": "文档 B 校订",
    "delete_pair": "删除文本对（双侧）",
}

SOLIDIFY_PRESETS = {
    "edits": frozenset({"edit_a", "edit_b"}),
    "line-aligned": frozenset(SOLIDIFY_TYPES),
    "document-a": frozenset({"merge_a", "split_a", "edit_a"}),
    "document-b": frozenset({"merge_b", "split_b", "edit_b"}),
    "none": frozenset(),
}

# 出厂默认：仅校订（双侧）+ 译文拆分。原文重组/删除等破坏性效果需用户显式启用。
DEFAULT_SOLIDIFY_TYPES = frozenset({"edit_a", "edit_b", "split_b"})


@dataclass(frozen=True)
class SolidifyPolicy:
    """The independently selectable effects that may change documents."""

    enabled: frozenset[str]

    def __post_init__(self) -> None:
        unknown = set(self.enabled) - set(SOLIDIFY_TYPES)
        if unknown:
            raise ValueError("未知固化类型: " + "、".join(sorted(unknown)))

    @classmethod
    def from_preset(cls, name: str) -> "SolidifyPolicy":
        try:
            return cls(SOLIDIFY_PRESETS[name])
        except KeyError as exc:
            raise ValueError(f"未知固化预设: {name}") from exc

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "SolidifyPolicy":
        preset = str(value.get("preset") or "none")
        enabled = set(cls.from_preset(preset).enabled)
        include = value.get("include", ())
        exclude = value.get("exclude", ())
        if isinstance(include, str) or isinstance(exclude, str):
            raise ValueError("include/exclude 必须是字符串数组")
        enabled.update(str(item) for item in include or ())
        enabled.difference_update(str(item) for item in exclude or ())
        return cls(frozenset(enabled))

    def includes(self, effect: str) -> bool:
        return effect in self.enabled

    def to_dict(self) -> dict[str, list[str]]:
        return {"include": [key for key in SOLIDIFY_TYPES if key in self.enabled]}


def load_solidify_policy(path: str | Path) -> SolidifyPolicy:
    """Load a JSON or TOML policy file."""

    source = Path(path)
    if source.suffix.lower() == ".toml":
        data = tomllib.loads(source.read_text(encoding="utf-8"))
    elif source.suffix.lower() == ".json":
        data = json.loads(source.read_text(encoding="utf-8"))
    else:
        raise ValueError("固化配置仅支持 .toml 或 .json")
    if not isinstance(data, dict):
        raise ValueError("固化配置必须是对象/表")
    if isinstance(data.get("solidify"), dict):
        data = data["solidify"]
    return SolidifyPolicy.from_mapping(data)


def _format_range_unified(start: int, stop: int) -> str:
    beginning = start + 1
    length = stop - start
    if length == 1:
        return str(beginning)
    if not length:
        beginning -= 1
    return f"{beginning},{length}"


def _diff(before: str, after: str, name: str) -> str:
    """行级 unified diff（autojunk=False）。

    difflib 默认的 autojunk 启发式在 >200 行的文档中会把重复行（如
    「※　　※　　※」、"............"、被修改行本身）当作垃圾排除，导致
    本应错位的少量变更被展开成整段删除/插入。关闭后只报告真实差异。
    """
    if before == after:
        return ""
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    out = [f"--- {name}（当前）\n", f"+++ {name}（固化后）\n"]
    for group in matcher.get_grouped_opcodes(3):
        first, last = group[0], group[-1]
        out.append(
            f"@@ -{_format_range_unified(first[1], last[2])} "
            f"+{_format_range_unified(first[3], last[4])} @@\n"
        )
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                out.extend(" " + line for line in before_lines[i1:i2])
            elif tag in {"replace", "delete"}:
                out.extend("-" + line for line in before_lines[i1:i2])
            if tag in {"replace", "insert"}:
                out.extend("+" + line for line in after_lines[j1:j2])
    return "".join(out)


def _action_copy(
    action: RepairAction,
    *,
    op_index: int,
    data: Mapping[str, object] | None = None,
) -> RepairAction:
    return RepairAction(
        op_index=op_index,
        kind=action.kind,
        sub_count=action.sub_count,
        source=action.source,
        data=dict(action.data if data is None else data),
        timestamp=action.timestamp,
    )


def _action_operations(action: RepairAction) -> tuple[int, ...]:
    raw = action.data.get("orig_snaps") or [action.op_index]
    result: list[int] = []
    for value in raw:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index not in result:
            result.append(index)
    return tuple(result or [action.op_index])


def _ordered_block_ids(state: PairEditingState, link_ids: Iterable[str], side: str):
    selected_links = {link_id for link_id in link_ids}
    values = {
        block_id
        for link in state.links
        if link.id in selected_links
        for block_id in (link.document_a if side == "a" else link.document_b)
    }
    document = state.document_a if side == "a" else state.document_b
    return tuple(block_id for block_id in document.block_ids if block_id in values)


def _block_texts(state: PairEditingState, block_ids: Iterable[str], side: str):
    document = state.document_a if side == "a" else state.document_b
    by_id = dict(zip(document.block_ids, document.blocks))
    return [by_id[block_id] for block_id in block_ids]


def _replacement_count(values: object) -> int:
    """Count non-empty physical blocks using EditableDocument semantics."""

    if not isinstance(values, (list, tuple)):
        return 0
    return sum(
        1
        for value in values
        if isinstance(value, str)
        for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip()
    )


@dataclass(frozen=True)
class SolidificationPlan:
    baseline: PairEditingState
    solidified: PairEditingState
    policy: SolidifyPolicy
    original_actions: tuple[RepairAction, ...]
    remaining_actions: tuple[RepairAction, ...]
    applied: tuple[dict, ...]

    @property
    def document_a_changed(self) -> bool:
        return (
            self.baseline.document_a.render_text()
            != self.solidified.document_a.render_text()
        )

    @property
    def document_b_changed(self) -> bool:
        return (
            self.baseline.document_b.render_text()
            != self.solidified.document_b.render_text()
        )

    @property
    def has_changes(self) -> bool:
        return self.document_a_changed or self.document_b_changed

    def document_a_diff(self) -> str:
        return _diff(
            self.baseline.document_a.render_text(),
            self.solidified.document_a.render_text(),
            "文档 A",
        )

    def document_b_diff(self) -> str:
        return _diff(
            self.baseline.document_b.render_text(),
            self.solidified.document_b.render_text(),
            "文档 B",
        )


@dataclass(frozen=True)
class _PendingAction:
    action: RepairAction
    link_ids: tuple[str, ...]


def build_solidification_plan(
    baseline: PairEditingState,
    repair_log: Iterable[RepairAction],
    policy: SolidifyPolicy,
) -> SolidificationPlan:
    """Apply selected effects and re-anchor everything that remains."""

    state = baseline
    actions = tuple(repair_log)
    old_to_link = {index: link.id for index, link in enumerate(state.links)}
    pending: list[_PendingAction] = []
    applied: list[dict] = []

    def links_for(action: RepairAction) -> tuple[str, ...]:
        result: list[str] = []
        for operation in _action_operations(action):
            link_id = old_to_link.get(operation)
            if link_id and link_id not in result:
                result.append(link_id)
        return tuple(result)

    def merge_links(link_ids: tuple[str, ...], operations: tuple[int, ...]) -> str:
        nonlocal state
        available = {link.id for link in state.links}
        existing = tuple(link_id for link_id in link_ids if link_id in available)
        if not existing:
            raise ValueError("固化操作引用的对齐关系已不存在")
        anchor = existing[0]
        if len(existing) > 1:
            state = state.merge_links(existing)
        for operation in operations:
            old_to_link[operation] = anchor
        return anchor

    for action in actions:
        operations = _action_operations(action)
        link_ids = links_for(action)
        if not link_ids:
            continue

        if action.kind == "merge":
            ids_a = _ordered_block_ids(state, link_ids, "a")
            ids_b = _ordered_block_ids(state, link_ids, "b")
            effects = {
                side: f"merge_{side}"
                for side, ids in (("a", ids_a), ("b", ids_b))
                if len(ids) > 1
            }
            selected = {
                side for side, effect in effects.items() if policy.includes(effect)
            }
            # An N:M structural merge is one semantic decision.  Applying only
            # one side would silently turn it into a different operation.
            if set(effects) == {"a", "b"} and selected != {"a", "b"}:
                pending.append(_PendingAction(action, link_ids))
                continue
            if not selected:
                pending.append(_PendingAction(action, link_ids))
                continue
            anchor = merge_links(link_ids, operations)
            kwargs = {}
            if "a" in selected:
                kwargs["document_a"] = [
                    _smart_join_lines(_block_texts(state, ids_a, "a"))
                ]
            if "b" in selected:
                kwargs["document_b"] = [
                    _smart_join_lines(_block_texts(state, ids_b, "b"))
                ]
            state = state.edit_link_content(anchor, **kwargs)
            applied.append(
                {
                    "action": action.to_dict(),
                    "effects": sorted(effects[s] for s in selected),
                }
            )
            remaining_sides = set(effects) - selected
            if remaining_sides:
                residual = _action_copy(action, op_index=0, data={})
                pending.append(_PendingAction(residual, (anchor,)))
            continue

        if action.kind == "split":
            raw_side = str(action.data.get("side") or "")
            ids_by_side = {
                "a": _ordered_block_ids(state, link_ids, "a"),
                "b": _ordered_block_ids(state, link_ids, "b"),
            }
            keys = {"a": "new_src_lines", "b": "new_tgt_lines"}
            affected = {
                side
                for side in ("a", "b")
                if _replacement_count(action.data.get(keys[side]))
                > len(ids_by_side[side])
            }
            if not affected:
                affected = {"a" if raw_side in {"a", "src", "source"} else "b"}
            selected = {side for side in affected if policy.includes(f"split_{side}")}
            # As with merge, a two-sided structural split must be committed as
            # a unit or remain completely represented by the repair action.
            if selected != affected:
                pending.append(_PendingAction(action, link_ids))
                continue
            anchor = merge_links(link_ids, operations)
            kwargs = {
                "document_a" if side == "a" else "document_b": list(
                    action.data.get(keys[side]) or ()
                )
                for side in affected
            }
            state = state.edit_link_content(anchor, **kwargs)
            applied.append(
                {
                    "action": action.to_dict(),
                    "effects": sorted(f"split_{side}" for side in affected),
                }
            )
            continue

        if action.kind == "edit":
            candidates = {
                "a": ("edit_a", "new_src_lines", "document_a"),
                "b": ("edit_b", "new_tgt_lines", "document_b"),
            }
            present = {
                side
                for side, (_effect, key, _argument) in candidates.items()
                if action.data.get(key)
            }
            selected = {
                side for side in present if policy.includes(candidates[side][0])
            }
            if not selected:
                pending.append(_PendingAction(action, link_ids))
                continue
            anchor = merge_links(link_ids, operations)
            kwargs = {
                candidates[side][2]: list(action.data.get(candidates[side][1]) or ())
                for side in selected
            }
            state = state.edit_link_content(anchor, **kwargs)
            applied.append(
                {
                    "action": action.to_dict(),
                    "effects": sorted(candidates[s][0] for s in selected),
                }
            )
            remaining_sides = present - selected
            if remaining_sides:
                data = {
                    key: value
                    for key, value in action.data.items()
                    if key not in {"orig_snaps", "new_src_lines", "new_tgt_lines"}
                }
                for side in remaining_sides:
                    key = candidates[side][1]
                    data[key] = action.data[key]
                pending.append(
                    _PendingAction(
                        _action_copy(action, op_index=0, data=data), (anchor,)
                    )
                )
            continue

        if action.kind == "delete":
            if not policy.includes("delete_pair"):
                pending.append(_PendingAction(action, link_ids))
                continue
            anchor = merge_links(link_ids, operations)
            state = state.delete_link_content(anchor)
            applied.append({"action": action.to_dict(), "effects": ["delete_pair"]})
            continue

        pending.append(_PendingAction(action, link_ids))

    final_positions = {link.id: index for index, link in enumerate(state.links)}
    remaining: list[RepairAction] = []
    for item in pending:
        positions: list[int] = []
        for link_id in item.link_ids:
            position = final_positions.get(link_id)
            if position is not None and position not in positions:
                positions.append(position)
        if not positions:
            continue
        data = dict(item.action.data)
        if len(positions) > 1:
            data["orig_snaps"] = positions
        else:
            data.pop("orig_snaps", None)
        remaining.append(_action_copy(item.action, op_index=positions[0], data=data))

    return SolidificationPlan(
        baseline=baseline,
        solidified=state,
        policy=policy,
        original_actions=actions,
        remaining_actions=tuple(remaining),
        applied=tuple(applied),
    )


def plan_report_solidification(
    document_a_path: str | Path,
    document_b_path: str | Path,
    report_path: str | Path,
    policy: SolidifyPolicy,
) -> tuple[SolidificationPlan, dict]:
    """Load a report and create a checked, non-mutating solidification plan."""

    path_a = Path(document_a_path)
    path_b = Path(document_b_path)
    report_target = Path(report_path)
    report = load_report(report_target)
    if not report_matches_documents(report, path_a, path_b):
        raise ReportError("源文档已变化，不能固化基于旧快照的修复")
    pair = create_alignment_pair(
        pair_id=str(report.get("chapter_id") or path_a.stem),
        document_a_path=path_a,
        document_b_path=path_b,
        alignment_path=report_target,
        operations=operations_from_report(report),
        provenance=dict(report.get("provenance") or {}),
    )
    baseline = PairEditingState.from_alignment_pair(
        pair,
        path_a.read_text(encoding="utf-8-sig"),
        path_b.read_text(encoding="utf-8-sig"),
    )
    actions = [RepairAction.from_dict(item) for item in report.get("repair_log", ())]
    return build_solidification_plan(baseline, actions, policy), report


def solidify_report(
    document_a_path: str | Path,
    document_b_path: str | Path,
    report_path: str | Path,
    policy: SolidifyPolicy,
) -> tuple[SolidificationPlan, PairSaveResult | None]:
    """Plan and atomically apply selected effects to a document pair."""

    report_target = Path(report_path)
    expected_report_sha256 = hashlib.sha256(report_target.read_bytes()).hexdigest()
    plan, report = plan_report_solidification(
        document_a_path, document_b_path, report_path, policy
    )
    if not plan.has_changes:
        return plan, None
    result = save_pair_transaction(
        plan.solidified,
        document_a_path=document_a_path,
        document_b_path=document_b_path,
        report_path=report_path,
        report=report,
        expected_report_sha256=expected_report_sha256,
        expected_report_exists=True,
        remaining_repair_log=plan.remaining_actions,
        solidification_policy=policy.to_dict(),
        applied_repairs=plan.applied,
    )
    return plan, result
