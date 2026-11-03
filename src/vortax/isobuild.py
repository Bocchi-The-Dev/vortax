# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Turn a folder of files (for example sound or network drivers) into an ISO a guest can read as a CD."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .errors import MissingToolError, VortaxError

# Tool name -> arguments that put it in genisoimage-compatible mode.
TOOLS = (("xorriso", ["-as", "mkisofs"]), ("genisoimage", []), ("mkisofs", []))
INSTALL_HINT = "Termux: pkg install xorriso; Debian: apt install xorriso"


def find_tool() -> tuple[str, list[str]] | None:
    for name, prefix in TOOLS:
        path = shutil.which(name)
        if path:
            return path, list(prefix)
    return None


def volume_label(name: str) -> str:
    """ISO volume ids are upper-case letters, digits and underscore, 32 characters at most."""
    label = re.sub(r"[^A-Z0-9_]", "_", name.upper()).strip("_")[:32]
    return label or "DRIVERS"


def build(src: Path, out: Path, label: str = "") -> Path:
    """Write `out` from the files in `src`. Long names work in Windows XP (Joliet) and in Linux (Rock Ridge)."""
    if not src.is_dir():
        raise VortaxError(f"not a folder: {src}")
    if not any(p.is_file() for p in src.rglob("*")):
        raise VortaxError(f"{src} has no files to put on the ISO")
    if out == src or src in out.parents:
        raise VortaxError("the ISO cannot be written inside the folder it is built from")
    tool = find_tool()
    if tool is None:
        raise MissingToolError("xorriso (or genisoimage / mkisofs)", INSTALL_HINT)
    exe, prefix = tool
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    argv = [exe, *prefix, "-r", "-J", "-joliet-long", "-V", volume_label(label or src.name), "-o", str(tmp), str(src)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        tmp.unlink(missing_ok=True)
        raise VortaxError(f"could not run {os.path.basename(exe)}: {exc}") from None
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise VortaxError(f"{os.path.basename(exe)} failed: {' | '.join(tail) or 'no output'}")
    os.replace(tmp, out)                                   # a failed run never leaves a half-written ISO
    return out
