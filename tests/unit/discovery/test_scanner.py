from pathlib import Path

from pptlib.discovery.scanner import scan_paths, scan_source_root


def test_scan_source_root_hashes_pptx_and_skips_lock_files(tmp_path: Path) -> None:
    (tmp_path / "deck.pptx").write_bytes(b"pptx")
    (tmp_path / "~$deck.pptx").write_bytes(b"lock")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "other.pptx").write_bytes(b"other")
    result = scan_source_root(tmp_path)
    assert [item.path.name for item in result] == ["deck.pptx", "other.pptx"]
    assert result[0].size_bytes == 4
    assert len(result[0].sha256) == 64


def test_scan_paths_indexes_files_in_place_and_dedupes(tmp_path: Path) -> None:
    # explicit files are indexed at their real path (nothing is copied)
    a = tmp_path / "a.pptx"
    a.write_bytes(b"aaaa")
    sub = tmp_path / "sub"
    sub.mkdir()
    b = sub / "b.pptx"
    b.write_bytes(b"bbbb")
    lock = sub / "~$b.pptx"
    lock.write_bytes(b"lock")

    # mix a file and a directory; the file inside the dir must not be duplicated
    result = scan_paths([a, sub, b])
    paths = [item.path for item in result]
    assert a.resolve() in paths
    assert b.resolve() in paths
    assert lock.resolve() not in paths
    # de-duplicated: b appears once even though passed via dir and explicitly
    assert paths.count(b.resolve()) == 1


def test_scan_paths_skips_non_pptx_and_missing(tmp_path: Path) -> None:
    txt = tmp_path / "note.txt"
    txt.write_bytes(b"hi")
    result = scan_paths([txt, tmp_path / "missing.pptx"])
    assert result == []
