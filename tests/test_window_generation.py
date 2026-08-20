from dualign.core import AlignmentResult
from dualign.gui.window_actions import WindowActionsMixin


class _Harness(WindowActionsMixin):
    def __init__(self):
        self._load_op_id = 2
        self.src_lines = ["current-source"]
        self.tgt_lines = ["current-target"]
        self.src_emb = "current-embedding"


def test_stale_load_callbacks_cannot_mutate_current_document():
    window = _Harness()
    stale_result = AlignmentResult([], [], {}, {})

    window._on_text_ready(1, "old-a", "old-b", ["old"], ["old"])
    window._on_encoded(1, "old-embedding", None, ["old"], ["old"], "a", "b")
    window._on_alignment_cache_hit(
        1, (stale_result, ["old"], ["old"], "old-a", "old-b")
    )
    window._on_align_done(1, stale_result)

    assert window.src_lines == ["current-source"]
    assert window.tgt_lines == ["current-target"]
    assert window.src_emb == "current-embedding"
