"""
Dualign — AI 校订 Agent 工具可靠性测试

覆盖本次修复:
  1. 工具参数统一为 target（兼容旧参数名 snap_range/snap_id/pair_spec）
  2. done 被拒绝时不再强制退出循环，模型可继续修复
  3. done(force=true) 跳过剩余项
  4. 范围 edit 行数校验
"""

import pytest

from dualign.models.state import AlignmentSnapshot
from dualign.models.action import RepairAction
from dualign.services.repair import RepairState, RepairService
from dualign.services.ai_repair_agent import (
    ChapterContext,
    ToolExecutor,
    ToolCall,
    LLMResponse,
    LLMBackend,
    AiRepairAgent,
)


@pytest.fixture
def reviewable_ctx():
    """构造含 3 个待审 snap 的章节上下文（无需嵌入模型）。

    snap 1: 1:2  → 待审
    snap 3: 0:1  → 待审（冗余，应 delete）
    snap 5: 2:1  → 待审
    """
    ops = [
        ((0,), (0,), 0.95),  # snap 0: 1:1 干净
        ((1,), (1, 2), 0.60),  # snap 1: 1:2
        ((2,), (3,), 0.95),  # snap 2: 1:1 干净
        ((), (4,), 0.70),  # snap 3: 0:1
        ((3,), (5,), 0.95),  # snap 4: 1:1 干净
        ((4, 5), (6,), 0.80),  # snap 5: 2:1
    ]
    snap = AlignmentSnapshot.from_alignment(
        ops,
        ["S0", "S1", "S2", "S3", "S4", "S5"],
        ["T0", "T1", "T2", "T3", "T4", "T5", "T6"],
    )
    raw = RepairState(snap)
    repaired = RepairService.auto_repair(raw, strategy="src", model=None)
    return ChapterContext.from_repair_state(
        repaired, chapter_id="t1", chapter_title="测试", strategy="src", model=None
    )


def _executor(ctx):
    return ToolExecutor(ctx, model=None, initial_state=None, strategy="src")


def _call(name, args):
    return ToolCall(id="c1", name=name, arguments=args)


