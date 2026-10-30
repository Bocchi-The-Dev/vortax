# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""`vortax selftest`: prove the install works, without needing an OS image.

Level 1 (always): build a QEMU command for every catalog system and profile and check
it is well-formed.
Level 2 (--boot): create a disk containing a 512-byte boot sector that prints a marker
to the serial port, run it through the *real* pipeline (prepare -> plan -> start ->
monitor -> stop) under whatever acceleration is available, and read the marker back.
This is the check CI runs after installing the .deb.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from . import catalog, paths, qemu, session, settings
from .errors import VortaxError

MARKER = b"VORTAX-OK"

# 16-bit real mode: print "VORTAX-OK\r\n" on COM1 (0x3F8) then halt.
_CODE = bytes.fromhex(
    "fa31c08ed8be167cbaf803"   # cli; xor ax,ax; mov ds,ax; mov si,0x7c16; mov dx,0x3f8
    "ac08c07403eeebf8"         # lodsb; or al,al; jz done; out dx,al; jmp loop
    "f4ebfd"                   # done: hlt; jmp done
)


def boot_sector() -> bytes:
    sector = _CODE + MARKER + b"\r\n\x00"
    assert len(_CODE) == 22, "message offset must match the mov si immediate"
    return sector.ljust(510, b"\x00") + b"\x55\xaa"


def check_plans() -> list[str]:
    """Return a list of problems (empty = all good)."""
    problems: list[str] = []
    systems = catalog.load_systems()
    profiles = catalog.load_profiles()
    for sid, s in systems.items():
        if s.profile and s.profile not in profiles:
            problems.append(f"system {sid}: unknown profile {s.profile}")
        for mode, url in s.sources.items():
            if url and not url.startswith(("http://", "https://", "latest:")):
                problems.append(f"system {sid}: {mode} source is not an http(s) URL or latest: source")
    for name, prof in profiles.items():
        for fw in ("bios",):
            plan = qemu.VMPlan(name="t", arch="x86_64", accel="tcg", firmware=fw, disk_path="/d.qcow2",
                               floppy_path="/f.ima" if prof.get("boot") == "floppy" else "",
                               monitor_sock="/m.sock", profile=prof)
            for mode in (prof.get("vga_modes") or {"virtio": None}):
                plan.gpu_mode = mode
                argv = qemu.build_command(plan)
                if any((not isinstance(a, str)) or a == "" for a in argv):
                    problems.append(f"profile {name}: empty/invalid argument in {argv}")
                if argv.count("-accel") != 1:
                    problems.append(f"profile {name}: -accel appears {argv.count('-accel')} times")
                if argv.count("-boot") != 1:
                    problems.append(f"profile {name}: -boot appears {argv.count('-boot')} times")
    for arch in ("x86_64", "aarch64"):
        for fw in ("bios", "uefi"):
            if arch == "aarch64" and fw == "bios":
                continue
            plan = qemu.VMPlan(name="t", arch=arch, accel="tcg", firmware=fw, disk_path="/d.qcow2",
                               iso_path="/i.iso", monitor_sock="/m.sock", uefi_single="/fw.fd")
            try:
                qemu.build_command(plan)
            except VortaxError as exc:
                problems.append(f"{arch}/{fw}: {exc}")
    return problems


def boot_test(timeout: float = 60.0, log=print) -> None:
    """End-to-end: raw disk -> prepare -> start -> serial marker -> stop."""
    name = "selftest"
    root = Path(tempfile.mkdtemp(prefix="vortax-selftest-"))
    old_home = os.environ.get("VORTAX_HOME")
    os.environ["VORTAX_HOME"] = str(root)
    try:
        src = root / "boot.img"
        src.write_bytes(boot_sector() + b"\x00" * 512 * 63)       # 32 KiB raw disk
        serial = root / "serial.log"

        s = settings.resolve({"name": name, "source": str(src), "ram_mb": 64, "vcpus": 1,
                              "no_password": True, "accel": os.environ.get("VORTAX_ACCEL") or "auto",
                              "extra_qemu_args": f"-serial file:{serial}"},
                             environ={}, config={})
        # The test hands QEMU an extra serial port so it can read what the guest prints.
        resolved = catalog.resolve(s)
        inst = session.Instance(name)
        log("preparing a raw test disk")
        prepared = session.prepare(s, resolved, inst, force=True, log=lambda m: None)
        if prepared.disk_format != "raw":
            raise VortaxError(f"expected the boot sector to be detected as raw, got {prepared.disk_format}")
        log("starting QEMU")
        state, argv = session.start(s, resolved, inst, prepared, log=lambda m: None)
        log(f"qemu running (pid {state['pid']}, accel {state['accel']})")
        try:
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if serial.exists() and MARKER in serial.read_bytes():
                    break
                if not inst.running():
                    raise VortaxError("QEMU exited before the guest printed the marker:\n" + session.tail(inst.log_file))
                time.sleep(0.5)
            else:
                raise VortaxError(f"guest did not print {MARKER.decode()} within {timeout:.0f}s")
            log("guest booted and printed the marker")
            out = inst.hmp("info status")
            if "running" not in out and "paused" not in out:
                raise VortaxError(f"unexpected monitor reply: {out!r}")
            log(f"monitor reply: {out.strip()}")
        finally:
            inst.stop(graceful=False)
        if inst.running():
            raise VortaxError("VM is still running after stop")
        log("stopped cleanly")
    finally:
        if old_home is None:
            os.environ.pop("VORTAX_HOME", None)
        else:
            os.environ["VORTAX_HOME"] = old_home
        shutil.rmtree(root, ignore_errors=True)
