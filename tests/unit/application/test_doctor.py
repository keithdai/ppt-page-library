import sqlite3
from pathlib import Path

from pptlib.application.doctor import run_doctor
from pptlib.config import load_settings


def test_doctor_reports_sqlite_fts5_and_missing_libreoffice(tmp_path: Path) -> None:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
            "PATH": "",
        }
    )

    report = run_doctor(settings, executable_finder=lambda _: None)

    assert report.sqlite.ok is True
    assert report.fts5.ok is True
    assert report.libreoffice.ok is False
    assert report.pdftoppm.ok is True
    assert report.ready_for_text_search is True
    assert report.ready_for_rendering is False


def test_doctor_reports_sqlite_unavailable(monkeypatch, tmp_path: Path) -> None:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
        }
    )

    def fail_connect(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database")

    monkeypatch.setattr("pptlib.application.doctor.sqlite3.connect", fail_connect)

    report = run_doctor(settings, executable_finder=lambda _: None)

    assert report.sqlite.ok is False
    assert report.sqlite.detail == "unable to open database"
    assert report.fts5.ok is False
    assert report.ready_for_text_search is False
    assert report.ready_for_rendering is False
