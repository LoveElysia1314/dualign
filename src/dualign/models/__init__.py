"""Dualign — 数据模型（统一公共 API 面）"""

from dualign.models.state import AlignmentSnapshot, OpT, MISSING
from dualign.models.action import RepairAction, AiProposal, AiProposalStore
from dualign.models.state import AlignedRow, SnapGroup, ChapterState
from dualign.models.snap_state import (
    SnapState,
    SnapInfo,
    build_snap_states,
    refresh_snap_states,
    snap_state_to_info,
    APPROVAL_NONE,
    APPROVAL_AUTO,
    APPROVAL_AGENT,
    APPROVAL_USER,
    ALL_APPROVAL_STATES,
    APPROVAL_LABELS,
)

# 文件 I/O
from dualign.common import save_report, load_report

__all__ = [
    "AlignmentSnapshot",
    "OpT",
    "MISSING",
    "RepairAction",
    "AiProposal",
    "AiProposalStore",
    "AlignedRow",
    "SnapGroup",
    "ChapterState",
    "SnapState",
    "SnapInfo",
    "build_snap_states",
    "refresh_snap_states",
    "snap_state_to_info",
    "APPROVAL_NONE",
    "APPROVAL_AUTO",
    "APPROVAL_AGENT",
    "APPROVAL_USER",
    "ALL_APPROVAL_STATES",
    "APPROVAL_LABELS",
    "save_report",
    "load_report",
]
