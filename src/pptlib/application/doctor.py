from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import pypdfium2 as pdfium  # type: ignore[import-untyped]

from pptlib.config import Settings


@dataclass(frozen=True, slots=True)
class Check:
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    sqlite: Check
    fts5: Check
    libreoffice: Check
    pdftoppm: Check
    data_directory: Check
    output_directory: Check

    @property
    def ready_for_text_search(self) -> bool:
        return self.sqlite.ok and self.fts5.ok and self.data_directory.ok

    @property
    def ready_for_rendering(self) -> bool:
        return self.ready_for_text_search and self.libreoffice.ok and self.pdftoppm.ok

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["ready_for_text_search"] = self.ready_for_text_search
        value["ready_for_rendering"] = self.ready_for_rendering
        return value


def _directory_check(path: Path) -> Check:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".pptlib-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return Check(True, str(path))
    except OSError as error:
        return Check(False, str(error))


def run_doctor(
    settings: Settings,
    executable_finder: Callable[[str], str | None] = shutil.which,
) -> DoctorReport:
    try:
        connection = sqlite3.connect(":memory:")
    except sqlite3.Error as error:
        sqlite_check = Check(False, str(error))
        fts_check = Check(False, "SQLite unavailable")
    else:
        sqlite_check = Check(True, sqlite3.sqlite_version)
        try:
            connection.execute("CREATE VIRTUAL TABLE fts_probe USING fts5(body)")
            fts_check = Check(True, "FTS5 available")
        except sqlite3.OperationalError as error:
            fts_check = Check(False, str(error))
        finally:
            connection.close()

    soffice = executable_finder("soffice")
    # A caller-provided finder is authoritative (and makes the check
    # deterministic in tests); probe the macOS app bundle only for the
    # default PATH-based finder.
    if soffice is None and executable_finder is shutil.which:
        app_binary = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        soffice = str(app_binary) if app_binary.exists() else None

    return DoctorReport(
        sqlite=sqlite_check,
        fts5=fts_check,
        libreoffice=Check(soffice is not None, soffice or "LibreOffice not found"),
        pdftoppm=Check(True, f"Bundled PDFium {pdfium.V_PYPDFIUM2}"),
        data_directory=_directory_check(settings.home),
        output_directory=_directory_check(settings.output_root),
    )
