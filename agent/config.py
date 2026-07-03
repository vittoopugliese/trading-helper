"""Load and expose application configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

_config: dict[str, Any] | None = None


def get_config(config_path: Path | None = None) -> dict[str, Any]:
    """Return cached config dict, loading from YAML on first call."""
    global _config
    if _config is None:
        path = config_path or DEFAULT_CONFIG_PATH
        with open(path, encoding="utf-8") as f:
            _config = yaml.safe_load(f)
    return _config


def resolve_path(relative: str) -> Path:
    """Resolve a config path relative to project root."""
    return PROJECT_ROOT / relative


def reset_config() -> None:
    """Clear cached config (useful for tests)."""
    global _config
    _config = None
