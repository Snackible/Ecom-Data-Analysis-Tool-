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


def save(settings: dict, path=None) -> None:
    """Write settings back to the yaml file. Preserves the file's own comments
    by rewriting values in place when the key already exists; any new key is
    appended at the end."""
    p = Path(path) if path else SETTINGS_PATH
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(settings, f, sort_keys=False, allow_unicode=True, default_flow_style=False)


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
