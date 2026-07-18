from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_dockerfile_is_self_contained_and_runs_as_non_root() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.11-slim-bookworm" in dockerfile
    assert "libreoffice-impress" in dockerfile
    assert "fonts-noto-cjk" in dockerfile
    assert "PPTLIB_HOST=0.0.0.0" in dockerfile
    assert "PPTLIB_HOME=/data" in dockerfile
    assert "PPTLIB_OUTPUT_ROOT=/exports" in dockerfile
    assert "PPTLIB_TEMP_DIR=/tmp/pptlib" in dockerfile
    assert "PPTLIB_LOG_DIR=/logs" in dockerfile
    assert "useradd --create-home --uid 10001" in dockerfile
    assert "USER pptlib" in dockerfile
    assert "EXPOSE 8765" in dockerfile
    assert "api/v1/health" in dockerfile
    assert 'CMD ["pptlib", "serve", "--no-open"]' in dockerfile


def test_compose_defines_web_worker_healthcheck_and_persistent_mounts() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "services:" in compose
    assert "  web:" in compose
    assert "  worker:" in compose
    assert "dockerfile: Dockerfile" in compose
    assert "image: ppt-page-library:local" in compose
    assert '"127.0.0.1:8765:8765"' in compose
    assert "PPTLIB_HOME: /data" in compose
    assert "PPTLIB_OUTPUT_ROOT: /exports" in compose
    assert "PPTLIB_TEMP_DIR: /tmp/pptlib" in compose
    assert "PPTLIB_LOG_DIR: /logs" in compose
    assert "./sources:/sources:ro" in compose
    assert "pptlib-data:/data" in compose
    assert "pptlib-exports:/exports" in compose
    assert "pptlib-logs:/logs" in compose
    assert "api/v1/health" in compose
    assert "condition: service_healthy" in compose
    assert "    healthcheck:\n      disable: true" in compose
    assert "while true; do" in compose
    assert "pptlib worker --once" in compose


def test_dockerignore_excludes_local_build_and_test_artifacts() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    for ignored in (".venv", ".pytest_cache", "build", "dist", "tests"):
        assert ignored in dockerignore
    assert "!README.md" in dockerignore
    assert "!pyproject.toml" in dockerignore
