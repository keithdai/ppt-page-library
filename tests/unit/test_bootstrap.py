from pathlib import Path

import pytest

from pptlib.bootstrap import initialize
from pptlib.config import load_settings


def test_initialize_surfaces_unusable_temp_dir(tmp_path: Path, monkeypatch) -> None:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_TEMP_DIR": str(tmp_path / "tmp"),
            "PPTLIB_LOG_DIR": str(tmp_path / "logs"),
            "PPTLIB_OUTPUT_ROOT": str(tmp_path / "exports"),
        }
    )
    original_mkdir = Path.mkdir

    def deny_temp(path: Path, *args, **kwargs) -> None:
        if path == settings.temp_dir:
            raise PermissionError("temporary directory is not writable")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_temp)
    with pytest.raises(PermissionError, match="not writable"):
        initialize(settings)
