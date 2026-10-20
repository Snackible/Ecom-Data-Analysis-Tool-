"""Runtime reader for bot/settings.yaml. A null value means "not set yet"."""
from pathlib import Path

import yaml

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.yaml"


class NeedsSetting(Exception):
    """Raised when an answer needs a setting that is still null."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key

    def message(self) -> str:
        return f"needs setting: {self.key}"


def load(path=None) -> dict:
    p = Path(path) if path else SETTINGS_PATH
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get(settings: dict, dotted: str, required: bool = False):
    """get(s, 'waste_rule.spend_inr'). Returns None when unset (null, empty
    list or empty mapping). With required=True raises NeedsSetting."""
    cur = settings
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            cur = None
            break
        cur = cur[part]
    if cur in (None, "", [], {}):
        if required:
            raise NeedsSetting(dotted)
        return None
    return cur
