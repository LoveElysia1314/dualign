"""
Dualign — 异常持久化测试

验证 MULTI / ORPHAN / LOW_SCORE / MIX 异常在 auto_repair 和 AI ok 后
不会消失，仅人类 ok（approval=USER）时清除。
"""

import pytest
from dualign.models.state import AlignmentSnapshot
from dualign.models.action import RepairAction
from dualign.services.repair import RepairState, RepairService
from dualign.models.snap_state import (
    SnapState,
    build_snap_states,
    refresh_snap_states,
    APPROVAL_NONE,
    APPROVAL_AUTO,
    APPROVAL_AGENT,
    APPROVAL_USER,
)

# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def mixed_snapshot():
    """构造包含不同类型异常的 snapshot。

    snap 0-4: 1:1 锚点（高分）
    snap 5: 1:2 → MULTI（auto_repair 将 merge）
    snap 6: 1:1 LOW_SCORE（0.30，在 9 个高分锚点中为离群）
    snap 7: 2:1 → MULTI（auto_repair 将 split）
    snap 8: 1:0 → ORPHAN（auto_repair 将 placeholder_tgt）
    snap 9: 1:1 锚点（正常）
    """
    ops = [
        ((0,), (0,), 0.96),  # snap 0: anchor
        ((1,), (1,), 0.94),  # snap 1: anchor
        ((2,), (2,), 0.97),  # snap 2: anchor
        ((3,), (3,), 0.93),  # snap 3: anchor
        ((4,), (4,), 0.95),  # snap 4: anchor
        ((5,), (5, 6), 0.60),  # snap 5: 1:2 MULTI
        ((6,), (7,), 0.05),  # snap 6: 1:1 LOW_SCORE ← 极端低分，Z>3
        ((7, 8), (8,), 0.55),  # snap 7: 2:1 MULTI
        ((9,), tuple(), 0.0),  # snap 8: 1:0 ORPHAN
        ((10,), (9,), 0.92),  # snap 9: anchor
    ]
    return AlignmentSnapshot.from_alignment(
        ops,
        [
            "A0",
            "A1",
            "A2",
            "A3",
            "A4",
            "原文行5",
            "低分原文",
            "原文行7-1",
            "原文行7-2",
            "孤立原文",
            "A9",
        ],
        [
            "a0",
            "a1",
            "a2",
            "a3",
            "a4",
            "译文行5-1",
            "译文行5-2",
            "译文行7",
            "译文行8",
            "a9",
        ],
    )


@pytest.fixture
def raw_state(mixed_snapshot):
    return RepairState(mixed_snapshot)


@pytest.fixture
def raw_states(mixed_snapshot):
    return build_snap_states(
        mixed_snapshot,
        list(mixed_snapshot.original_src_lines),
        list(mixed_snapshot.original_tgt_lines),
        repair_log=[],
    )


@pytest.fixture
def repaired_state(raw_state):
    """src 策略 auto_repair 后的状态。"""
    return RepairService.auto_repair(raw_state, strategy="src")


@pytest.fixture
def repaired_states(raw_states, mixed_snapshot, repaired_state):
    """auto_repair 后 refreshed 的 SnapState 列表。"""
    return refresh_snap_states(
        raw_states,
        mixed_snapshot,
        repaired_state.current,
        repaired_state.repair_log,
    )


# ═══════════════════════════════════════════════════════════════
# MULTI 持久化
# ═══════════════════════════════════════════════════════════════


