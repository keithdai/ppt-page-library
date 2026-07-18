import json
from pathlib import Path

from pptlib.logging import configure_logging


def test_configure_logging_creates_jsonl_without_duplicate_handlers(tmp_path: Path) -> None:
    logger = configure_logging(tmp_path)
    first_count = len(logger.handlers)
    logger = configure_logging(tmp_path)
    assert len(logger.handlers) == first_count == 1

    logger.info("hello", extra={"request_id": "req_test"})
    for handler in logger.handlers:
        handler.flush()
    lines = (tmp_path / "app.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert '"message": "hello"' in lines[0]
    assert '"request_id": "req_test"' in lines[0]


def test_jsonl_includes_exception_type_and_traceback(tmp_path: Path) -> None:
    logger = configure_logging(tmp_path)
    try:
        raise ValueError("secret detail")
    except ValueError:
        logger.exception("boom")
    for handler in logger.handlers:
        handler.flush()
    payload = json.loads((tmp_path / "app.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["exception_type"] == "ValueError"
    assert "ValueError: secret detail" in payload["traceback"]
