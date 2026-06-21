"""
Dualign — 修复状态机测试
"""

import pytest
from dualign.models.state import AlignmentSnapshot
from dualign.models.action import RepairAction
from dualign.models.state import AlignedRow, SnapGroup, ChapterState
from dualign.services.repair import RepairState, RepairService


@pytest.fixture
def simple_snapshot():
    ops = [((0,), (0,), 0.95), ((1,), (1,), 0.85), ((2, 3), (2,), 0.65)]
    return AlignmentSnapshot.from_alignment(ops, ["A", "B", "C", "D"], ["a", "b", "c"])


@pytest.fixture
def simple_state(simple_snapshot):
    return RepairState(simple_snapshot)


class TestRepairStateCreate:
    def test_initial_groups(self, simple_state):
        assert len(simple_state.current.groups) == 3

    def test_snap_index_access(self, simple_state):
        cs = simple_state.current
        assert cs.group(0) is not None
        assert cs.group(99) is None

    def test_not_dirty_initially(self, simple_state):
        assert len(simple_state.repair_log) == 0

    def test_dirty_after_apply(self, simple_state):
        action = RepairAction(kind="ok", op_index=0)
        state2 = simple_state.apply(action)
        assert len(state2.repair_log) == 1
        assert len(simple_state.repair_log) == 0  # 原状态不变


class TestApplyUndo:
    def test_apply_new_instance(self, simple_state):
        action = RepairAction(kind="ok", op_index=0)
        state2 = simple_state.apply(action)
        assert state2 is not simple_state

    def test_undo_new_instance(self, simple_state):
        action = RepairAction(kind="ok", op_index=0)
        state2 = simple_state.apply(action)
        state3 = RepairState(
            state2.snapshot, state2.repair_log[:-1], state2.ai_proposal_store
        )
        assert len(state3.repair_log) == 0

    def test_undo_empty(self, simple_state):
        assert len(simple_state.repair_log) == 0

    def test_apply_then_undo_restores(self, simple_state):
        action = RepairAction(kind="ok", op_index=0)
        n_before = len(simple_state.current.groups)
        state2 = simple_state.apply(action)
        state3 = RepairState(
            state2.snapshot, state2.repair_log[:-1], state2.ai_proposal_store
        )
        assert len(state3.current.groups) == n_before

    def test_reset_clears_log(self, simple_state):
        state2 = simple_state.apply(RepairAction(kind="ok", op_index=0))
        assert len(state2.reset().repair_log) == 0

    def test_reset_op(self, simple_state):
        s1 = simple_state.apply(RepairAction(kind="ok", op_index=0))
        s2 = s1.apply(RepairAction(kind="flag", op_index=1))
        sr = s2.reset_op(0)
        assert sr.action_for_op(0) is None
        assert sr.action_for_op(1) is not None


class TestChapterState:
    def test_replace_snap_immutable(self, simple_state):
        cs = simple_state.current
        g0_edited = cs.group(0).with_text([("NewSrc", "NewTgt")], [0.99], "[E]")
        cs2 = cs.replace_snap(0, g0_edited)
        assert cs2.group(0).rows[0].src_text == "NewSrc"
        assert cs.group(0).rows[0].src_text == "A"

    def test_remove_snap(self, simple_state):
        cs = simple_state.current
        cs2 = cs.remove_snap(1)
        assert len(cs2.groups) == 2
        assert cs2.group(1) is None
        assert cs.group(1) is not None


class TestSnapGroup:
    def test_from_snapshot_2to1(self, simple_snapshot):
        g = SnapGroup.from_snapshot(2, simple_snapshot)
        assert g.snap_i == 2
        assert len(g.rows) == 2

    def test_from_snapshot_1to1(self, simple_snapshot):
        assert len(SnapGroup.from_snapshot(0, simple_snapshot).rows) == 1

    def test_with_marker_immutable(self, simple_snapshot):
        g = SnapGroup.from_snapshot(0, simple_snapshot)
        g2 = g.with_marker("[M]")
        assert g2.rows[0].marker == "[M]"
        assert g.rows[0].marker == ""

    def test_with_text_immutable(self, simple_snapshot):
        g = SnapGroup.from_snapshot(0, simple_snapshot)
        g2 = g.with_text([("new_src", "new_tgt")], [0.99], "[E]")
        assert g2.rows[0].src_text == "new_src"

    def test_row_frozen(self):
        row = AlignedRow(
            snap_index=0,
            sub=0,
            init_type="1:1",
            cur_type="1:1",
            src_text="A",
            tgt_text="a",
            score=0.9,
            orig_score=0.9,
            n_src=1,
            n_tgt=1,
        )
        with pytest.raises(Exception):
            row.src_text = "B"  # type: ignore


