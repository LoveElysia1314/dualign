from __future__ import annotations

import json

import numpy as np

from dualign.core import AlignConfig
from dualign.models.action import RepairAction
from dualign.services.cli_pipeline import align_documents
from dualign.services.report_io import load_report, materialize_reader_rows, save_report


class MockEncoder:
    _model = "mock-diagonal"

    def encode(self, texts, normalize_embeddings=True, **_kwargs):
        if isinstance(texts, str):
            texts = [texts]
        vectors = np.eye(max(len(texts), 1), 8, dtype=np.float32)[: len(texts)]
        return vectors


def _pair(tmp_path):
    source = tmp_path / "chapter.source.md"
    target = tmp_path / "chapter.target.md"
    source.write_text("A\n\nB\n", encoding="utf-8")
    target.write_text("a\n\nb\n", encoding="utf-8")
    return source, target


def test_alignment_persists_only_a_report(tmp_path):
    source, target = _pair(tmp_path)
    report = tmp_path / "alignment" / "chapter.report.json"

    result = align_documents(str(source), str(target), str(report), model=MockEncoder())

    assert result["success"]
    assert result["report_path"] == str(report)
    assert report.is_file()
    assert not list(tmp_path.rglob("*.align.yaml"))
    assert not (report.parent / "chapter.source.md").exists()
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["format"] == "dualign-report"
    assert data["documents"]["a"]["sha256"]
    assert data["snapshot_fingerprint"]
    assert data["provenance"]["embedding"]["model"] == "mock-diagonal"


def test_matching_report_skips_model_and_stale_document_invalidates_it(tmp_path):
    source, target = _pair(tmp_path)
    report = tmp_path / "chapter.report.json"
    encoder = MockEncoder()
    first = align_documents(str(source), str(target), str(report), model=encoder)
    second = align_documents(str(source), str(target), str(report), model=encoder)
    assert first["success"] and second["cache_hit"]

    source.write_text("changed\n", encoding="utf-8")
    third = align_documents(str(source), str(target), str(report), model=encoder)
    assert third["success"] and not third["cache_hit"]


def test_alignment_configuration_is_part_of_report_cache_identity(tmp_path):
    source, target = _pair(tmp_path)
    report = tmp_path / "chapter.report.json"
    encoder = MockEncoder()
    assert align_documents(str(source), str(target), str(report), model=encoder)[
        "success"
    ]

    changed = align_documents(
        str(source),
        str(target),
        str(report),
        model=encoder,
        config=AlignConfig(anchor_min_score=0.42),
    )

    assert changed["success"] and not changed["cache_hit"]


def test_tool_release_metadata_does_not_invalidate_same_alignment(tmp_path):
    source, target = _pair(tmp_path)
    report = tmp_path / "chapter.report.json"
    encoder = MockEncoder()
    assert align_documents(str(source), str(target), str(report), model=encoder)[
        "success"
    ]
    data = load_report(report)
    data["provenance"]["tool_version"] = "future-ui-release"
    save_report(data, report)

    reused = align_documents(str(source), str(target), str(report), model=encoder)

    assert reused["cache_hit"] is True


def test_reset_work_state_reuses_alignment_but_discards_review_markers(tmp_path):
    source, target = _pair(tmp_path)
    report = tmp_path / "chapter.report.json"
    encoder = MockEncoder()
    first = align_documents(str(source), str(target), str(report), model=encoder)
    original_ops = first["ops"]
    stale = load_report(report)
    stale["repair_log"] = [RepairAction.make_flag(0, "旧标记").to_dict()]
    stale["ai_review"] = {"status": "completed"}
    stale["scores"] = {"0": 0.1}
    stale["history"] = [{"type": "old"}]
    save_report(stale, report)

    reset = align_documents(
        str(source),
        str(target),
        str(report),
        model=encoder,
        reset_work_state=True,
    )

    assert reset["success"] and reset["cache_hit"]
    assert reset["work_state_reset"]
    assert reset["ops"] == original_ops
    rebuilt = load_report(report)
    assert all(item["kind"] != "flag" for item in rebuilt["repair_log"])
    assert rebuilt["ai_review"] == {}
    assert rebuilt["scores"] == {}
    assert rebuilt["history"] == []


def test_disabling_alignment_reuse_recomputes_and_replaces_old_report(tmp_path):
    source, target = _pair(tmp_path)
    report = tmp_path / "chapter.report.json"
    encoder = MockEncoder()
    assert align_documents(str(source), str(target), str(report), model=encoder)[
        "success"
    ]
    stale = load_report(report)
    stale["ops"] = [{"s": [0, 1], "t": [0, 1], "sc": 0.01}]
    stale["repair_log"] = [RepairAction.make_flag(0, "旧标记").to_dict()]
    save_report(stale, report)

    rebuilt_result = align_documents(
        str(source),
        str(target),
        str(report),
        model=encoder,
        reset_work_state=True,
        reuse_alignment=False,
    )

    assert rebuilt_result["success"] and not rebuilt_result["cache_hit"]
    rebuilt = load_report(report)
    assert rebuilt["ops"] != stale["ops"]
    assert all(item["kind"] != "flag" for item in rebuilt["repair_log"])


def test_preserved_work_state_repairs_only_unresolved_relations(tmp_path):
    source, target = _pair(tmp_path)
    report = tmp_path / "chapter.report.json"
    encoder = MockEncoder()
    first = align_documents(str(source), str(target), str(report), model=encoder)
    stale = load_report(report)
    stale["ops"] = [
        {"s": [0, 1], "t": [0], "sc": 0.5},
        {"s": [], "t": [1], "sc": 0.0},
    ]
    stale["repair_log"] = [
        RepairAction.make_flag(0, "仍需人工确认").to_dict(),
        RepairAction.make_ok(1).to_dict(),
    ]
    save_report(stale, report)

    result = align_documents(
        str(source),
        str(target),
        str(report),
        model=encoder,
        reset_work_state=True,
        reuse_alignment=True,
        preserve_work_state=True,
    )

    assert result["success"] and result["cache_hit"]
    actions = load_report(report)["repair_log"]
    assert [a["kind"] for a in actions if a["op_index"] == 0] == ["merge", "flag"]
    assert [a["kind"] for a in actions if a["op_index"] == 1] == ["ok"]


def test_reader_rows_are_materialized_on_demand(tmp_path):
    source, target = _pair(tmp_path)
    report = tmp_path / "chapter.report.json"
    assert align_documents(str(source), str(target), str(report), model=MockEncoder())[
        "success"
    ]

    source_rows, target_rows = materialize_reader_rows(report, source, target)

    assert len(source_rows) == len(target_rows)
    assert source_rows


def test_empty_document_still_produces_a_replayable_report(tmp_path):
    source = tmp_path / "empty.source.md"
    target = tmp_path / "empty.target.md"
    source.write_text("", encoding="utf-8")
    target.write_text("one\n", encoding="utf-8")
    report = tmp_path / "empty.report.json"

    result = align_documents(str(source), str(target), str(report), model=MockEncoder())

    assert result["success"]
    assert result["quality"] == "unreliable"
    assert json.loads(report.read_text(encoding="utf-8"))["ops"] == [
        {"s": [], "t": [0], "sc": 0.0}
    ]
    assert load_report(report)["repair_log"][0]["kind"] == "delete"
