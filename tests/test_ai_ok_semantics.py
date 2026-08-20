"""Regression tests: AI "ok" must approve, not override, existing repairs.

原缺陷（两层）：
  1. ai_repair_chapter 调用 agent.run(ctx) 未传 initial_state，导致
     ToolExecutor._get_current_snap_action 恒返回 None，_handle_ok 的
     "AI ok 等同于认可已有修复操作" 语义永远不生效 —— AI 对已合并的
     snap 发 ok 会生成独立的 ok（marker [AI][OK]）而非转换后的 merge。
  2. replay 的 _apply_info_free 对 [AI][OK]/[AI][F] 直接设置 marker，
     覆盖已有 [M]/[S]/[E]/[D]/[P] —— 合并等修复操作从状态列消失。
"""

from dualign.models.state import AlignmentSnapshot
from dualign.models.action import RepairAction
from dualign.services.repair import RepairState
from dualign.services.ai_repair_agent import (
    ChapterContext,
    ToolExecutor,
    ToolCall,
    LLMResponse,
    LLMBackend,
    AiRepairAgent,
)


def _snapshot():
    ops = [
        ((0,), (0,), 0.95),
        ((1, 2), (1,), 0.80),  # snap 1: 2:1 → auto merge
        ((3,), (2, 3), 0.70),  # snap 2: 1:2 → auto merge
        ((4,), (4,), 0.95),
    ]
    return AlignmentSnapshot.from_alignment(
        ops,
        ["S0", "S1", "S2", "S3", "S4"],
        ["T0", "T1", "T2", "T3", "T4"],
    )


def _repaired_state():
    """snap 1 已有自动合并。"""
    return RepairState(_snapshot(), [RepairAction.make_merge(1, sub_count=2)])


def _ctx_with_repair():
    return ChapterContext.from_repair_state(
        _repaired_state(),
        chapter_id="t1",
        chapter_title="测试",
        strategy="src",
        model=None,
    )


class TestOkConvertsToExistingRepair:
    """Bug A：ok 必须携带 initial_state 才能识别已有修复操作。"""

    def test_ok_without_initial_state_stays_ok(self):
        # 对照：未传 initial_state（旧行为）时 ok 不会被转换
        ctx = _ctx_with_repair()
        ex = ToolExecutor(ctx, model=None, initial_state=None, strategy="src")
        ex.execute(ToolCall("c1", "ok", {"target": "1"}))
        act = ex.reviewed_actions.get(1)
        assert act is not None
        assert act.kind == "ok"

    def test_ok_with_initial_state_converts_to_merge(self):
        ctx = _ctx_with_repair()
        ex = ToolExecutor(
            ctx, model=None, initial_state=_repaired_state(), strategy="src"
        )
        ex.execute(ToolCall("c2", "ok", {"target": "1"}))
        act = ex.reviewed_actions.get(1)
        assert act is not None
        assert act.kind == "merge"  # AI ok 认可已有合并 → 转换为 merge
        assert act.source == "ai"
        assert act.marker == "[AI][M]"

    def test_ok_with_initial_state_on_clean_snap_stays_ok(self):
        ctx = ChapterContext.from_repair_state(
            RepairState(_snapshot()),
            chapter_id="t1",
            chapter_title="测试",
            strategy="src",
            model=None,
        )
        ex = ToolExecutor(
            ctx, model=None, initial_state=RepairState(_snapshot()), strategy="src"
        )
        ex.execute(ToolCall("c3", "ok", {"target": "1"}))
        act = ex.reviewed_actions.get(1)
        assert act.kind == "ok"  # snap 1 无先前修复 → 真正的通过
        assert act.marker == "[AI][OK]"


class TestAiOkDoesNotEraseRepairMarker:
    """Bug B：replay 时 [AI][OK] 必须叠加而非覆盖已有修复标记。"""

    def test_ai_ok_preserves_merge_marker(self):
        state = RepairState(
            _snapshot(),
            [
                RepairAction.make_merge(1, sub_count=2),
                RepairAction(op_index=1, kind="ok", source="ai"),
            ],
        )
        markers = {row.marker for row in state.current.rows if row.snap_index == 1}
        assert markers == {"[M] [AI][OK]"}, markers

    def test_ai_flag_preserves_repair_marker(self):
        state = RepairState(
            _snapshot(),
            [
                RepairAction.make_merge(1, sub_count=2),
                RepairAction(op_index=1, kind="flag", source="ai"),
            ],
        )
        markers = {row.marker for row in state.current.rows if row.snap_index == 1}
        assert markers == {"[M] [AI][F]"}, markers

    def test_ai_ok_without_prior_repair_keeps_full_marker(self):
        state = RepairState(
            _snapshot(), [RepairAction(op_index=1, kind="ok", source="ai")]
        )
        markers = {row.marker for row in state.current.rows if row.snap_index == 1}
        assert markers == {"[AI][OK]"}, markers

    def test_manual_ok_still_combines(self):
        # 对照组：手动 ok（无 AI 前缀）行为不变
        state = RepairState(
            _snapshot(),
            [RepairAction.make_merge(1, sub_count=2), RepairAction.make_ok(1)],
        )
        markers = {row.marker for row in state.current.rows if row.snap_index == 1}
        assert markers == {"[M] [OK]"}, markers

    def test_ok_f_mutual_exclusion_with_ai_prefix(self):
        # [OK] 与 [F] 互斥：已有 [AI][OK] 再叠加 AI flag → 移除 [OK] 保留 [AI][F]
        state = RepairState(
            _snapshot(),
            [
                RepairAction(op_index=1, kind="ok", source="ai"),
                RepairAction(op_index=1, kind="flag", source="ai"),
            ],
        )
        markers = {row.marker for row in state.current.rows if row.snap_index == 1}
        assert markers == {"[AI][F]"}, markers


class _ScriptedBackend(LLMBackend):
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def chat(self, messages, thinking=False, tools=None):
        self.calls += 1
        return self.script.pop(0)


def test_agent_run_with_initial_state_passes_ok_through():
    """端到端：Agent 用 initial_state 运行时，ok 转换由 ToolExecutor 完成。"""
    ctx = _ctx_with_repair()
    t1 = LLMResponse(
        tool_calls=[
            ToolCall("a", "ok", {"target": "1"}),
            ToolCall("b", "ok", {"target": "2"}),
            ToolCall("c", "done", {}),
        ]
    )
    agent = AiRepairAgent(backend="deepseek", verbose=False, strategy="src")
    agent._llm = _ScriptedBackend([t1])
    actions = agent.run(ctx, initial_state=_repaired_state())
    by_op = {a.op_index: a for a in actions}
    assert by_op[1].kind == "merge"  # AI ok 认可已有 merge
    assert by_op[2].kind == "ok"  # snap 2 无修复 → 真正的通过
