from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


def test_built_wheel_includes_foundation_migration(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "-w",
            str(wheel_dir),
        ],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel_path = next(wheel_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel_path) as archive:
        assert "pptlib/migrations/0001_foundation.sql" in archive.namelist()
