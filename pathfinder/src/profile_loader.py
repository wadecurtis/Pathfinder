"""Load application settings from config.yaml."""

import os
import yaml


def load_settings(path: str = None) -> dict:
    """Load settings from config.yaml at the repo root."""
    if path is None:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        path = os.path.join(repo_root, "config.yaml")
    path = os.path.abspath(path)

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # Translate config.yaml format (has 'search' key) to the internal
    # structure that scout.py expects (has 'scout' and 'discovery' keys).
    search = raw["search"]
    return {
        "scout": search,
        "discovery": {
            "default_sources": search.get("sources", ["linkedin"]),
            "default_locations": search.get("locations", ["canada"]),
        },
        "llm":    raw.get("llm", {}),
        "output": raw.get("output", {}),
    }
