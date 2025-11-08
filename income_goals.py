#!/usr/bin/env python3
"""Utilities for reading/updating income goals shared between bot and scheduler."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional

DEFAULT_GOALS_PATH = "data/income_goals.json"


def get_income_goals_path() -> Path:
    return Path(os.getenv("MYRACE_GOALS_PATH", DEFAULT_GOALS_PATH)).expanduser()


def _load_raw_goals(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(data, dict):
        return {str(key): str(value) for key, value in data.items()}
    return {}


def load_income_goals(path: Optional[Path] = None) -> Dict[str, Decimal]:
    target_path = path or get_income_goals_path()
    raw = _load_raw_goals(target_path)
    goals: Dict[str, Decimal] = {}
    for race_id, value in raw.items():
        try:
            goals[str(race_id)] = Decimal(str(value))
        except Exception:
            continue
    return goals


def upsert_income_goal(
    race_id: str,
    amount: Optional[Decimal],
    path: Optional[Path] = None,
) -> Dict[str, Decimal]:
    target_path = path or get_income_goals_path()
    raw = _load_raw_goals(target_path)
    if amount is None:
        raw.pop(race_id, None)
    else:
        raw[race_id] = str(amount)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("w", encoding="utf-8") as handle:
        json.dump(raw, handle, ensure_ascii=False, indent=2, sort_keys=True)
    return load_income_goals(target_path)
