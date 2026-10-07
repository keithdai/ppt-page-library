from collections.abc import Callable
from pathlib import Path

import pytest

from pptlib.application import render_assets
from pptlib.config import load_settings


class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows
        self.closed = False

    def execute(self, _query: str) -> _Rows:
        return _Rows(self._rows)

    def close(self) -> None:
        self.closed = True


def test_backfill_reports_skipped_repaired_and_page_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
        }
    )
    settings.home.mkdir(parents=True)
    settings.database_path.touch()
    complete = tmp_path / "complete.pptx"
    missing = tmp_path / "missing.pptx"
    complete.touch()
    missing.touch()
    connection = _Connection(
        [
            (str(complete), "ver_complete", 2, "pptx", "a" * 64),
            (str(missing), "ver_missing", 3, "pptx", "b" * 64),
        ]
    )
    rendered: list[str] = []

    monkeypatch.setattr(render_assets, "connect", lambda _path: connection)
    monkeypatch.setattr(
        render_assets,
        "render_cache_is_complete",
        lambda _assets, version_id, _slides: version_id == "ver_complete",
    )

    def fake_render(
        _source_path: Path,
        version_id: str,
        slide_count: int,
        *,
        on_page: Callable[[int, int], None],
        **_kwargs: object,
    ) -> int:
        rendered.append(version_id)
        for page in range(1, slide_count + 1):
            on_page(page, slide_count)
        return slide_count

    monkeypatch.setattr(render_assets, "render_deck_thumbnails", fake_render)
    events: list[dict[str, object]] = []

    report = render_assets.backfill_thumbnails(settings, on_progress=events.append)

    assert connection.closed is True
    assert rendered == ["ver_missing"]
    assert report.to_dict() == {
        "ok": True,
        "total": 2,
        "repaired": 1,
        "skipped": 1,
        "pages": 3,
        "failed": [],
    }
    assert [event["stage"] for event in events].count("render") == 3
    assert events[-1]["action"] == "repaired"
