# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Build the QEMU command line.

`build_command` is a pure function of a `VMPlan`: no I/O, no environment, no
globals. That is what makes it testable without QEMU installed, and it is the one
extension point for new hardware: add arguments here, never in a copy of a whole
command block.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
from dataclasses import dataclass, field
from typing import Any

from .errors import MissingToolError, VortaxError

BINARIES = {"x86_64": "qemu-system-x86_64", "aarch64": "qemu-system-aarch64"}


@dataclass
class VMPlan:
    name: str
    arch: str = "x86_64"
    accel: str = "tcg"                 # already resolved: kvm | tcg
    vcpus: int = 2
    ram_mb: int | None = 2048          # None = do not pass -m
    firmware: str = "bios"             # bios | uefi
    uefi_code: str = ""
    uefi_vars: str = ""
    uefi_single: str = ""              # a combined firmware file, passed via -bios
    bios_file: str = ""                # custom SeaBIOS (BIOS mode only)
    disk_path: str = ""
    disk_format: str = "qcow2"
    iso_path: str = ""
    floppy_path: str = ""
    nic: str = "e1000"
    vnc_bind: str = "127.0.0.1"
    vnc_display: int = 0
    vnc_password: bool = True
    monitor_sock: str = ""
    virtio_iso: str = ""
    forwards: list[tuple[str, int, int]] = field(default_factory=list)
    shares: list[tuple[str, str]] = field(default_factory=list)     # (host path, 9p mount tag)
    usb: list[tuple[int, int]] = field(default_factory=list)        # (vendor id, product id)
    audio: str = "none"                                              # none | hda | ac97 | sb16 | es1370
    mttcg: bool = False                # software emulation only: run vCPUs on several host threads (experimental)
    profile: dict[str, Any] | None = None
    gpu_mode: str = "virtio"
    extra_args: list[str] = field(default_factory=list)
    serial_file: str = ""              # used by the self-test to capture serial output
    warnings: list[str] = field(default_factory=list)


def binary_for(arch: str) -> str:
    try:
        return BINARIES[arch]
    except KeyError:
        raise VortaxError(f"unsupported architecture {arch!r} (supported: {', '.join(BINARIES)})") from None


def detect_accel(arch: str, requested: str = "auto") -> str:
    """kvm only when the guest matches the host and /dev/kvm is usable."""
    host = platform.machine().lower().replace("arm64", "aarch64").replace("amd64", "x86_64")
    kvm_ok = host == arch and os.access("/dev/kvm", os.R_OK | os.W_OK)
    if requested == "kvm":
        if not kvm_ok:
            raise VortaxError(
                f"--accel kvm needs a {arch} host with a usable /dev/kvm "
                f"(host is {host}; is your user in the 'kvm' group?)"
            )
        return "kvm"
    if requested == "tcg":
        return "tcg"
    return "kvm" if kvm_ok else "tcg"


_UEFI_CANDIDATES = {
    "x86_64": [
        ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
        ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
        ("/usr/share/edk2/ovmf/OVMF_CODE.fd", "/usr/share/edk2/ovmf/OVMF_VARS.fd"),
        ("/usr/share/edk2/x64/OVMF_CODE.4m.fd", "/usr/share/edk2/x64/OVMF_VARS.4m.fd"),
    ],
    "aarch64": [
        ("/usr/share/AAVMF/AAVMF_CODE.fd", "/usr/share/AAVMF/AAVMF_VARS.fd"),
        ("/usr/share/edk2/aarch64/QEMU_EFI-pflash.raw", "/usr/share/edk2/aarch64/vars-template-pflash.raw"),
    ],
}
_UEFI_SINGLE = {
    "x86_64": ["/usr/share/qemu/OVMF.fd", "{PREFIX}/share/qemu/edk2-x86_64-code.fd"],
    "aarch64": ["/usr/share/qemu-efi-aarch64/QEMU_EFI.fd", "{PREFIX}/share/qemu/edk2-aarch64-code.fd"],
}


def find_uefi(arch: str) -> dict[str, str]:
    """Locate system-installed UEFI firmware. Returns {code, vars} or {single}; {} if none."""
    prefix = os.environ.get("PREFIX", "/usr")
    for code, vars_ in _UEFI_CANDIDATES.get(arch, []):
        if os.path.exists(code) and os.path.exists(vars_):
            return {"code": code, "vars": vars_}
    for pattern in _UEFI_SINGLE.get(arch, []):
        path = pattern.replace("{PREFIX}", prefix)
        if os.path.exists(path):
            return {"single": path}
    return {}


def host_ram_mb() -> int:
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 4096


