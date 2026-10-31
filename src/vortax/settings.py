# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Run settings.

Precedence, highest first: command line > VORTAX_* environment > config.json > defaults.
The environment layer is what lets a CI workflow (or a shell alias) drive the tool
without ever pasting values into shell source, e.g.:

    VORTAX_SYSTEM=ubuntu VORTAX_BOOT_MODE=iso VORTAX_TUNNEL=bore vortax run
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from . import paths
from .errors import VortaxError

BOOT_MODES = ("auto", "iso", "qcow2", "img")
FIRMWARE = ("bios", "uefi")
TUNNELS = ("none", "bore", "ngrok", "tailscale")
ACCESS = ("vnc", "rdp", "nomachine", "rustdesk", "anydesk")
ARCHES = ("x86_64", "aarch64")
ACCEL = ("auto", "kvm", "tcg")
GPU_MODES = ("virtio", "bochs", "std")
BOOT_ORDER = ("auto", "disk", "cdrom")
AUDIO = ("none", "hda", "ac97", "sb16")
NICS = ("e1000", "e1000e", "virtio-net-pci", "rtl8139", "pcnet", "ne2k_pci")

_SECRET_FIELDS = ("password", "access_password")


@dataclass
class Settings:
    name: str = "vortax"            # VM name: names the disk, state dir and process
    system: str = ""                # catalog id (see `vortax list`)
    boot_mode: str = "auto"         # auto | iso | qcow2 | img
    source: str = ""                # URL or local path; wins over the catalog
    runtime: int = 0                # minutes before `vortax run` stops the VM; 0 = until Ctrl-C
    tunnel: str = "none"            # none | bore | ngrok | tailscale
    access: str = "vnc"             # vnc | rdp | nomachine | rustdesk | anydesk
    firmware: str = "bios"          # bios | uefi
    boot: str = "auto"              # auto | disk | cdrom (disk = ignore the installer ISO once installed)
    arch: str = ""                  # x86_64 | aarch64 (default: the system's, else x86_64)
    accel: str = "auto"             # auto | kvm | tcg
    profile: str = ""               # hardware preset, see `vortax profiles`
    gpu_mode: str = "virtio"        # batocera profile only: virtio | bochs | std
    floppy_url: str = ""            # URL or path to a .ima floppy (retro profiles)
    bios_url: str = ""              # custom SeaBIOS build (BIOS mode only)
    nic: str = "e1000"
    ram_mb: int = 0                 # 0 = half of host RAM, between 1 and 8 GiB
    vcpus: int = 2
    disk_gb: int = 0                # 0 = the catalog's size
    vnc_display: int = -1           # -1 = first free display (port 5900 + n)
    vnc_bind: str = "127.0.0.1"
    password: str = ""              # VNC password; empty = generate one
    no_password: bool = False       # allow a passwordless VNC (loopback only)
    access_password: str = ""       # RustDesk / AnyDesk unattended password
    extra_qemu_args: str = ""       # appended verbatim (shell-style quoting)
    forward: list = field(default_factory=list)   # ["tcp:2222:22", ...] host->guest
    virtio_iso: str = ""            # path to virtio-win.iso to attach
    shares: list = field(default_factory=list)    # ["/host/dir[:tag]", ...] shared via 9p (Linux guests)
    usb: list = field(default_factory=list)       # ["vendor:product", ...] host USB devices to pass through
    audio: str = "none"             # none | hda | ac97 | sb16: sound device for guests that need one
    ssh: bool = False               # forward a free host port to guest port 22 (see `vortax ssh`)
    snapshot_on_stop: bool = False  # savevm "auto-<time>" before a graceful stop (qcow2 only)
    notify: bool = False            # notify (Termux / desktop / VORTAX_NOTIFY_URL) when long jobs finish

    def validate(self) -> "Settings":
        paths.check_name(self.name)
        _one_of("boot_mode", self.boot_mode, BOOT_MODES)
        _one_of("firmware", self.firmware, FIRMWARE)
        _one_of("tunnel", self.tunnel, TUNNELS)
        _one_of("access", self.access, ACCESS)
        _one_of("accel", self.accel, ACCEL)
        _one_of("gpu_mode", self.gpu_mode, GPU_MODES)
        _one_of("boot", self.boot, BOOT_ORDER)
        _one_of("audio", self.audio, AUDIO)
        for dev in self.usb:
            parse_usb(dev)
        for share in self.shares:
            parse_share(share)
        if self.arch:
            _one_of("arch", self.arch, ARCHES)
        if self.runtime < 0:
            raise VortaxError("runtime must be 0 or more minutes")
        if self.vcpus < 1 or self.vcpus > 256:
            raise VortaxError("vcpus must be between 1 and 256")
        if self.ram_mb < 0 or self.disk_gb < 0:
            raise VortaxError("ram_mb and disk_gb cannot be negative")
        if self.vnc_display < -1 or self.vnc_display > 99:
            raise VortaxError("vnc_display must be -1 (auto) or 0-99")
        if not self.nic or any(c in self.nic for c in " ,="):
            raise VortaxError(f"invalid nic model {self.nic!r}")
        for spec in self.forward:
            parse_forward(spec)
        try:
            shlex.split(self.extra_qemu_args)
        except ValueError as exc:
            raise VortaxError(f"extra_qemu_args: {exc}") from None
        return self

    def extra_args(self) -> list[str]:
        return shlex.split(self.extra_qemu_args) if self.extra_qemu_args else []

    def redacted(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        for key in _SECRET_FIELDS:
            if d.get(key):
                d[key] = "***"
        return d

    def __repr__(self) -> str:  # never leak passwords into logs or tracebacks
        return f"Settings({self.redacted()})"


def _one_of(label: str, value: str, allowed: tuple) -> None:
    if value not in allowed:
        raise VortaxError(f"{label} must be one of {', '.join(allowed)} (got {value!r})")


def parse_forward(spec: str) -> tuple[str, int, int]:
    """'tcp:2222:22' -> ('tcp', 2222, 22)"""
    parts = str(spec).split(":")
    if len(parts) == 2:
        parts = ["tcp", *parts]
    try:
        proto, host, guest = parts[0], int(parts[1]), int(parts[2])
    except (IndexError, ValueError):
        raise VortaxError(f"invalid forward {spec!r}; use [tcp|udp:]HOST:GUEST, e.g. tcp:2222:22") from None
    if proto not in ("tcp", "udp") or not (0 < host < 65536) or not (0 < guest < 65536):
        raise VortaxError(f"invalid forward {spec!r}; use [tcp|udp:]HOST:GUEST, e.g. tcp:2222:22")
    return proto, host, guest


def parse_usb(spec: str) -> tuple[int, int]:
    """'046d:c52b' -> (0x046d, 0xc52b)"""
    try:
        vendor, product = str(spec).split(":")
        v, p = int(vendor, 16), int(product, 16)
    except ValueError:
        raise VortaxError(f"invalid USB device {spec!r}; use VENDOR:PRODUCT in hex, e.g. 046d:c52b (see lsusb)") from None
    if not (0 <= v <= 0xFFFF and 0 <= p <= 0xFFFF):
        raise VortaxError(f"invalid USB device {spec!r}")
    return v, p


def parse_share(spec: str) -> tuple[str, str]:
    """'/home/me/files:files' -> ('/home/me/files', 'files'); the tag defaults to the folder name."""
    text = str(spec)
    path, sep, tag = text.rpartition(":")
    if not sep or "/" in tag or not path:          # no ':tag' suffix: the whole string is the path
        path, tag = text, ""
    path = os.path.expanduser(path)
    if "," in path:
        raise VortaxError(f"share path may not contain a comma: {path!r}")
    if not tag:
        tag = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(path.rstrip("/"))) or "share"
    elif not re.fullmatch(r"[A-Za-z0-9._-]+", tag):
        raise VortaxError(f"invalid share tag {tag!r}; use letters, digits, '-', '_' or '.'")
    return path, tag


