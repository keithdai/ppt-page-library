import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from pptlib.config import load_settings


def test_load_settings_uses_local_paths_and_environment_override(tmp_path: Path) -> None:
    settings = load_settings(
        {
            "PPTLIB_HOME": str(tmp_path / "home"),
            "PPTLIB_MAX_WORKERS": "1",
        }
    )

    assert settings.home == (tmp_path / "home").resolve()
    assert settings.database_path == settings.home / "pages.db"
    assert settings.assets_dir == settings.home / "assets"
    assert settings.max_workers == 1


def test_load_settings_defaults_to_three_gib_upload_limit(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})

    assert settings.max_file_bytes == 3 * 1024 * 1024 * 1024


def test_renderer_defaults_to_libreoffice_and_accepts_known_backends(tmp_path: Path) -> None:
    default = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    assert default.renderer == "libreoffice"
    assert default.preview_long_edge == 1920
    for backend in ("officecli", "libreoffice", "auto"):
        settings = load_settings(
            {"PPTLIB_HOME": str(tmp_path / "home"), "PPTLIB_RENDERER": backend}
        )
        assert settings.renderer == backend


def test_invalid_renderer_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"^invalid renderer"):
        load_settings({"PPTLIB_HOME": str(tmp_path / "home"), "PPTLIB_RENDERER": "magic"})


def test_load_settings_works_from_built_wheel(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(project_root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    wheel_path = next(wheel_dir.glob("ppt_page_library-*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel:
        assert "pptlib/default.toml" in wheel.namelist()
        package_dir = tmp_path / "installed"
        wheel.extractall(package_dir)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pptlib.config import load_settings; "
                "print(load_settings({'PPTLIB_HOME': '.'}).renderer)"
            ),
        ],
        cwd=package_dir,
        env={**os.environ, "PYTHONPATH": str(package_dir)},
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "libreoffice"


def test_temp_dir_default_and_override(tmp_path: Path) -> None:
    settings = load_settings({"PPTLIB_HOME": str(tmp_path / "home")})
    assert settings.temp_dir == (
        Path.home() / "Library" / "Caches" / "PPT Page Library" / "tmp"
    ).resolve()
    overridden = load_settings(
        {"PPTLIB_HOME": str(tmp_path / "home"), "PPTLIB_TEMP_DIR": str(tmp_path / "tmp")}
    )
    assert overridden.temp_dir == (tmp_path / "tmp").resolve()


def test_invalid_numeric_settings_have_deterministic_errors(tmp_path: Path) -> None:
    cases = {
        "PPTLIB_MAX_WORKERS": "3",
        "PPTLIB_JOB_LEASE_SECONDS": "0",
        "PPTLIB_THUMBNAIL_LONG_EDGE": "0",
        "PPTLIB_PREVIEW_LONG_EDGE": "-1",
        "PPTLIB_MAX_FILE_BYTES": "0",
        "PPTLIB_MAX_UNCOMPRESSED_PACKAGE_BYTES": "0",
        "PPTLIB_MAX_PARTS_PER_PACKAGE": "0",
    }
    for key, value in cases.items():
        field = key.removeprefix("PPTLIB_").lower()
        with pytest.raises(ValueError, match=rf"^invalid {field}"):
            load_settings({"PPTLIB_HOME": str(tmp_path / "home"), key: value})
