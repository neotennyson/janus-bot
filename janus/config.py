from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand(value):
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


class Config:
    def __init__(self, data: dict):
        self._d = data or {}

    def get(self, path: str, default=None):
        cur = self._d
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def __getitem__(self, path: str):
        return self.get(path)

    @property
    def raw(self) -> dict:
        return self._d


def load_config(path: str = "config.yaml") -> Config:
    p = Path(path)
    data = yaml.safe_load(p.read_text()) if p.exists() else {}
    return Config(_expand(data or {}))
