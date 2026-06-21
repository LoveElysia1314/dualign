"""
Dualign — Marker 编解码测试
"""

from dualign.models.marker import (
    from_kind,
    parse,
    get_tags,
    get_source,
    get_display_text,
    is_merge,
    is_split,
    is_edit,
    is_deleted,
    is_placeholder,
    is_flagged,
    is_approved,
    is_from_ai,
    is_ai_reviewed,
    is_resolved_to_11,
    combine,
    KIND_MAP,
    has_tag,
    AI_PREFIX,
)


class TestMarkerConstruct:
    def test_basic_kind(self):
        assert from_kind("merge") == "[M]"
        assert from_kind("split") == "[S]"
        assert from_kind("edit") == "[E]"
        assert from_kind("delete") == "[D]"
        assert from_kind("flag") == "[F]"
        assert from_kind("ok") == "[OK]"
        assert from_kind("placeholder_src") == "[P]"

    def test_with_ai_source(self):
        # AI 来源：操作标记加 [AI] 前缀
        assert from_kind("merge", source="ai") == "[AI][M]"
        assert from_kind("edit", source="ai") == "[AI][E]"
        assert from_kind("delete", source="ai") == "[AI][D]"
        assert from_kind("flag", source="ai") == "[AI][F]"
        # AI ok → [AI][OK]（与其他操作一致，不再有裸 [AI] 特例）
        assert from_kind("ok", source="ai") == "[AI][OK]"

    def test_ok_human_source(self):
        # 人类 ok → [OK]
        assert from_kind("ok", source="") == "[OK]"
        assert from_kind("ok", source="user") == "[OK]"

    def test_unknown_kind(self):
        assert from_kind("invalid") == ""

    def test_combine_ok_removes_ai(self):
        # [OK] 叠加时自动剥离 [AI] 前缀
        assert combine("[AI][M]", "[OK]") == "[M] [OK]"

    def test_combine_ok_removes_f(self):
        assert combine("[M] [F]", "[OK]") == "[M] [OK]"

    def test_combine_f_removes_ok(self):
        assert combine("[M] [OK]", "[F]") == "[M] [F]"

    def test_combine_f_removes_ai_prefix(self):
        # [F] 是人类操作，叠加时剥离 [AI] 前缀
        assert combine("[AI][E]", "[F]") == "[E] [F]"
        assert combine("[AI][M]", "[F]") == "[M] [F]"

    def test_combine_ok_removes_ai_prefix(self):
        # [OK] 是人类操作，叠加时剥离 [AI] 前缀
        assert combine("[AI][S]", "[OK]") == "[S] [OK]"

    def test_combine_no_duplicate(self):
        assert combine("[M]", "[M]") == "[M]"
        assert combine("[M] [OK]", "[OK]") == "[M] [OK]"

    def test_combine_empty(self):
        assert combine("", "[OK]") == "[OK]"


class TestMarkerParse:
    def test_parse_single(self):
        r = parse("[M]")
        assert r["[M]"] is True
        assert r["[OK]"] is False

    def test_parse_ai_merge_ok(self):
        r = parse("[AI][M] [OK]")
        assert r["[AI]"] is True
        assert r["[M]"] is True
        assert r["[OK]"] is True

    def test_parse_empty(self):
        r = parse("")
        assert all(not v for v in r.values())

    def test_get_tags_includes_present(self):
        tags = get_tags("[AI][M] [OK] [F]")
        assert "[M]" in tags and "[F]" in tags and "[OK]" in tags

    def test_get_tags_empty(self):
        assert get_tags("") == []

    def test_get_source(self):
        assert get_source("[AI][M]") == "ai"
        assert get_source("[M]") == ""
        assert get_source("") == ""

    def test_roundtrip(self):
        for kind in ("merge", "split", "edit", "delete", "ok"):
            m = from_kind(kind)
            assert parse(m)[KIND_MAP[kind]] is True


class TestMarkerSemanticQueries:
    def test_is_merge(self):
        assert is_merge("[M]") and is_merge("[AI][M]")
        assert not is_merge("[S]") and not is_merge("")

    def test_is_split(self):
        assert is_split("[S]") and not is_split("[M]")

    def test_is_edit(self):
        assert is_edit("[E]") and not is_edit("[M]")

    def test_is_deleted(self):
        assert is_deleted("[D]") and not is_deleted("[M]")

    def test_is_placeholder(self):
        assert is_placeholder("[P]") and not is_placeholder("[D]")

    def test_is_flagged(self):
        assert is_flagged("[F]") and not is_flagged("[OK]")

    def test_is_approved(self):
        assert is_approved("[OK]") and not is_approved("[M]")

    def test_is_from_ai(self):
        assert is_from_ai("[AI][M]") and not is_from_ai("[M]")

    def test_is_resolved_to_11(self):
        for m in ("[M]", "[S]", "[P]", "[OK]"):
            assert is_resolved_to_11(m)
        for m in ("[E]", "[D]", "[F]"):
            assert not is_resolved_to_11(m)

    def test_has_tag(self):
        assert has_tag("[AI][M]", "[M]") and not has_tag("[M]", "[S]")

    def test_empty_marker(self):
        assert not is_merge("") and not is_approved("") and not is_from_ai("")


class TestIsAiReviewed:
    """验证 is_ai_reviewed() — marker 是否以 [AI] 开头。"""

    def test_pure_ai(self):
        assert is_ai_reviewed("[AI]")

    def test_ai_with_op(self):
        assert is_ai_reviewed("[AI][E]")
        assert is_ai_reviewed("[AI][M]")
        assert is_ai_reviewed("[AI][F]")

    def test_no_ai_prefix(self):
        assert not is_ai_reviewed("[E] [OK]")
        assert not is_ai_reviewed("[M]")
        assert not is_ai_reviewed("[OK]")

    def test_empty_or_none(self):
        assert not is_ai_reviewed("")


class TestMarkerDisplay:
    def test_display(self):
        assert get_display_text("[M]") == "合并"
        assert get_display_text("[AI][E]") == "AI 校订"
        assert get_display_text("") == ""
