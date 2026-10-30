# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Disk image helpers built on qemu-img, with a pure-Python fallback for detection."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .errors import MissingToolError, VortaxError

_SIZE_RE = re.compile(r"^\d+(\.\d+)?[KMGTkmgt]?$")


def qemu_img() -> str:
    exe = shutil.which("qemu-img")
    if not exe:
        raise MissingToolError("qemu-img", "Debian: apt install qemu-utils; Termux: pkg search qemu")
    return exe


def info(path: Path) -> dict:
    out = subprocess.run([qemu_img(), "info", "--force-share", "--output=json", str(path)],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise VortaxError(f"qemu-img could not read {path.name}: {out.stderr.strip()[:300]}")
    try:
        return json.loads(out.stdout)
    except ValueError:
        raise VortaxError(f"qemu-img returned unreadable output for {path.name}") from None


def detect_format(path: Path) -> str:
    """The real on-disk format. File extensions lie (the original workflow renamed raw .img to .qcow2)."""
    try:
        with open(path, "rb") as fh:
            if fh.read(4) == b"QFI\xfb":
                return "qcow2"
    except OSError as exc:
        raise VortaxError(f"cannot read {path}: {exc}") from None
    if shutil.which("qemu-img"):
        return str(info(path).get("format", "raw"))
    return "raw"


def create(path: Path, size: str, fmt: str = "qcow2") -> None:
    if not _SIZE_RE.match(size):
        raise VortaxError(f"invalid disk size {size!r}")
    if fmt not in ("qcow2", "raw"):
        raise VortaxError(f"invalid disk format {fmt!r}")
    rc = subprocess.run([qemu_img(), "create", "-f", fmt, str(path), size], stdout=subprocess.DEVNULL)
    if rc.returncode != 0:
        raise VortaxError(f"qemu-img create failed for {path}")


def convert_to_qcow2(src: Path, dst: Path) -> None:
    rc = subprocess.run([qemu_img(), "convert", "-O", "qcow2", str(src), str(dst)])
    if rc.returncode != 0:
        raise VortaxError(f"qemu-img convert failed for {src.name}")


def size_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def resize(path: Path, size: str) -> None:
    """Grow (or with a leading '-' shrink) a disk image. `size` is e.g. +10G or 80G."""
    if not re.fullmatch(r"[+-]?\d+(\.\d+)?[KMGT]?", size):
        raise VortaxError("size looks like 80G or +10G")
    cmd = [qemu_img(), "resize"] + (["--shrink"] if size.startswith("-") else []) + [str(path), size]
    rc = subprocess.run(cmd, capture_output=True, text=True)
    if rc.returncode != 0:
        raise VortaxError(rc.stderr.strip()[:300] or "qemu-img resize failed")


def compact(path: Path) -> tuple[int, int]:
    """Rewrite a qcow2 with compression to reclaim space. Returns (size before, size after)."""
    before = size_bytes(path)
    tmp = path.with_name(path.name + ".compact")
    rc = subprocess.run([qemu_img(), "convert", "-O", "qcow2", "-c", str(path), str(tmp)],
                        capture_output=True, text=True)
    if rc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise VortaxError(rc.stderr.strip()[:300] or "qemu-img convert failed")
    os.replace(tmp, path)
    return before, size_bytes(path)
