from __future__ import annotations

import json

import numpy as np

from dualign.services.cli_pipeline import align_documents
from dualign.services.report_io import materialize_reader_rows


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
