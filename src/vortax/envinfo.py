# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Facts about the machine we are running on that change defaults (mainly: Termux on Android)."""

from __future__ import annotations

import os
import shutil


def is_termux() -> bool:
    return "TERMUX_VERSION" in os.environ or "com.termux" in os.environ.get("PREFIX", "")


def termux_packages() -> dict[str, str]:
    """Tool -> Termux package, for `vortax doctor` hints. Package names can change; `pkg search` is the fallback."""
    return {
        "qemu-system-x86_64": "qemu-system-x86-64-headless",
        "qemu-system-aarch64": "qemu-system-aarch64-headless",
        "qemu-img": "qemu-utils",
        "aria2c": "aria2",
        "curl": "curl",
        "7z": "p7zip",
        "zstd": "zstd",
        "unrar": "unrar",
        "rclone": "rclone",
        "ssh": "openssh",
        "websockify": "python-websockify",
        "termux-notification": "termux-api",
    }


def termux_install_command(missing: list[str]) -> str:
    table = termux_packages()
    pkgs = sorted({table[m] for m in missing if m in table})
    return ("pkg install -y " + " ".join(pkgs)) if pkgs else ""


def wake_lock(enable: bool = True) -> bool:
    """On Termux, ask Android not to kill us while a VM runs. Returns True if the command was available."""
    exe = shutil.which("termux-wake-lock" if enable else "termux-wake-unlock")
    if not exe:
        return False
    import subprocess
    try:
        subprocess.run([exe], timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return True
