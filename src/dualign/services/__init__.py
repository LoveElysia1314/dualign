"""Dualign 0.7.0 — 服务层"""

from dualign.services.repair import (
    RepairState,
    RepairService,
    replay,
    make_table_view,
    TableRow,
    TableViewModel,
)
from dualign.services.ai_repair_agent import (
    AiRepairAgent,
    ChapterContext,
    AgentEvent,
    MaxTurnsExceeded,
    compute_cost,
    DEEPSEEK_PRICES,
)
from dualign.services.embedding_cache import EmbeddingCache
from dualign.services.cached_encoder import CachedEncoder
from dualign.services.similarity import SimilarityScorer
from dualign.services.cli_pipeline import align_chapter
from dualign.services.quality_gate import (
    assess_alignment_quality,
    QualityGateConfig,
    _gap_row_ratio,
)
from dualign.common import save_report, load_report, set_ai_review

__all__ = [
    # repair
    "RepairState",
    "RepairService",
    "replay",
    "make_table_view",
    "TableRow",
    "TableViewModel",
    # ai_repair_agent
    "AiRepairAgent",
    "ChapterContext",
    "AgentEvent",
    "MaxTurnsExceeded",
    "compute_cost",
    "DEEPSEEK_PRICES",
    # embedding_cache
    "EmbeddingCache",
    # cached_encoder
    "CachedEncoder",
    # similarity
    "SimilarityScorer",
    # cli_pipeline
    "align_chapter",
    # quality_gate
    "assess_alignment_quality",
    "QualityGateConfig",
    "_gap_row_ratio",
    # report_io
    "save_report",
    "load_report",
    "set_ai_review",
]