def default_ram_mb() -> int:
    from . import envinfo
    cap = 2048 if envinfo.is_termux() else 8192       # phones: leave room for Android itself
    return max(1024, min(cap, host_ram_mb() // 2))


# ---------------------------------------------------------------------------


def _netdev(plan: VMPlan) -> str:
    forwards = list(plan.forwards)
    for fw in (plan.profile or {}).get("hostfwd", []):
        forwards.append((fw.get("proto", "tcp"), int(fw["host"]), int(fw["guest"])))
    spec = "user,id=net0"
    seen = set()
    for proto, host, guest in forwards:
        if (proto, host) in seen:
            continue
        seen.add((proto, host))
        spec += f",hostfwd={proto}:127.0.0.1:{host}-:{guest}"
    return spec


def _smp(count: int) -> str:
    """-smp value. One socket with several cores, never several sockets: editions limited by physical CPUs
    (XP Home sees one socket, XP Pro two) then use every vCPU."""
    return f"{count},sockets=1,cores={count},threads=1" if count > 1 else "1"


def _tcg_accel(plan: VMPlan, tb_size_mb: int | None = None) -> str:
    opts = ["tcg"]
    if tb_size_mb:
        opts.append(f"tb-size={int(tb_size_mb)}")
    if plan.mttcg:
        opts.append("thread=multi")
    return ",".join(opts)


def _legacy_vcpus(plan: VMPlan, prof: dict, use_kvm: bool) -> int:
    """Without KVM every vCPU shares one host thread unless multi-threaded TCG is on, so a preset can cap the count."""
    return plan.vcpus if (use_kvm or plan.mttcg) else (prof.get("vcpus_tcg") or plan.vcpus)


def _common(plan: VMPlan, vcpus: int | None = None) -> list[str]:
    vnc = f"{plan.vnc_bind}:{plan.vnc_display}"
    if plan.vnc_password:
        vnc += ",password=on"
    args = ["-name", plan.name, "-smp", _smp(vcpus or plan.vcpus), "-display", "none"]
    # Always defined, ahead of -vnc, so the VNC server can stream sound to the dashboard console and a sound
    # card can be plugged into the running VM later (`vortax live --audio ac97`).
    args += ["-audiodev", "none,id=snd0"]
    vnc += ",audiodev=snd0"
    args += ["-vnc", vnc]
    if plan.monitor_sock:
        args += ["-monitor", f"unix:{plan.monitor_sock},server=on,wait=off"]
    if plan.serial_file:
        args += ["-serial", f"file:{plan.serial_file}"]
    return args


def _disk_args(plan: VMPlan, interface: str | None) -> list[str]:
    if not plan.disk_path:
        return []
    drive = f"file={plan.disk_path},format={plan.disk_format}"
    if interface == "virtio":
        return ["-drive", f"if=none,id=hd0,{drive}", "-device", "virtio-blk-pci,drive=hd0,bootindex=1"]
    return ["-drive", f"{drive},index=0,media=disk"]


def _cpu_for(cpu: str | None, accel: str) -> str | None:
    if not cpu:
        return None
    return "max" if cpu == "host" and accel != "kvm" else cpu


def _x86_modern(plan: VMPlan) -> list[str]:
    args = _common(plan)
    args += ["-accel", _tcg_accel(plan) if plan.accel == "tcg" else plan.accel,
             "-cpu", "host" if plan.accel == "kvm" else "max"]
    if plan.ram_mb:
        args += ["-m", str(plan.ram_mb)]
    if plan.bios_file and plan.firmware == "bios":
        args += ["-bios", plan.bios_file]
    args += ["-netdev", _netdev(plan), "-device", f"{plan.nic},netdev=net0"]
    args += ["-device", "usb-ehci,id=ehci", "-device", "usb-tablet,bus=ehci.0"]
    return args + _driver_cd(plan)


def _driver_cd(plan: VMPlan) -> list[str]:
    """An extra CD with drivers (virtio-win, or files you put on an ISO). It takes the next free slot on the second
    IDE bus, so it can sit next to the installer CD."""
    if not plan.virtio_iso:
        return []
    return ["-drive", f"file={plan.virtio_iso},media=cdrom,readonly=on,if=none,id=cd1",
            "-device", "ide-cd,bus=ide.1,drive=cd1"]


def _x86_legacy(plan: VMPlan) -> list[str]:
    prof = plan.profile or {}
    use_kvm = plan.accel == "kvm" and prof.get("kvm", True)
    args = _common(plan, _legacy_vcpus(plan, prof, use_kvm))
    args += ["-accel", "kvm" if use_kvm else _tcg_accel(plan, prof.get("tb_size_mb"))]
    cpu = _cpu_for(prof.get("cpu"), "kvm" if use_kvm else "tcg")
    if cpu:
        args += ["-cpu", cpu]
    ram = prof["ram_mb"] if "ram_mb" in prof else plan.ram_mb
    if ram:
        args += ["-m", str(ram)]
    if prof.get("machine"):
        args += ["-M", prof["machine"]]
    if plan.bios_file:
        args += ["-bios", plan.bios_file]
    vga = prof.get("vga", [])
    modes = prof.get("vga_modes")
    if modes:
        vga = modes.get(plan.gpu_mode, modes.get("virtio", vga))
    args += list(vga)
    if prof.get("rtc") == "localtime":
        args += ["-rtc", "base=localtime"]
    nic = prof.get("nic", plan.nic)
    if nic in (None, "none"):
        args += ["-net", "none"]
    else:
        args += ["-netdev", _netdev(plan), "-device", f"{nic},netdev=net0"]
    args += list(prof.get("extra", []))
    if plan.floppy_path:
        args += ["-fda", plan.floppy_path]
    return args + _driver_cd(plan)


def _aarch64(plan: VMPlan) -> list[str]:
    args = _common(plan)
    args += ["-M", "virt", "-accel", plan.accel, "-cpu", "host" if plan.accel == "kvm" else "max"]
    args += ["-m", str(plan.ram_mb or 2048)]
    args += ["-device", "virtio-gpu-pci", "-device", "qemu-xhci", "-device", "usb-kbd", "-device", "usb-tablet"]
    args += ["-netdev", _netdev(plan), "-device", f"{plan.nic},netdev=net0"]
    return args


def _extra_devices(plan: VMPlan) -> list[str]:
    """Shared folders, USB passthrough and a sound device: the optional hardware every profile can use."""
    args: list[str] = []
    for i, (path, tag) in enumerate(plan.shares):
        args += ["-virtfs", f"local,path={path},mount_tag={tag},security_model=none,id=share{i}"]
    for vendor, product in plan.usb:
        dev = f"usb-host,vendorid=0x{vendor:04x},productid=0x{product:04x}"
        if plan.arch == "x86_64" and plan.profile is None:
            dev += ",bus=ehci.0"                       # the controller added for the tablet
        args += ["-device", dev]
    if plan.audio != "none":
        dev = {"hda": ["intel-hda", "hda-duplex"], "ac97": ["AC97"], "sb16": ["sb16"], "es1370": ["ES1370"]}[plan.audio]
        for d in dev:
            args += ["-device", d if d == "intel-hda" else f"{d},audiodev=snd0"]
    return args


def _firmware_args(plan: VMPlan) -> list[str]:
    if plan.firmware != "uefi":
        return []
    if plan.uefi_code and plan.uefi_vars:
        return ["-drive", f"if=pflash,format=raw,readonly=on,file={plan.uefi_code}",
                "-drive", f"if=pflash,format=raw,file={plan.uefi_vars}"]
    if plan.uefi_single:
        return ["-bios", plan.uefi_single]
    raise VortaxError(
        f"UEFI firmware for {plan.arch} was not found. Install it (Debian: apt install ovmf"
        + (" qemu-efi-aarch64" if plan.arch == "aarch64" else "") + ") or use --firmware bios."
    )


def effective_resources(plan: VMPlan) -> tuple[int, int]:
    """(RAM in MB, vCPUs) that QEMU is really given. A hardware preset can override both, and the status
    line should show what runs, not what was requested. RAM 0 means QEMU's own default."""
    prof = plan.profile
    if prof is None or plan.arch != "x86_64":
        return plan.ram_mb or 0, plan.vcpus
    use_kvm = plan.accel == "kvm" and prof.get("kvm", True)
    ram = prof["ram_mb"] if "ram_mb" in prof else plan.ram_mb
    return ram or 0, _legacy_vcpus(plan, prof, use_kvm)


def build_command(plan: VMPlan) -> list[str]:
    """Return the full argv, binary first."""
    legacy = plan.profile is not None and plan.arch == "x86_64"
    if plan.arch == "x86_64":
        args = _x86_legacy(plan) if legacy else _x86_modern(plan)
    elif plan.arch == "aarch64":
        args = _aarch64(plan)
        if plan.firmware != "uefi":
            raise VortaxError("aarch64 guests need UEFI firmware: use --firmware uefi")
    else:
        raise VortaxError(f"unsupported architecture {plan.arch!r}")

    args += _extra_devices(plan)
    args += _firmware_args(plan)

    prof = plan.profile or {}
    floppy_boot = legacy and prof.get("boot") == "floppy" and plan.floppy_path
    if plan.arch == "aarch64":
        if plan.iso_path:
            args += ["-drive", f"if=none,id=cd0,file={plan.iso_path},media=cdrom,readonly=on",
                     "-device", "usb-storage,drive=cd0,bootindex=0"]
    elif floppy_boot:
        args += ["-boot", "a"]
        if plan.iso_path:
            args += ["-cdrom", plan.iso_path]
    elif plan.iso_path:
        args += ["-boot", "d", "-cdrom", plan.iso_path]
    else:
        args += ["-boot", "c"]

    # UEFI + installer boots the target through virtio-blk, as the original workflow did.
    virtio_disk = (plan.arch == "aarch64") or (plan.firmware == "uefi" and bool(plan.iso_path) and not legacy)
    args += _disk_args(plan, "virtio" if virtio_disk else None)
    args += list(plan.extra_args)
    return [binary_for(plan.arch), *args]


def command_line(argv: list[str]) -> str:
    return shlex.join(argv)


def require_binary(arch: str) -> str:
    name = binary_for(arch)
    path = shutil.which(name)
    if not path:
        raise MissingToolError(name, "Debian: apt install qemu-system-x86 qemu-utils; Termux: pkg search qemu")
    return path
