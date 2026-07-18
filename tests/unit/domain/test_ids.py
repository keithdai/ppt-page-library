from pptlib.domain.ids import new_id


def test_new_id_has_prefix_and_is_unique() -> None:
    first = new_id("job")
    second = new_id("job")

    assert first.startswith("job_")
    assert second.startswith("job_")
    assert first != second
