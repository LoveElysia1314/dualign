"""Dualign 服务层。"""

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
from dualign.services.cli_pipeline import align_documents
from dualign.services.pair_save import (
    PairSaveConflictError,
    PairSaveError,
    PairSaveResult,
    recover_pending_pair_saves,
    save_pair_transaction,
)
from dualign.services.change_set import (
    PairChangeSet,
    build_pair_change_set,
)
from dualign.services.pair_editing_adapter import (
    apply_repair_log_to_pair_state,
    link_id_for_operation,
)
from dualign.services.quality_gate import (
    assess_alignment_quality,
    QualityGateConfig,
    _gap_row_ratio,
)
from dualign.services.report_io import (
    ReportError,
    load_report,
    materialize_reader_rows,
    save_report,
    set_ai_review,
)

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
    "align_documents",
    # source overwrite transaction
    "PairSaveConflictError",
    "PairSaveError",
    "PairSaveResult",
    "recover_pending_pair_saves",
    "PairChangeSet",
    "build_pair_change_set",
    "save_pair_transaction",
    # editing bridge
    "apply_repair_log_to_pair_state",
    "link_id_for_operation",
    # quality_gate
    "assess_alignment_quality",
    "QualityGateConfig",
    "_gap_row_ratio",
    # report_io
    "save_report",
    "load_report",
    "set_ai_review",
    "ReportError",
    "materialize_reader_rows",
]