class TestRepairAction:
    def test_serialize(self):
        action = RepairAction(
            kind="edit",
            op_index=0,
            sub_count=1,
            data={"new_src_lines": ["X"], "new_tgt_lines": ["Y"]},
        )
        d = action.to_dict()
        assert d["kind"] == "edit"
        assert d["data"]["new_src_lines"] == ["X"]

    def test_deserialize(self):
        d = {"kind": "edit", "op_index": 0, "data": {"X": 1}}
        action = RepairAction.from_dict(d)
        assert action.kind == "edit"

    def test_invalid_kind_raises(self):
        with pytest.raises(ValueError):
            RepairAction(kind="invalid_kind", op_index=0)

    def test_auto_timestamp(self):
        action = RepairAction(kind="ok", op_index=0)
        assert len(action.timestamp) > 0
        assert "T" in action.timestamp


class TestActionEdgeCases:
    def test_delete_snap(self, simple_state):
        """删除操作将 marker 设为 [D]，groups 数不变但 marker 变化。"""
        state2 = simple_state.apply(RepairAction(kind="delete", op_index=0))
        g0 = state2.current.group(0)
        assert g0 is not None
        # 删除操作给 group 打上 [D] 标记
        assert "[D]" in g0.rows[0].marker or g0.rows[0].marker != ""

    def test_action_for_op(self, simple_state):
        assert simple_state.action_for_op(0) is None
        action = RepairAction(kind="ok", op_index=0)
        state2 = simple_state.apply(action)
        assert state2.action_for_op(0) is not None

    def test_repair_log_property(self, simple_state):
        assert simple_state.repair_log == []
        state2 = simple_state.apply(RepairAction(kind="ok", op_index=0))
        assert len(state2.repair_log) == 1
        # 原始 state 不受影响
        assert simple_state.repair_log == []

    def test_snapshot_property(self, simple_state):
        assert simple_state.snapshot is not None
        assert len(simple_state.original_ops) == 3


# ═══════════════════════════════════════════════════════════════
# render_rows 测试 — 所有标记类型的文本输出正确性
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def multi_state():
    """ops: snap0=1:1, snap1=2:1(需合并), snap2=1:1, snap3=0:2(多余译文)"""
    ops = [
        ((0,), (0,), 0.95),
        ((1, 2), (1,), 0.80),
        ((3,), (2,), 0.70),
        ((), (3, 4), 0.0),
    ]
    src = ["A", "B", "C", "D", "E"]
    tgt = ["a", "b", "c", "d", "e"]
    return RepairState(AlignmentSnapshot.from_alignment(ops, src, tgt))


