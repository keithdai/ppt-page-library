from pathlib import Path

from pptlib.discovery.scanner import scan_source_root


def test_scan_source_root_hashes_pptx_and_skips_lock_files(tmp_path: Path) -> None:
    (tmp_path / "deck.pptx").write_bytes(b"pptx")
    (tmp_path / "~$deck.pptx").write_bytes(b"lock")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "other.pptx").write_bytes(b"other")
    result = scan_source_root(tmp_path)
    assert [item.path.name for item in result] == ["deck.pptx", "other.pptx"]
    assert result[0].size_bytes == 4
    assert len(result[0].sha256) == 64
