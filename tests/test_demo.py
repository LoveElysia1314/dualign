from dualign.demo import create_demo_working_pair, get_demo_source_paths


def test_demo_runs_use_independent_writable_copies(tmp_path):
    source_a, source_b = get_demo_source_paths()
    original_a = source_a.read_bytes()
    original_b = source_b.read_bytes()

    first_a, first_b, first_workspace = create_demo_working_pair(tmp_path)
    first_a.write_text("changed by first run\n", encoding="utf-8")
    first_b.write_text("changed by first run\n", encoding="utf-8")
    second_a, second_b, second_workspace = create_demo_working_pair(tmp_path)

    assert first_workspace != second_workspace
    assert first_a.parent == first_workspace
    assert second_a.parent == second_workspace
    assert source_a.read_bytes() == original_a
    assert source_b.read_bytes() == original_b
    assert second_a.read_bytes() == original_a
    assert second_b.read_bytes() == original_b
    assert all(path.is_file() for path in (first_a, first_b, second_a, second_b))
