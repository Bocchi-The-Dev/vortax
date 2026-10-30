"""`vortax doctor`: what is installed, what is missing, and what that means."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass

from . import paths, qemu


@dataclass
class Check:
    name: str
    ok: bool
    required: bool
    detail: str = ""
    hint: str = ""

    def to_dict(self):
        return self.__dict__.copy()


def _tool(name: str, required: bool, hint: str, alts: tuple[str, ...] = ()) -> Check:
    for n in (name, *alts):
        path = shutil.which(n)
        if path:
            return Check(name, True, required, path)
    return Check(name, False, required, "not found", hint)


def run_checks() -> list[Check]:
    checks: list[Check] = []
    py_ok = sys.version_info >= (3, 9)
    checks.append(Check("python >= 3.9", py_ok, True, platform.python_version(), "install a newer python3"))
    checks.append(_tool("qemu-system-x86_64", True, "apt install qemu-system-x86 | Termux: pkg search qemu"))
    checks.append(_tool("qemu-img", True, "apt install qemu-utils | Termux: pkg search qemu"))
    checks.append(_tool("qemu-system-aarch64", False, "apt install qemu-system-arm (only for aarch64 guests)"))

    arch = platform.machine().lower()
    kvm = os.path.exists("/dev/kvm")
    kvm_ok = os.access("/dev/kvm", os.R_OK | os.W_OK)
    detail = "usable" if kvm_ok else ("present but not accessible (add your user to the 'kvm' group)" if kvm
                                      else "absent: guests run under TCG (slow but works)")
    checks.append(Check("KVM acceleration", kvm_ok, False, detail))

    checks.append(_tool("aria2c", False, "apt install aria2 (faster parallel downloads)"))
    checks.append(Check("downloader (curl/wget/aria2c)", any(shutil.which(t) for t in ("aria2c", "curl", "wget")),
                        False, "python urllib is the fallback", "apt install curl"))
    checks.append(_tool("7z", False, "apt install p7zip-full | pkg install p7zip (needed for .7z sources)", ("7zz", "7za")))
    checks.append(_tool("unrar", False, "apt install unrar-free (only for .rar sources)", ("unrar-free",)))
    checks.append(_tool("zstd", False, "apt install zstd (optional: .zst sources, faster exports)"))

    uefi = qemu.find_uefi("x86_64")
    checks.append(Check("UEFI firmware (x86_64)", bool(uefi), False,
                        uefi.get("code") or uefi.get("single") or "not found", "apt install ovmf"))

    for tool, hint in (("bore", "cargo install bore-cli"), ("ngrok", "https://ngrok.com/download"),
                       ("tailscale", "https://tailscale.com/download"),
                       ("websockify", "apt install websockify novnc (browser VNC in the dashboard)"),
                       ("rclone", "apt install rclone (optional export upload)")):
        checks.append(_tool(tool, False, hint))

    data = paths.data_dir()
    probe = data if data.exists() else data.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    checks.append(Check("data directory writable", os.access(probe, os.W_OK), True, str(data)))
    try:
        free = shutil.disk_usage(probe).free / 1024 ** 3
        checks.append(Check("free disk space", free >= 5, False, f"{free:.1f} GB free", "VM disks and ISOs are large"))
    except OSError:
        pass
    checks.append(Check("host architecture", arch in ("x86_64", "amd64", "aarch64", "arm64"), False, arch))
    return checks


def format_report(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.required else "warn")
        line = f"[{mark}] {c.name}: {c.detail}"
        if not c.ok and c.hint:
            line += f"\n         -> {c.hint}"
        lines.append(line)
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.required and not c.ok for c in checks) else 0