class TestRenderRows:
    """验证 render_rows 对所有标记类型的文本输出正确性。

    测试数据 (multi_state):
      snap0=1:1 → (A, a)
      snap1=2:1 → (B, b), (C, '')
      snap2=1:1 → (D, c)
      snap3=0:2 → ('', d), ('', e)
      total=6 行
    """

    def test_no_marker_passthrough(self, multi_state):
        """无标记时逐行输出原始文本。"""
        src, tgt = RepairService.render_rows(multi_state)
        assert len(src) == 6, f"expect 6 rows (1+2+1+2), got {len(src)}"
        assert src[0] == "A"
        assert tgt[0] == "a"
        # snap1=2:1: sub0=(B,b), sub1=(C,'')
        assert src[1] == "B"
        assert tgt[1] == "b"
        assert src[2] == "C"
        assert tgt[2] == ""
        # snap3=0:2: sub0=('',d), sub1=('',e)
        assert src[4] == ""
        assert tgt[4] == "d"
        assert tgt[5] == "e"

    def test_merge_single_snap(self, multi_state):
        """[M] 单行合并：2:1 → 1 行输出。"""
        repaired = RepairService.repair_merge(multi_state, 1)
        src, tgt = RepairService.render_rows(repaired)
        assert len(src) == 5, f"expect 5 rows (1+1+1+2), got {len(src)}"
        assert src[1] == "B C", f"merged src mismatch: {src[1]!r}"
        assert tgt[1] == "b", f"merged tgt mismatch: {tgt[1]!r}"

    def test_merge_bundle(self, multi_state):
        """[M] 跨行合并 (bundle)：合并 snap0+snap2。"""
        repaired = RepairService.repair_bundle_snaps(multi_state, [0, 2])
        src, tgt = RepairService.render_rows(repaired)
        assert len(src) == 5, f"expect 5 rows, got {len(src)}"
        assert src[0] == "A D", f"bundle src mismatch: {src[0]!r}"
        assert tgt[0] == "a c", f"bundle tgt mismatch: {tgt[0]!r}"

    def test_edit(self, multi_state):
        """[E] 校订：替换为自定义文本。"""
        repaired = multi_state.apply(
            RepairAction.make_edit(
                1,
                new_src_lines=["X", "Y"],
                new_tgt_lines=["x", "y"],
                inherited_scores=[0.9, 0.8],
            )
        )
        src, tgt = RepairService.render_rows(repaired)
        assert len(src) == 6
        assert src[1] == "X", f"edit src[1] mismatch: {src[1]!r}"
        assert tgt[1] == "x", f"edit tgt[1] mismatch: {tgt[1]!r}"
        assert src[2] == "Y", f"edit src[2] mismatch: {src[2]!r}"

    def test_edit_multi_snap(self, multi_state):
        """[E] 跨行校订 (multi-snap edit)。"""
        repaired = RepairService.repair_multi_edit(
            multi_state, [0, 1], ["X", "Y"], ["x", "y"], [0.9, 0.8]
        )
        src, tgt = RepairService.render_rows(repaired)
        # snap1 被删除，anchor=snap0 有 2 行；snap2(1) + snap3(2) = 5
        assert len(src) == 5, f"expect 5 rows (2+1+2), got {len(src)}"
        assert src[0] == "X"
        assert src[1] == "Y"

    def test_delete(self, multi_state):
        """[D] 删除：跳过不输出。"""
        repaired = multi_state.apply(RepairAction.make_delete(1))
        src, tgt = RepairService.render_rows(repaired)
        assert "B" not in src, "deleted snap text should not appear"

    def test_placeholder(self, multi_state):
        """[P] 占位符：空侧标记 ⟢MISSING⟣。"""
        # snap3=0:2 → 多余译文，占位符填原文侧
        repaired = RepairService.repair_placeholder(multi_state, 3, "src")
        src, tgt = RepairService.render_rows(repaired)
        assert len(src) == 6
        # snap3 sub0 和 sub1 的原文侧均为 ⟢MISSING⟣
        assert src[4] == "\u27e2MISSING\u27e3", f"placeholder[4] mismatch: {src[4]!r}"
        assert src[5] == "\u27e2MISSING\u27e3", f"placeholder[5] mismatch: {src[5]!r}"
        assert tgt[4] == "d", f"placeholder tgt[4] mismatch: {tgt[4]!r}"
        assert tgt[5] == "e", f"placeholder tgt[5] mismatch: {tgt[5]!r}"

    def test_flag_preserves_text(self, multi_state):
        """[F] 标记：文本不变。"""
        repaired = multi_state.apply(RepairAction.make_flag(1))
        src_before, _ = RepairService.render_rows(multi_state)
        src_after, _ = RepairService.render_rows(repaired)
        assert src_before == src_after, "flag should not change text"

    def test_ok_preserves_text(self, multi_state):
        """[OK] 确认：文本不变。"""
        repaired = multi_state.apply(RepairAction.make_ok(1))
        src_before, _ = RepairService.render_rows(multi_state)
        src_after, _ = RepairService.render_rows(repaired)
        assert src_before == src_after, "ok should not change text"

    def test_mixed_merge_edit(self, multi_state):
        """混合操作：先 merge 再 bundle。"""
        s1 = RepairService.repair_merge(multi_state, 1)
        s2 = RepairService.repair_bundle_snaps(s1, [0, 1])
        src, tgt = RepairService.render_rows(s2)
        # bundle snap0+snap1=1行；snap2(1)+snap3(2)=4
        assert len(src) == 4, f"expect 4 rows, got {len(src)}"
        assert src[0] == "A B C", f"mixed src mismatch: {src[0]!r}"
        assert tgt[0] == "a b", f"mixed tgt mismatch: {tgt[0]!r}"

    def test_delete_and_merge_preserves_order(self, multi_state):
        """删除后再合并，顺序保持正确。"""
        s1 = multi_state.apply(RepairAction.make_delete(0))
        s2 = RepairService.repair_bundle_snaps(s1, [1, 2])
        src, tgt = RepairService.render_rows(s2)
        # [D]skip snap0 + bundle snap1+snap2=1行 + snap3(2) = 3
        assert len(src) == 3, f"expect 3 rows, got {len(src)}"
        assert src[0] == "B C D", f"delete+merge src mismatch: {src[0]!r}"

    def test_render_rows_to_files(self, multi_state, tmp_path):
        """render_to_files 将文本写入文件。"""
        import os

        spath = os.path.join(tmp_path, "test.src.md")
        tpath = os.path.join(tmp_path, "test.tgt.md")
        repaired = RepairService.repair_merge(multi_state, 1)
        RepairService.render_to_files(repaired, spath, tpath)
        assert os.path.isfile(spath)
        assert os.path.isfile(tpath)
        with open(spath) as f:
            content = f.read()
        assert "A" in content
        assert "B C" in content

    def test_render_after_full_reset(self, multi_state):
        """重置所有操作后文本恢复原始。"""
        s1 = RepairService.repair_merge(multi_state, 1)
        s2 = s1.apply(RepairAction.make_delete(0))
        src_reset, _ = RepairService.render_rows(s2.reset())
        assert src_reset == [
            "A",
            "B",
            "C",
            "D",
            "",
            "",
        ], f"reset restore mismatch: {src_reset}"
