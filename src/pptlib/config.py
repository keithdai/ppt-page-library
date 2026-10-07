from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    home: Path
    database_path: Path
    assets_dir: Path
    temp_dir: Path
    log_dir: Path
    output_root: Path
    max_workers: int
    job_lease_seconds: int
    renderer: str
    thumbnail_long_edge: int
    preview_long_edge: int
    max_file_bytes: int
    max_uncompressed_package_bytes: int
    max_parts_per_package: int
    analyzer_version: str
    html_enabled: bool = False

    def __post_init__(self) -> None:
        checks = (
            ("max_workers", 1 <= self.max_workers <= 2),
            ("job_lease_seconds", self.job_lease_seconds > 0),
            ("renderer", self.renderer in {"auto", "officecli", "libreoffice"}),
            ("thumbnail_long_edge", self.thumbnail_long_edge > 0),
            ("preview_long_edge", self.preview_long_edge > 0),
            ("max_file_bytes", self.max_file_bytes > 0),
            ("max_uncompressed_package_bytes", self.max_uncompressed_package_bytes > 0),
            ("max_parts_per_package", self.max_parts_per_package > 0),
        )
        for name, valid in checks:
            if not valid:
                raise ValueError(f"invalid {name}: value is outside the allowed range")


def _default_home() -> Path:
    return Path.home() / "Library" / "Application Support" / "PPT Page Library"


def _read_defaults() -> dict[str, object]:
    with (Path(__file__).resolve().parent / "default.toml").open("rb") as handle:
        return tomllib.load(handle)


def _int_setting(values: Mapping[str, str], key: str, default: object, field: str) -> int:
    try:
        return int(values.get(key, str(default)))
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid {field}: value must be an integer") from error


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    values = os.environ if env is None else env
    defaults = _read_defaults()
    home = Path(values.get("PPTLIB_HOME", str(_default_home()))).expanduser().resolve()
    output_root = Path(
        values.get(
            "PPTLIB_OUTPUT_ROOT",
            str(Path.home() / "Documents" / "PPT Page Library Exports"),
        )
    ).expanduser().resolve()
    log_dir = Path(
        values.get(
            "PPTLIB_LOG_DIR",
            str(Path.home() / "Library" / "Logs" / "PPT Page Library"),
        )
    ).expanduser().resolve()
    temp_dir = Path(
        values.get(
            "PPTLIB_TEMP_DIR",
            str(Path.home() / "Library" / "Caches" / "PPT Page Library" / "tmp"),
        )
    ).expanduser().resolve()

    return Settings(
        home=home,
        database_path=home / "pages.db",
        assets_dir=home / "assets",
        temp_dir=temp_dir,
        log_dir=log_dir,
        output_root=output_root,
        max_workers=_int_setting(
            values, "PPTLIB_MAX_WORKERS", defaults["max_workers"], "max_workers"
        ),
        job_lease_seconds=_int_setting(
            values, "PPTLIB_JOB_LEASE_SECONDS", defaults["job_lease_seconds"], "job_lease_seconds"
        ),
        renderer=values.get("PPTLIB_RENDERER", str(defaults["renderer"])),
        thumbnail_long_edge=_int_setting(
            values,
            "PPTLIB_THUMBNAIL_LONG_EDGE",
            defaults["thumbnail_long_edge"],
            "thumbnail_long_edge",
        ),
        preview_long_edge=_int_setting(
            values, "PPTLIB_PREVIEW_LONG_EDGE", defaults["preview_long_edge"], "preview_long_edge"
        ),
        max_file_bytes=_int_setting(
            values, "PPTLIB_MAX_FILE_BYTES", defaults["max_file_bytes"], "max_file_bytes"
        ),
        max_uncompressed_package_bytes=_int_setting(
            values,
            "PPTLIB_MAX_UNCOMPRESSED_PACKAGE_BYTES",
            defaults["max_uncompressed_package_bytes"],
            "max_uncompressed_package_bytes",
        ),
        max_parts_per_package=_int_setting(
            values,
            "PPTLIB_MAX_PARTS_PER_PACKAGE",
            defaults["max_parts_per_package"],
            "max_parts_per_package",
        ),
        analyzer_version=str(defaults["analyzer_version"]),
        html_enabled=values.get("PPTLIB_ENABLE_HTML", "0") == "1",
    )
