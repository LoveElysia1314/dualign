import json

from dualign.__main__ import _load_gui_entries
from dualign.common import content_hash
from dualign.gui.workers import EncodeThread


def test_load_gui_entries_preserves_project_paths(tmp_path):
    manifest = tmp_path / "entries.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "entry_id": "001",
                    "label": "第一章",
                    "source_path": "raw/001.source.md",
                    "target_path": "raw/001.target.md",
                    "repaired_dir": "repaired",
                    "report_path": "repaired/001.report.json",
                    "metadata": {"novel_id": "demo"},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    entries = _load_gui_entries(str(manifest))

    assert len(entries) == 1
    assert entries[0].entry_id == "001"
    assert entries[0].repaired_dir == "repaired"
    assert entries[0].report_path == "repaired/001.report.json"
    assert entries[0].metadata == {"novel_id": "demo"}


def test_encode_thread_restores_valid_alignment_before_encoding(tmp_path):
    src_lines = ["原文一", "原文二"]
    tgt_lines = ["译文一", "译文二"]
    report = tmp_path / "001.report.json"
    report.write_text(
        json.dumps(
            {
                "src_hash": content_hash(src_lines),
                "tgt_hash": content_hash(tgt_lines),
                "ops": [
                    {"s": [0], "t": [0], "sc": 0.9},
                    {"s": [1], "t": [1], "sc": 0.8},
                ],
                "stats": {"n_source": 2, "n_target": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    worker = EncodeThread("src.md", "tgt.md", report_path=str(report))

    result = worker._load_cached_alignment(
        content_hash(src_lines), content_hash(tgt_lines)
    )

    assert result is not None
    assert len(result.all_ops) == 2
    assert result.stats["n_source"] == 2


def test_encode_thread_cache_hit_skips_preview_and_model(tmp_path, monkeypatch):
    src_lines = ["原文一", "原文二"]
    tgt_lines = ["译文一", "译文二"]
    src = tmp_path / "001.source.md"
    tgt = tmp_path / "001.target.md"
    src.write_text("\n".join(src_lines), encoding="utf-8")
    tgt.write_text("\n".join(tgt_lines), encoding="utf-8")
    report = tmp_path / "001.report.json"
    report.write_text(
        json.dumps(
            {
                "src_hash": content_hash(src_lines),
                "tgt_hash": content_hash(tgt_lines),
                "ops": [{"s": [0], "t": [0], "sc": 0.9}],
                "stats": {"n_source": 2, "n_target": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    worker = EncodeThread(str(src), str(tgt), report_path=str(report))
    cache_hits = []
    previews = []
    worker.cache_hit_signal.connect(cache_hits.append)
    worker.text_ready_signal.connect(lambda *args: previews.append(args))
    monkeypatch.setattr(
        "dualign.gui.workers._try_lazy_load_model",
        lambda: (_ for _ in ()).throw(AssertionError("缓存命中时不应加载模型")),
    )

    worker._run_impl()

    assert len(cache_hits) == 1
    assert previews == []


def test_encode_thread_rejects_stale_alignment_report(tmp_path):
    report = tmp_path / "001.report.json"
    report.write_text(
        json.dumps(
            {
                "src_hash": "old-source",
                "tgt_hash": "old-target",
                "ops": [{"s": [0], "t": [0], "sc": 0.9}],
                "stats": {},
            }
        ),
        encoding="utf-8",
    )
    worker = EncodeThread("src.md", "tgt.md", report_path=str(report))

    assert worker._load_cached_alignment("new-source", "new-target") is None
