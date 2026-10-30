# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Where Vortax keeps things.

Everything lives under one data directory so a whole install can be moved,
backed up or deleted as a unit:

    <data>/cache/            downloaded sources (resumable)
    <data>/disks/            VM disks   (<name>.qcow2 or <name>.img)
    <data>/media/            installer ISOs and floppies (<name>.iso, <name>.ima)
    <data>/run/<name>/       per-VM state: state.json, pid, monitor socket, logs
    <config>/                config.json, secrets.json (0600), systems.json overrides
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def data_dir() -> Path:
    env = os.environ.get("VORTAX_HOME")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "vortax"


def config_dir() -> Path:
    env = os.environ.get("VORTAX_CONFIG")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "vortax"


def cache_dir() -> Path:
    return data_dir() / "cache"


def disks_dir() -> Path:
    return data_dir() / "disks"


def media_dir() -> Path:
    return data_dir() / "media"


def run_dir(name: str) -> Path:
    return data_dir() / "run" / check_name(name)


def check_name(name: str) -> str:
    """VM names end up in file names and process arguments, so keep them boring."""
    from .errors import VortaxError

    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise VortaxError(
            f"invalid VM name {name!r}: use 1-64 letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return name


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def write_private(path: Path, text: str) -> None:
    """Write a file that only the owner can read, never briefly world-readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o600)
