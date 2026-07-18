from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    if not prefix or not prefix.isascii() or not prefix.replace("_", "").isalnum():
        raise ValueError("prefix must contain ASCII letters, numbers, or underscores")
    return f"{prefix}_{uuid.uuid4().hex}"
