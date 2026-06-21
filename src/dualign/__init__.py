"""
Dualign 0.7.0 — 双语平行文档对齐与辅助校验工具

公共 API 导出:
  - 核心: AlignConfig, AlignmentResult, AlignmentSnapshot, RepairState
  - 修复: RepairService, RepairAction
  - AI 审校: AiRepairAgent, ChapterContext, build_chapter_context
"""

from dualign.core import (
    AlignConfig,
    AlignmentResult,
    align,
    op_type_str,
)
from dualign.core.file_pair_matcher import (
    FilePairMatcher,
    MatchRule,
    MatchedPair,
)
from dualign.models.state import AlignmentSnapshot, MISSING
from dualign.models.action import RepairAction
from dualign.models.state import AlignedRow, SnapGroup, ChapterState
from dualign.models.action import AiProposal, AiProposalStore
from dualign.services.repair import (
    RepairState,
    RepairService,
    replay,
    make_table_view,
)
from dualign.config import (
    repair_session_path,
    get_report_cache_dir,
    get_cache_root,
    DUALIGN_CACHE_ROOT,
)
from dualign.services.ai_repair_agent import (
    AiRepairAgent,
    ChapterContext,
    AgentEvent,
)

__version__ = "0.7.0"

__all__ = [
    "__version__",
    "AlignConfig",
    "AlignmentResult",
    "AlignmentSnapshot",
    "align",
    "op_type_str",
    "MISSING",
    "RepairAction",
    "AlignedRow",
    "SnapGroup",
    "ChapterState",
    "RepairState",
    "RepairService",
    "replay",
    "make_table_view",
    "FilePairMatcher",
    "MatchRule",
    "MatchedPair",
    "AiProposal",
    "AiProposalStore",
    "AiRepairAgent",
    "ChapterContext",
    "AgentEvent",
    "repair_session_path",
    "get_report_cache_dir",
    "get_cache_root",
    "DUALIGN_CACHE_ROOT",
]
