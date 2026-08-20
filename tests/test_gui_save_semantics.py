import hashlib

from dualign.gui.window_actions import WindowActionsMixin
from dualign.models.action import RepairAction
from dualign.services.repair import RepairState
from dualign.services.report_io import build_report, load_report, save_report


class _Harness(WindowActionsMixin):
    def __init__(self, tmp_path):
        self._src_path = str(tmp_path / "a.md")
        self._tgt_path = str(tmp_path / "b.md")
        self._alignment_path = str(tmp_path / "pair.report.json")
        (tmp_path / "a.md").write_text("甲\n乙\n", encoding="utf-8")
        (tmp_path / "b.md").write_text("A\n", encoding="utf-8")
        self._repair_state = RepairState.from_ops(
            [((0, 1), (0,), 0.9)], ["甲", "乙"], ["A"]
        )
        report = build_report(
            chapter_id="pair",
            document_a_path=self._src_path,
            document_b_path=self._tgt_path,
            operations=self._repair_state.original_ops,
            stats={"n_source": 2, "n_target": 1},
            quality={"level": "ok"},
            provenance={"tool": "test"},
        )
        save_report(report, self._alignment_path)
        self._alignment_file_hash = hashlib.sha256(
            (tmp_path / "pair.report.json").read_bytes()
        ).hexdigest()
        self._alignment_file_present = True
        self._score_cache = {}

    def _set_temp_status(self, *_args, **_kwargs):
        pass


def test_save_records_relation_decision_without_touching_documents(tmp_path):
    harness = _Harness(tmp_path)
    action = RepairAction.make_ok(0)
    action.source = "user"
    harness._repair_state = harness._repair_state.apply(action)

    assert harness._on_save_alignment() is True

    assert load_report(harness._alignment_path)["repair_log"][0]["kind"] == "ok"
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "甲\n乙\n"
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == "A\n"


def test_save_records_content_edit_without_implicitly_overwriting_sources(tmp_path):
    harness = _Harness(tmp_path)
    action = RepairAction.make_edit(
        0,
        source="user",
        new_src_lines=["甲校订", "乙"],
        new_tgt_lines=["A"],
    )
    harness._repair_state = harness._repair_state.apply(action)

    assert harness._on_save_alignment() is True

    assert load_report(harness._alignment_path)["repair_log"][0]["kind"] == "edit"
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "甲\n乙\n"
