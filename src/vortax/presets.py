# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Named run presets: a saved bundle of settings, applied with `--preset NAME` or `vortax run NAME`."""

from __future__ import annotations

import json

from . import paths, settings
from .errors import VortaxError

_SECRETS = {"password", "access_password", "tailscale_authkey", "ngrok_authtoken"}


def _file():
    return paths.config_dir() / "presets.json"


def load() -> dict[str, dict]:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get(name: str) -> dict:
    data = load()
    if name not in data:
        raise VortaxError(f"no preset named {name!r}; see `vortax preset list`")
    return data[name]


def save(name: str, values: dict) -> dict:
    paths.check_name(name)
    clean = {k: v for k, v in values.items()
             if v not in (None, "", [], False) and k in settings.Settings.__dataclass_fields__
             and k not in _SECRETS and k != "name"}
    if not clean:
        raise VortaxError("nothing to save: pass the options you want in the preset")
    data = load()
    data[name] = clean
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    paths.write_private(_file(), json.dumps(data, indent=2))
    return clean


def remove(name: str) -> None:
    data = load()
    if name not in data:
        raise VortaxError(f"no preset named {name!r}")
    del data[name]
    paths.write_private(_file(), json.dumps(data, indent=2))
