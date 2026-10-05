from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path


def test_built_wheel_includes_all_migrations(tmp_path: Path) -> None:
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
        packaged = {
            name
            for name in archive.namelist()
            if name.startswith("pptlib/migrations/") and name.endswith(".sql")
        }
    assert packaged == {
        "pptlib/migrations/0001_foundation.sql",
        "pptlib/migrations/0002_ingestion.sql",
        "pptlib/migrations/0003_selection.sql",
        "pptlib/migrations/0003_taxonomy.sql",
        "pptlib/migrations/0004_hierarchical_taxonomy.sql",
        "pptlib/migrations/0005_slide_fingerprints.sql",
        "pptlib/migrations/0006_source_file_identity.sql",
        "pptlib/migrations/0007_deck_version_sha256_index.sql",
        "pptlib/migrations/0008_html_assets.sql",
        "pptlib/migrations/0009_scan_plans.sql",
        "pptlib/migrations/0010_scan_run_schedule.sql",
    }


def test_desktop_package_declares_bundled_python_runtime() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    package = json.loads((repository_root / "desktop" / "package.json").read_text("utf-8"))
    resources = package["build"]["extraResources"]

    assert {
        "from": "../build/runtime/pptlib",
        "to": "runtime/pptlib",
    } in resources
    assert "npm run runtime" in package["scripts"]["dist"]
    main_source = (repository_root / "desktop" / "main.js").read_text("utf-8")
    assert "process.resourcesPath" in main_source
    assert "'runtime', 'pptlib', 'pptlib'" in main_source