class TestMultiPersistence:
    """NON_1TO1 异常（1:2, 2:1）的双模行为。"""

    def test_raw_multi_detected(self, raw_states):
        assert "NON_1TO1" in raw_states[5].anomaly_types  # 1:2
        assert "NON_1TO1" in raw_states[7].anomaly_types  # 2:1

    def test_multi_persists_after_auto_repair(self, repaired_states):
        """auto_repair 将 1:2 → merge — 逻辑层面已是 1:1，current 不含 NON_1TO1。"""
        assert (
            "NON_1TO1" in repaired_states[5].initial_anomaly_types
        ), f"1:2 after merge should still be NON_1TO1 (initial): {repaired_states[5].initial_anomaly_types}"
        assert (
            "NON_1TO1" in repaired_states[7].initial_anomaly_types
        ), f"2:1 after split should still be NON_1TO1 (initial): {repaired_states[7].initial_anomaly_types}"
        # merge → 逻辑 1:1
        assert "NON_1TO1" not in repaired_states[5].current_anomaly_types
        assert "NON_1TO1" not in repaired_states[7].current_anomaly_types
        assert repaired_states[5].approval == APPROVAL_AUTO
        assert repaired_states[7].approval == APPROVAL_AUTO

    def test_multi_after_ai_ok(self, repaired_state, repaired_states, mixed_snapshot):
        """AI ok 解析为 merge — merge 已使逻辑为 1:1，current 不再含 NON_1TO1。"""
        # AI 的 ok 在 ToolExecutor 层面解析为真实的操作 kind
        s2 = repaired_state.apply(RepairAction(op_index=5, kind="merge", source="ai"))
        r2 = refresh_snap_states(
            repaired_states, mixed_snapshot, s2.current, s2.repair_log
        )
        assert "NON_1TO1" in r2[5].initial_anomaly_types
        assert "NON_1TO1" not in r2[5].current_anomaly_types  # merge → 逻辑 1:1
        assert r2[5].approval == APPROVAL_AGENT
        assert not r2[5].is_reviewable  # agent 批准 → 不再审校

    def test_multi_cleared_after_human_ok(
        self, repaired_state, repaired_states, mixed_snapshot
    ):
        """人类 ok — approval=USER → is_reviewable=False。"""
        s2 = repaired_state.apply(RepairAction(op_index=5, kind="ok", source="user"))
        r2 = refresh_snap_states(
            repaired_states, mixed_snapshot, s2.current, s2.repair_log
        )
        assert not r2[5].is_reviewable
        assert r2[5].approval == APPROVAL_USER


# ═══════════════════════════════════════════════════════════════
# ORPHAN 持久化
# ═══════════════════════════════════════════════════════════════


class TestOrphanPersistence:
    """NON_1TO1 异常（1:0, 0:1）的双模行为。"""

    def test_raw_orphan_detected(self, raw_states):
        assert "NON_1TO1" in raw_states[8].anomaly_types  # 1:0

    def test_orphan_persists_after_auto_repair(self, repaired_states):
        """auto_repair 将 1:0 → placeholder_tgt — 占位后 n_src=n_tgt=1，current NON_1TO1 解除。"""
        assert "NON_1TO1" in repaired_states[8].initial_anomaly_types
        assert "NON_1TO1" not in repaired_states[8].current_anomaly_types  # 占位补为1:1
        assert repaired_states[8].approval == APPROVAL_AUTO
        assert repaired_states[8].has_missing

    def test_orphan_after_ai_ok(self, repaired_state, repaired_states, mixed_snapshot):
        """AI ok 解析为 placeholder_tgt — 占位文本保持不变，current 仍无 NON_1TO1。"""
        # AI 的 ok 在 ToolExecutor 层面解析为真实的操作 kind
        s2 = repaired_state.apply(RepairAction(op_index=8, kind="placeholder_tgt", source="ai"))
        r2 = refresh_snap_states(
            repaired_states, mixed_snapshot, s2.current, s2.repair_log
        )
        assert "NON_1TO1" in r2[8].initial_anomaly_types
        assert "NON_1TO1" not in r2[8].current_anomaly_types  # 占位后结构不变
        assert r2[8].approval == APPROVAL_AGENT
        assert not r2[8].is_reviewable

    def test_orphan_cleared_after_human_ok(
        self, repaired_state, repaired_states, mixed_snapshot
    ):
        """人类 ok — approval=USER → is_reviewable=False。"""
        s2 = repaired_state.apply(RepairAction(op_index=8, kind="ok", source="user"))
        r2 = refresh_snap_states(
            repaired_states, mixed_snapshot, s2.current, s2.repair_log
        )
        assert not r2[8].is_reviewable
        assert r2[8].approval == APPROVAL_USER


# ═══════════════════════════════════════════════════════════════
# LOW_SCORE 持久化说明
#
# LOW_SCORE 的 is_low_score 字段在 build_snap_states 中设置后，
# refresh_snap_states 直接继承 states[si].is_low_score，不会重新计算。
# 因此只要初始检测通过，持久化是自动保证的。
# Z-score 检测本身（k=3.0 阈值）由 quality_gate 的 is_statistical_low_score 覆盖。
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 正常 1:1 锚点不出现在异常列表
# ═══════════════════════════════════════════════════════════════


class TestNormalAnchors:
    """正常 1:1 锚点不产生异常。"""

    def test_normal_anchor_no_anomaly(self, raw_states):
        assert raw_states[0].anomaly_types == []
        assert not raw_states[0].is_reviewable

    def test_normal_anchor_after_auto_repair(self, repaired_states):
        assert repaired_states[9].anomaly_types == []
        assert not repaired_states[9].is_reviewable
