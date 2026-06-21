"""Dualign 0.7.0 — 核心算法模块（无状态纯函数）"""

from dualign.core.aligner import (
    AlignConfig,
    AlignmentResult,
    align,
    op_type_str,
    count_punct_info,
    pair_score,
    find_bilateral_anchors,
    bilateral_trust_margin,
    select_monotonic_anchors_weighted,
    ALIGN_CORE_VERSION,
    _normalize,
    _smart_join_lines,
)

from dualign.core.punctuation import (
    PunctuationHandler,
    UniversalSplitter,
    calculate_punctuation_similarity,
    detect_language_mix,
)

from dualign.core.file_pair_matcher import (
    FilePairMatcher,
    MatchRule,
    MatchedPair,
)

__all__ = [
    "AlignConfig",
    "AlignmentResult",
    "align",
    "op_type_str",
    "count_punct_info",
    "pair_score",
    "find_bilateral_anchors",
    "bilateral_trust_margin",
    "select_monotonic_anchors_weighted",
    "ALIGN_CORE_VERSION",
    "_normalize",
    "_smart_join_lines",
    "PunctuationHandler",
    "UniversalSplitter",
    "calculate_punctuation_similarity",
    "detect_language_mix",
    "FilePairMatcher",
    "MatchRule",
    "MatchedPair",
]
