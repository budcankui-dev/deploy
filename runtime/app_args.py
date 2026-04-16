from __future__ import annotations

import json
import os
from typing import Any


def parse_json_override(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def env_json(name: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = default or {}
    return parse_json_override(os.getenv(name)) or fallback


def merge_dicts(base: dict[str, Any], override: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(base)
    if override:
        merged.update({k: v for k, v in override.items() if v is not None})
    return merged

