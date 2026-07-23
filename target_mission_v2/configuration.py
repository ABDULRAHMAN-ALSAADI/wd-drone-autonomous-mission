#!/usr/bin/env python3
"""Load mission JSON profiles with small, explicit overrides."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return a recursive merge without mutating either input."""
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path | str, _seen: set[Path] | None = None) -> dict[str, Any]:
    """Load one profile and resolve an optional relative ``extends`` path."""
    profile_path = Path(path).expanduser().resolve()
    seen = set() if _seen is None else set(_seen)
    if profile_path in seen:
        chain = " -> ".join(str(item) for item in (*seen, profile_path))
        raise ValueError(f"Configuration inheritance cycle: {chain}")
    seen.add(profile_path)

    data = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Configuration root must be an object: {profile_path}")
    parent = data.pop("extends", None)
    if parent is None:
        return data
    parent_path = Path(str(parent))
    if not parent_path.is_absolute():
        parent_path = profile_path.parent / parent_path
    return deep_merge(load_config(parent_path, seen), data)