class TestTargetParamUnification:
    """核心回归：模型误用旧参数名 / int 类型也必须成功。"""

    def test_edit_with_legacy_snap_id(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        # 本次事故现场：edit 传了 snap_id 而不是 snap_range
        result = ex.execute(_call("edit", {"snap_id": 1, "new_tgt": ["新译文"]}))
        assert "✏️ 编辑" in result, result
        assert 1 in ex.reviewed_ids

    def test_edit_with_target_int(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        result = ex.execute(_call("edit", {"target": 5, "new_src": ["A", "B"]}))
        assert "✏️ 编辑" in result, result

    def test_merge_with_legacy_snap_range(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        result = ex.execute(_call("merge", {"snap_range": "1"}))
        assert "🔗 合并" in result, result

    def test_delete_with_target(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        result = ex.execute(_call("delete", {"target": "3"}))
        assert "🗑️ 删除" in result, result

    def test_ok_with_target(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        result = ex.execute(_call("ok", {"target": "1"}))
        assert "✅ 确认" in result, result

    def test_ok_rejects_range(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        result = ex.execute(_call("ok", {"target": "1-2"}))
        assert "只接受单个" in result, result

    def test_flag_with_target(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        result = ex.execute(_call("flag", {"target": "2", "note": "跨行从句"}))
        assert "🚩 标记" in result, result

    def test_view_with_target(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        result = ex.execute(_call("view", {"target": "1-2,4"}))
        assert "snap" in result.lower() or '"id"' in result, result

    def test_append_with_target(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        result = ex.execute(_call("append", {"target": "4"}))
        assert "已追加" in result, result

    def test_edit_missing_target(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        result = ex.execute(_call("edit", {}))
        assert "缺少必填参数 target" in result, result

    def test_range_edit_length_mismatch_rejected(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        result = ex.execute(_call("edit", {"target": "1-2", "new_tgt": ["只有一行"]}))
        assert "edit 拒绝" in result, result

    def test_edit_rejects_missing_placeholder_in_new_tgt(self, reviewable_ctx):
        """占位符防线：新译文含 ⟢MISSING⟣ → 拒绝，不产生 edit 操作。"""
        ex = _executor(reviewable_ctx)
        placeholder = "\u27e2MISSING\u27e3"
        result = ex.execute(
            _call("edit", {"target": "1", "new_tgt": [placeholder]})
        )
        assert "占位符" in result, result
        assert "edit 拒绝" in result, result
        assert 1 not in ex.reviewed_ids
        assert not ex.reviewed_actions

    def test_edit_rejects_missing_placeholder_in_new_src(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        placeholder = "\u27e2MISSING\u27e3"
        result = ex.execute(
            _call("edit", {"target": "5", "new_src": [placeholder, "B"]})
        )
        assert "edit 拒绝" in result, result
        assert 5 not in ex.reviewed_ids

    def test_edit_allows_embedded_missing_in_prose(self, reviewable_ctx):
        """内嵌符号（非独立占位符行）不拦截。"""
        ex = _executor(reviewable_ctx)
        placeholder = "\u27e2MISSING\u27e3"
        result = ex.execute(
            _call("edit", {"target": "1", "new_tgt": ["正文提及 " + placeholder]})
        )
        assert "✏️ 编辑" in result, result
        assert 1 in ex.reviewed_ids

    def test_unknown_tool(self, reviewable_ctx):
        ex = _executor(reviewable_ctx)
        result = ex.execute(_call("force_done", {"note": "x"}))
        assert "未知工具" in result, result


class _ScriptedBackend(LLMBackend):
    """按脚本依次返回预置响应的假后端。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def chat(self, messages, thinking=False, tools=None):
        self.calls += 1
        return self.script.pop(0)


def _run_agent(ctx, script):
    agent = AiRepairAgent(backend="deepseek", verbose=False, strategy="src")
    agent._llm = _ScriptedBackend(script)
    actions = agent.run(ctx, initial_state=None)
    return agent, actions


class TestDoneFlow:
    def test_done_rejected_keeps_loop_alive(self, reviewable_ctx):
        """本次事故场景：先处理 2/3，调用 done 被拒绝 → 循环必须继续而非退出。"""
        t1 = LLMResponse(
            tool_calls=[
                ToolCall("a", "edit", {"snap_id": 1, "new_tgt": ["新译文"]}),
                ToolCall("b", "delete", {"snap_id": 3}),
                ToolCall("c", "done", {}),
            ]
        )
        t2 = LLMResponse(
            tool_calls=[
                ToolCall("d", "ok", {"target": "5"}),
                ToolCall("e", "done", {}),
            ]
        )
        agent, actions = _run_agent(reviewable_ctx, [t1, t2])
        kinds = sorted((a.op_index, a.kind) for a in actions)
        assert (1, "edit") in kinds, kinds
        assert (3, "delete") in kinds, kinds
        assert (5, "ok") in kinds, kinds
        assert len(actions) == 3, actions

    def test_done_force_skips_remaining(self, reviewable_ctx):
        t1 = LLMResponse(
            tool_calls=[ToolCall("a", "done", {"force": True, "note": "跳过"})]
        )
        agent, actions = _run_agent(reviewable_ctx, [t1])
        assert actions == []

    def test_done_rejected_then_force(self, reviewable_ctx):
        t1 = LLMResponse(tool_calls=[ToolCall("a", "done", {})])
        t2 = LLMResponse(
            tool_calls=[ToolCall("b", "done", {"force": True, "note": "重复项"})]
        )
        agent, actions = _run_agent(reviewable_ctx, [t1, t2])
        assert actions == []
        assert agent._llm.calls == 2

    def test_all_processed_done_accepted_single_turn(self, reviewable_ctx):
        t1 = LLMResponse(
            tool_calls=[
                ToolCall("a", "edit", {"target": "1", "new_tgt": ["X"]}),
                ToolCall("b", "delete", {"target": "3"}),
                ToolCall("c", "ok", {"target": "5"}),
                ToolCall("d", "done", {}),
            ]
        )
        agent, actions = _run_agent(reviewable_ctx, [t1])
        assert len(actions) == 3, actions
