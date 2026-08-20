from dualign.services.quality_gate import (
    assess_alignment_quality,
    automatic_repair_blockers,
)


def test_anchor_density_fallback_counts_both_sides_of_each_anchor():
    result = assess_alignment_quality(
        {"n_true_anchors": 6},
        n_src=10,
        n_tgt=10,
        gap_row_ratio=0.0,
    )

    assert result["indicators"]["anchor_density"] == 0.6
    assert result["quality"] == "ok"


def test_quality_assessment_keeps_all_independent_rejection_reasons():
    result = assess_alignment_quality(
        {"n_true_anchors": 1},
        n_src=100,
        n_tgt=200,
        gap_row_ratio=0.4,
        n_overflow_rows=100,
    )

    assert set(result["rejections"]) == {
        "low_anchor_density",
        "gap_dominated",
        "merge_overflow",
    }
    assert automatic_repair_blockers(result) == result["rejections"]
