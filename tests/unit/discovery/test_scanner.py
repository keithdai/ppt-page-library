from pathlib import Path

from pptlib.discovery import scanner
from pptlib.discovery.scanner import (
    ScanRules,
    discover_paths,
    discover_paths_with_diagnostics,
    scan_paths,
    scan_source_root,
)


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


def test_discover_paths_does_not_hash_file_contents(monkeypatch, tmp_path: Path) -> None:
    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"pptx")

    def unexpected_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
        raise AssertionError("metadata discovery must not hash file contents")

    monkeypatch.setattr(scanner, "_hash_file", unexpected_hash)
    result = discover_paths([tmp_path])

    assert len(result) == 1
    assert result[0].path == deck.resolve()
    assert result[0].size_bytes == 4


def test_diagnostic_discovery_reports_format_size_hidden_and_temporary(
    tmp_path: Path,
) -> None:
    (tmp_path / "good.pptx").write_bytes(b"1234")
    (tmp_path / "small.html").write_bytes(b"x")
    (tmp_path / "large.zip").write_bytes(b"x" * 20)
    (tmp_path / "note.txt").write_text("ignored", encoding="utf-8")
    (tmp_path / ".hidden.pptx").write_bytes(b"1234")
    (tmp_path / "~$draft.pptx").write_bytes(b"1234")
    result = discover_paths_with_diagnostics(
        [tmp_path],
        rules=ScanRules(
            formats=frozenset({"pptx", "html", "html_zip"}),
            min_file_bytes=2,
            max_file_bytes=10,
        ),
    )

    assert [item.path.name for item in result.accepted] == ["good.pptx"]
    reasons = {item.path.name: item.reason for item in result.rejected}
    assert reasons == {
        ".hidden.pptx": "hidden",
        "large.zip": "above_maximum",
        "note.txt": "unsupported",
        "small.html": "below_minimum",
        "~$draft.pptx": "temporary",
    }


def test_diagnostic_discovery_respects_non_recursive_roots(tmp_path: Path) -> None:
    (tmp_path / "top.pptx").write_bytes(b"top")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "inside.pptx").write_bytes(b"inside")

    result = discover_paths_with_diagnostics(
        [tmp_path],
        rules=ScanRules(recursive=False),
    )

    assert [item.path.name for item in result.accepted] == ["top.pptx"]
