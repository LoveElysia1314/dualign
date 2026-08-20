"""Workspace selection has one event carrying the complete queue item."""

from dualign.common import FilePair
from dualign.gui.workspace import FileQueueItem
from dualign.gui.window_actions import WindowActionsMixin


class _WorkspaceStub:
    def __init__(self, queue=None):
        self._queue = list(queue or [])
        self._selected = self._queue[0] if self._queue else None

    def selected_item(self):
        return self._selected

    def set_file_paths(self, src_path, tgt_path, label):
        pass


class _Harness(WindowActionsMixin):
    """轻量测试替身：只记录 load_file_pair 收到的参数。"""

    def __init__(self, workspace=None):
        self._workspace = workspace
        self.loaded = []

    def load_file_pair(
        self,
        src_path,
        tgt_path,
        label="",
        *,
        alignment_path="",
        document_a_id="",
        document_b_id="",
        language_a="",
        language_b="",
    ):
        self.loaded.append(
            {
                "src": src_path,
                "tgt": tgt_path,
                "label": label,
                "alignment_path": alignment_path,
                "document_a_id": document_a_id,
                "document_b_id": document_b_id,
                "language_a": language_a,
                "language_b": language_b,
            }
        )


def _make_queue_item(entry: FilePair) -> FileQueueItem:
    return FileQueueItem(
        label=entry.label,
        src_path=entry.document_a_path,
        tgt_path=entry.document_b_path,
        entry=entry,
    )


def test_workspace_selection_keeps_entry_alignment_path():
    entry = FilePair(
        entry_id="one",
        label="One",
        document_a_path="a.md",
        document_b_path="b.md",
        report_path="alignment/one.report.json",
        language_a="zh-Hans",
        language_b="en",
    )
    item = _make_queue_item(entry)
    h = _Harness(_WorkspaceStub([item]))

    h._on_workspace_pair_selected(item)

    assert len(h.loaded) == 1
    call = h.loaded[0]
    assert call["src"] == "a.md"
    assert call["tgt"] == "b.md"
    # 关键断言：报告路径必须保留，不得回退到 raw/ 默认路径
    assert call["alignment_path"] == "alignment/one.report.json"
    assert call["language_a"] == "zh-Hans"
    assert call["language_b"] == "en"


def test_workspace_selection_without_entry_uses_plain_paths():
    item = FileQueueItem(label="Plain", src_path="p.md", tgt_path="q.md")
    h = _Harness(_WorkspaceStub([item]))

    h._on_workspace_pair_selected(item)

    assert len(h.loaded) == 1
    assert h.loaded[0]["alignment_path"] == ""


def test_workspace_align_checked_keeps_entry_alignment_path():
    entry = FilePair(
        entry_id="one",
        label="One",
        document_a_path="a.md",
        document_b_path="b.md",
        report_path="alignment/one.report.json",
    )
    h = _Harness(_WorkspaceStub([_make_queue_item(entry)]))

    h._on_workspace_align_checked()

    assert len(h.loaded) == 1
    assert h.loaded[0]["alignment_path"] == "alignment/one.report.json"