def _coerce(name: str, current: Any, raw: Any) -> Any:
    if isinstance(current, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise VortaxError(f"{name}: expected a number, got {raw!r}") from None
    if isinstance(current, list):
        if isinstance(raw, (list, tuple)):
            return list(raw)
        return [p for p in str(raw).replace(",", " ").split() if p]
    return "" if raw is None else str(raw)


def load_config_file() -> dict[str, Any]:
    path = paths.config_dir() / "config.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VortaxError(f"cannot read {path}: {exc}") from None
    if not isinstance(data, dict):
        raise VortaxError(f"{path} must contain a JSON object")
    return data


EDITABLE = ("ram_mb", "vcpus", "disk_gb", "tunnel", "accel", "firmware", "nic", "audio", "access", "runtime",
            "ssh", "snapshot_on_stop", "notify")


def save_config_file(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge `updates` into config.json after validating the result. None/'' resets a key to its default."""
    bad = [k for k in updates if k not in EDITABLE]
    if bad:
        raise VortaxError(f"these settings cannot be edited here: {', '.join(bad)}")
    config = load_config_file()
    for k, v in updates.items():
        if v is None or v == "":
            config.pop(k, None)
        else:
            config[k] = v
    resolve(environ={}, config=config)                    # raises VortaxError if anything is invalid
    path = paths.config_dir() / "config.json"
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    paths.write_private(path, json.dumps(config, indent=2))
    return config


def resolve(cli: dict[str, Any] | None = None, environ: dict[str, str] | None = None,
            config: dict[str, Any] | None = None) -> Settings:
    """Merge defaults < config file < environment < command line."""
    environ = os.environ if environ is None else environ
    config = load_config_file() if config is None else config
    s = Settings()
    known = {f.name for f in dataclasses.fields(Settings)}
    for key in config:
        if key not in known and not key.startswith("_"):
            raise VortaxError(f"config.json: unknown setting {key!r}")
    for f in dataclasses.fields(Settings):
        current = getattr(s, f.name)
        value = current
        if f.name in config:
            value = _coerce(f.name, current, config[f.name])
        env_key = "VORTAX_" + f.name.upper()
        if env_key in environ and environ[env_key] != "":
            value = _coerce(f.name, current, environ[env_key])
        if cli and cli.get(f.name) is not None:
            value = _coerce(f.name, current, cli[f.name])
        setattr(s, f.name, value)
    return s.validate()


# --- secrets (tunnel tokens) -------------------------------------------------

_SECRET_ENV = {
    "tailscale_authkey": ("TAILSCALE_AUTHKEY", "TS_AUTHKEY"),
    "ngrok_authtoken": ("NGROK_AUTHTOKEN", "NGROK_AUTH"),
}


def secrets_path():
    return paths.config_dir() / "secrets.json"


def get_secret(name: str) -> str:
    """Environment first, then ~/.config/vortax/secrets.json (kept 0600)."""
    for env in _SECRET_ENV.get(name, ()):
        if os.environ.get(env):
            return os.environ[env]
    try:
        return str(json.loads(secrets_path().read_text(encoding="utf-8")).get(name, ""))
    except (OSError, ValueError):
        return ""


def set_secret(name: str, value: str) -> None:
    if name not in _SECRET_ENV:
        raise VortaxError(f"unknown secret {name!r}; choose from {', '.join(_SECRET_ENV)}")
    try:
        data = json.loads(secrets_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[name] = value
    paths.write_private(secrets_path(), json.dumps(data, indent=2))
