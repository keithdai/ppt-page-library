from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "job_id", "worker_id", "kind", "error_code"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info is not None:
            exception_type = record.exc_info[0]
            if exception_type is not None:
                payload["exception_type"] = exception_type.__name__
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_dir: Path) -> logging.Logger:
    """Configure the application JSONL log sink once and return its logger."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = (log_dir / "app.jsonl").resolve()
    logger = logging.getLogger("pptlib")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler):
            if Path(handler.baseFilename).resolve() == log_path:
                return logger
            logger.removeHandler(handler)
            handler.close()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    return logger
