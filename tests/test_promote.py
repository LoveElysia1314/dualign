import json
from pathlib import Path

from dualign.common import load_text_lines, promote_repaired
from dualign.gui.window import DualignWindow
from dualign.gui.window_actions import WindowActionsMixin
from dualign.models.action import RepairAction


def _promotion_case(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    repaired_dir = tmp_path / "repaired"
    raw_dir.mkdir()
    repaired_dir.mkdir()

    src = raw_dir / "chapter.source.md"
    tgt = raw_dir / "chapter.target.md"
    repaired_src = repaired_dir / "chapter.source.md"
    repaired_tgt = repaired_dir / "chapter.target.md"
    report = repaired_dir / "chapter.report.json"
    sim = repaired_dir / "chapter.sim.npy"

    src.write_text("A\n\nB\n", encoding="utf-8")
    tgt.write_text("a\n\nb\n", encoding="utf-8")
    repaired_src.write_text("STALE SOURCE\n", encoding="utf-8")
    repaired_tgt.write_text("STALE TARGET\n", encoding="utf-8")
    action = RepairAction.make_edit(
        0,
        source="user",
        new_src_lines=["X"],
        new_tgt_lines=["Y"],
        scores=[0.95],
    )
    report.write_text(
        json.dumps(
            {
                "src_hash": "old-source",
                "tgt_hash": "old-target",
                "ops": [
                    {"s": [0], "t": [0], "sc": 0.9},
                    {"s": [1], "t": [1], "sc": 0.8},
                ],
                "repair_log": [action.to_dict()],
                "ai_review": {"status": "completed"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sim.write_bytes(b"old similarity")
    return src, tgt, repaired_dir, repaired_src, repaired_tgt, report, sim


def test_dry_run_is_read_only_and_previews_logged_repairs(tmp_path, monkeypatch):
    src, tgt, repaired_dir, repaired_src, repaired_tgt, report, _sim = _promotion_case(
        tmp_path
    )
    before = {
        path: path.read_bytes()
        for path in (src, tgt, repaired_src, repaired_tgt, report)
    }
    cache_root = tmp_path / "absent-cache"
    monkeypatch.setenv("DUALIGN_CACHE_DIR", str(cache_root))

    result = promote_repaired(
        "chapter",
        str(src),
        str(tgt),
        str(repaired_dir),
        dry_run=True,
    )

    assert result["success"] is True
    assert result["src_count"] == result["tgt_count"] == 2
    assert result["report_backup"].endswith(".pre-promote.bak")
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not cache_root.exists()


def test_promote_replays_repairs_then_invalidates_derived_state(tmp_path):
    src, tgt, repaired_dir, repaired_src, repaired_tgt, report, sim = _promotion_case(
        tmp_path
    )

    result = promote_repaired("chapter", str(src), str(tgt), str(repaired_dir))

    assert result["success"] is True
    assert load_text_lines(str(src)) == ["X", "B"]
    assert load_text_lines(str(tgt)) == ["Y", "b"]
    assert load_text_lines(str(src) + ".bak") == ["A", "B"]
    assert load_text_lines(str(tgt) + ".bak") == ["a", "b"]
    assert not report.exists()
    assert not repaired_src.exists()
    assert not repaired_tgt.exists()
    assert not sim.exists()

    report_backup = Path(result["report_backup"])
    archived = json.loads(report_backup.read_text(encoding="utf-8"))
    assert archived["repair_log"]
    assert archived["ai_review"]["status"] == "completed"


def test_strategy_uses_replayed_result_and_rejects_without_mutation(tmp_path):
    src, tgt, repaired_dir, repaired_src, repaired_tgt, report, _sim = _promotion_case(
        tmp_path
    )
    before = {path: path.read_bytes() for path in (src, tgt, repaired_src, report)}

    result = promote_repaired(
        "chapter", str(src), str(tgt), str(repaired_dir), strategy="src"
    )

    assert result["success"] is False
    assert "策略拒绝" in result["message"]
    assert all(path.read_bytes() == content for path, content in before.items())


def test_window_promote_entry_delegates_to_actions_mixin():
    assert DualignWindow._on_promote is WindowActionsMixin._on_promote


def test_promote_rejects_overlapping_raw_and_repaired_paths(tmp_path):
    source = tmp_path / "chapter.source.md"
    target = tmp_path / "chapter.target.md"
    source.write_text("source\n", encoding="utf-8")
    target.write_text("target\n", encoding="utf-8")

    result = promote_repaired("chapter", str(source), str(target), str(tmp_path))

    assert result["success"] is False
    assert "路径发生重叠" in result["message"]
    assert source.exists() and target.exists()
