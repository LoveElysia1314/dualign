import hashlib
import json
from pathlib import Path

import pytest

from dualign.models.alignment_pair import (
    AlignmentLink,
    AlignmentPair,
    DocumentReference,
)
from dualign.models.pair_editing import PairEditingState
from dualign.services.alignment_io import document_sha256
from dualign.services.pair_save import (
    PairSaveConflictError,
    PairSaveError,
    PairSavePlaceholderError,
    recover_pending_pair_saves,
    save_pair_transaction,
)
from dualign.services.report_io import build_report, load_report, save_report


def _case(tmp_path: Path):
    path_a = tmp_path / "a.md"
    path_b = tmp_path / "b.md"
    report_path = tmp_path / "pair.report.json"
    path_a.write_text("甲\n\n乙\n", encoding="utf-8")
    path_b.write_text("A\n", encoding="utf-8")
    pair = AlignmentPair(
        id="pair",
        document_a=DocumentReference("a", "a.md", sha256=document_sha256(path_a)),
        document_b=DocumentReference("b", "b.md", sha256=document_sha256(path_b)),
        links=(AlignmentLink("L1", (1, 2), (1,), state="confirmed"),),
    )
    state = PairEditingState.from_alignment_pair(
        pair, path_a.read_text(encoding="utf-8"), path_b.read_text(encoding="utf-8")
    )
    report = build_report(
        chapter_id="pair",
        document_a_path=path_a,
        document_b_path=path_b,
        operations=[((0, 1), (0,), 0.9)],
        stats={"n_source": 2, "n_target": 1, "n_ops": 1},
        quality={"level": "ok", "rejections": [], "indicators": {}},
        provenance={"tool": "test"},
    )
    save_report(report, report_path)
    return path_a, path_b, report_path, state


def _save(state, path_a, path_b, report_path, **kwargs):
    return save_pair_transaction(
        state,
        document_a_path=path_a,
        document_b_path=path_b,
        report_path=report_path,
        report=load_report(report_path),
        **kwargs,
    )


def test_three_file_save_updates_documents_and_rebases_report(tmp_path: Path):
    path_a, path_b, report_path, state = _case(tmp_path)
    expected = hashlib.sha256(report_path.read_bytes()).hexdigest()
    edited = state.edit_link_content(
        "L1", document_a=["甲", "乙校订"], document_b=["A edited"]
    )
    transactions = tmp_path / "transactions"

    result = _save(
        edited,
        path_a,
        path_b,
        report_path,
        expected_report_sha256=expected,
        transaction_dir=transactions,
    )

    assert path_a.read_text(encoding="utf-8") == "甲\n\n乙校订\n"
    assert path_b.read_text(encoding="utf-8") == "A edited\n"
    saved = load_report(report_path)
    assert saved["ops"][0]["s"] == [0, 1]
    assert saved["documents"]["a"]["sha256"] == document_sha256(path_a)
    assert saved["repair_log"] == []
    assert saved["history"][-1]["type"] == "source-overwrite"
    assert result.report_sha256
    assert list(transactions.glob("*")) == []


def test_external_document_change_refuses_all_writes(tmp_path: Path):
    path_a, path_b, report_path, state = _case(tmp_path)
    original_report = report_path.read_bytes()
    path_b.write_text("external\n", encoding="utf-8")

    with pytest.raises(PairSaveConflictError, match="文档 B"):
        _save(state, path_a, path_b, report_path)

    assert report_path.read_bytes() == original_report


def test_report_created_after_open_refuses_all_writes(tmp_path: Path):
    path_a, path_b, report_path, state = _case(tmp_path)
    report = load_report(report_path)

    with pytest.raises(PairSaveConflictError, match="工作报告"):
        save_pair_transaction(
            state,
            document_a_path=path_a,
            document_b_path=path_b,
            report_path=report_path,
            report=report,
            expected_report_exists=False,
        )


