"""
config.py

Minimal config.yaml loader (PRD section 5). Missing file, missing
keys, or malformed YAML all fall back to safe defaults rather than
crashing the daemon — config.yaml is a convenience, not a requirement.
"""

import os
from typing import Any, Dict

import yaml

DEFAULTS: Dict[str, Any] = {
    "device_path": None,
    "ghost_overlay": {"enabled": True},
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    config = {
        "device_path": DEFAULTS["device_path"],
        "ghost_overlay": dict(DEFAULTS["ghost_overlay"]),
    }

    try:
        with open(path, "r") as f:
            raw = yaml.safe_load(f) or {}
    except (FileNotFoundError, yaml.YAMLError):
        return config

    if not isinstance(raw, dict):
        return config

    if "device_path" in raw:
        config["device_path"] = raw["device_path"]

    overlay_raw = raw.get("ghost_overlay")
    if isinstance(overlay_raw, dict) and "enabled" in overlay_raw:
        config["ghost_overlay"]["enabled"] = bool(overlay_raw["enabled"])

    return config