def test_mid_transaction_failure_rolls_back_every_target(tmp_path: Path, monkeypatch):
    path_a, path_b, report_path, state = _case(tmp_path)
    originals = (path_a.read_bytes(), path_b.read_bytes(), report_path.read_bytes())
    edited = state.edit_link_content("L1", document_b=["changed"])
    calls = 0
    from dualign.services import pair_save

    real_install = pair_save._install_prepared

    def fail_second_install(temp_path, target_path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected failure")
        real_install(temp_path, target_path)

    monkeypatch.setattr(pair_save, "_install_prepared", fail_second_install)
    with pytest.raises(PairSaveError, match="injected failure"):
        _save(
            edited,
            path_a,
            path_b,
            report_path,
            transaction_dir=tmp_path / "transactions",
        )

    assert (
        path_a.read_bytes(),
        path_b.read_bytes(),
        report_path.read_bytes(),
    ) == originals


def test_recovery_rolls_back_an_interrupted_install(tmp_path: Path):
    target = tmp_path / "a.md"
    backup = tmp_path / ".a.rollback"
    temporary = tmp_path / ".a.tmp"
    target.write_text("new", encoding="utf-8")
    backup.write_text("old", encoding="utf-8")
    temporary.write_text("prepared", encoding="utf-8")
    transaction_dir = tmp_path / "transactions"
    transaction_dir.mkdir()
    journal = transaction_dir / "pair-save-test.json"
    journal.write_text(
        json.dumps(
            {
                "id": "test",
                "targets": [
                    {
                        "path": str(target),
                        "backup": str(backup),
                        "temporary": str(temporary),
                        "existed": True,
                        "new_sha256": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert recover_pending_pair_saves(transaction_dir) == ["已回滚未完成保存: test"]
    assert target.read_text(encoding="utf-8") == "old"


def test_save_refuses_missing_placeholder_in_document_a(tmp_path: Path):
    """固化防线：⟢MISSING⟣ 占位符绝不允许写入正文文档。"""
    path_a, path_b, report_path, state = _case(tmp_path)
    expected = hashlib.sha256(report_path.read_bytes()).hexdigest()
    placeholder = "\u27e2MISSING\u27e3"
    edited = state.edit_link_content(
        "L1", document_a=["甲", placeholder], document_b=["A"]
    )

    with pytest.raises(PairSavePlaceholderError, match="文档 A"):
        _save(
            edited,
            path_a,
            path_b,
            report_path,
            expected_report_sha256=expected,
            transaction_dir=tmp_path / "transactions",
        )

    # 任何文件都不应被写入
    assert path_a.read_text(encoding="utf-8") == "甲\n\n乙\n"
    assert path_b.read_text(encoding="utf-8") == "A\n"


def test_save_refuses_missing_placeholder_in_document_b(tmp_path: Path):
    """译文侧占位符同样被拒绝。"""
    path_a, path_b, report_path, state = _case(tmp_path)
    placeholder = "\u27e2MISSING\u27e3"
    edited = state.edit_link_content("L1", document_a=["甲"], document_b=[placeholder])

    with pytest.raises(PairSavePlaceholderError, match="文档 B"):
        _save(edited, path_a, path_b, report_path)

    assert path_b.read_text(encoding="utf-8") == "A\n"


def test_save_allows_embedded_missing_symbol_in_prose(tmp_path: Path):
    """正文文本中内嵌的符号（非独立占位符行）不拦截——可能是正常引用。"""
    path_a, path_b, report_path, state = _case(tmp_path)
    placeholder = "\u27e2MISSING\u27e3"
    edited = state.edit_link_content(
        "L1", document_a=["甲", "符号 " + placeholder + " 出现于文中"], document_b=["A"]
    )

    result = _save(
        edited,
        path_a,
        path_b,
        report_path,
        transaction_dir=tmp_path / "transactions",
    )
    assert result.report_sha256
    assert (
        path_a.read_text(encoding="utf-8")
        == "甲\n\n符号 " + placeholder + " 出现于文中\n"
    )
