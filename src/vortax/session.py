# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Instances: prepare the files for a VM, start it, control it, stop it.

An instance is a named VM. Its state lives in <data>/run/<name>/ so the CLI, the
dashboard and a second terminal all see the same truth without any daemon.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import catalog, disk, extract, fetch, monitor, paths, qemu, tunnels
from .errors import VortaxError
from . import settings as settings_mod
from .settings import Settings, parse_forward, parse_share, parse_usb

Log = Callable[[str], None]
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
VNC_BASE_PORT = 5900


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_password() -> str:
    """8 characters: the VNC protocol only uses the first 8 of any password."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


@dataclass
class Prepared:
    disk_path: Path
    disk_format: str
    iso_path: Path | None = None
    floppy_path: Path | None = None
    bios_path: Path | None = None
    notes: list[str] = field(default_factory=list)


class Instance:
    def __init__(self, name: str):
        self.name = paths.check_name(name)
        self.run = paths.run_dir(name)

    # -- paths ---------------------------------------------------------------
    @property
    def state_file(self) -> Path:
        return self.run / "state.json"

    @property
    def sock(self) -> Path:
        return self.run / "monitor.sock"

    @property
    def log_file(self) -> Path:
        return self.run / "qemu.log"

    @property
    def vnc_pass_file(self) -> Path:
        return self.run / "vnc.pass"

    @property
    def access_pass_file(self) -> Path:
        return self.run / "access.pass"

    def disk_candidates(self) -> list[Path]:
        return [paths.disks_dir() / f"{self.name}.qcow2", paths.disks_dir() / f"{self.name}.img"]

    def existing_disk(self) -> Path | None:
        return next((p for p in self.disk_candidates() if p.exists()), None)

    @property
    def iso(self) -> Path:
        return paths.media_dir() / f"{self.name}.iso"

    @property
    def floppy(self) -> Path:
        return paths.media_dir() / f"{self.name}.ima"

    @property
    def bios(self) -> Path:
        return paths.media_dir() / f"{self.name}.bios.bin"

    # -- state ------------------------------------------------------------------
    def state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_state(self, data: dict) -> None:
        paths.ensure_private_dir(self.run)
        tmp = self.state_file.with_suffix(".tmp")
        paths.write_private(tmp, json.dumps(data, indent=2))
        os.replace(tmp, self.state_file)

    def update_state(self, **changes) -> dict:
        data = self.state()
        data.update(changes)
        self.save_state(data)
        return data

    def pid(self) -> int:
        pid = int(self.state().get("pid", 0) or 0)
        if pid and not _is_qemu(pid):
            try:                      # reap it if it is our own exited child, so it does not linger as a zombie
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass
            return 0
        return pid

    def running(self) -> bool:
        return self.pid() > 0

    def status(self) -> dict:
        st = self.state()
        running = self.running()
        d = self.existing_disk()
        out = {
            "name": self.name,
            "running": running,
            "pid": self.pid(),
            "system": st.get("system", ""),
            "arch": st.get("arch", ""),
            "accel": st.get("accel", ""),
            "started": st.get("started", "") if running else "",
            "deadline": st.get("deadline", "") if running else "",
            "vnc": st.get("vnc", {}) if running else {},
            "tunnel": st.get("tunnel", {}) if running else {},
            "access": st.get("access", ""),
            "disk": str(d) if d else "",
            "disk_size": disk.size_bytes(d) if d else 0,
            "disk_format": st.get("disk_format", ""),
            "ram_mb": st.get("ram_mb", 0),
            "vcpus": st.get("vcpus", 0),
            "password_set": self.vnc_pass_file.exists(),
        }
        return out

    # -- passwords ------------------------------------------------------------------
    def vnc_password(self) -> str:
        try:
            return self.vnc_pass_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def set_vnc_password(self, password: str, log: Log = lambda m: None) -> str:
        if not password:
            raise VortaxError("password cannot be empty")
        if len(password) > 8:
            log("note: the VNC protocol only uses the first 8 characters of a password; truncating")
            password = password[:8]
        paths.ensure_private_dir(self.run)
        if self.running() and self.sock.exists():
            monitor.set_vnc_password(str(self.sock), password)
        paths.write_private(self.vnc_pass_file, password)
        return password

    # -- control ----------------------------------------------------------------------
    def hmp(self, command: str, timeout: float = 5.0) -> str:
        if not self.running():
            raise VortaxError(f"{self.name} is not running")
        return monitor.hmp(str(self.sock), command, timeout)

    def stop(self, graceful: bool = True, timeout: float = 30.0, log: Log = lambda m: None) -> bool:
        pid = self.pid()
        tunnels.stop(self.state().get("tunnel"))
        self.update_state(tunnel={})
        if not pid:
            return False
        if self.state().get("snapshot_on_stop") and self.sock.exists():
            d = self.existing_disk()
            if d and d.suffix == ".qcow2":
                try:
                    log("saving the running state so the next start resumes where you left off")
                    monitor.hmp(str(self.sock), f"savevm {AUTOSAVE}", 180.0)
                    monitor.hmp(str(self.sock), "quit", 3.0)
                    if _wait_exit(pid, 10):
                        self.update_state(pid=0)
                        self._notify("stopped", "state saved")
                        return True
                except VortaxError as exc:
                    log(f"could not save the state ({exc}); shutting down normally")
        if graceful and self.sock.exists():
            try:
                monitor.hmp(str(self.sock), "system_powerdown")
                log("sent ACPI power-down, waiting for the guest to shut down")
                if _wait_exit(pid, timeout):
                    self.update_state(pid=0)
                    return True
                log("guest did not shut down in time; quitting QEMU")
                monitor.hmp(str(self.sock), "quit", 3.0)
            except VortaxError:
                pass
            if _wait_exit(pid, 5):
                self.update_state(pid=0)
                return True
        self.kill()
        return True

    def _notify(self, what: str, detail: str = "") -> None:
        if self.state().get("notify"):
            from . import notify
            notify.send(f"vortax: {self.name} {what}", detail)

    def screenshot(self, out: Path) -> Path:
        """Capture the guest display to a PNG (QEMU writes a PPM, converted here)."""
        from . import png
        ppm = self.run / "screen.ppm"
        ppm.unlink(missing_ok=True)
        self.hmp(f"screendump {ppm}", timeout=15.0)
        for _ in range(20):
            if ppm.exists() and ppm.stat().st_size > 0:
                break
            time.sleep(0.1)
        else:
            raise VortaxError("QEMU did not write a screendump")
        try:
            out.write_bytes(png.ppm_to_png(ppm.read_bytes()))
        finally:
            ppm.unlink(missing_ok=True)
        return out

    def ssh_port(self) -> int:
        return int(self.state().get("ssh_port") or 0)

    def kill(self) -> bool:
        pid = self.pid()
        tunnels.stop(self.state().get("tunnel"))
        self.update_state(tunnel={})
        if not pid:
            return False
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _wait_exit(pid, 5)
        self.update_state(pid=0)
        return True

    def reboot(self) -> None:
        self.hmp("system_reset")

    # -- snapshots (qcow2 only) -----------------------------------------------------
    def snapshot(self, action: str, tag: str = "") -> str:
        if action in ("save", "load", "delete"):
            if not _TAG_RE.match(tag or ""):
                raise VortaxError("snapshot name: 1-40 letters, digits, '.', '_' or '-'")
        d = self.existing_disk()
        if not d or d.suffix != ".qcow2":
            raise VortaxError("snapshots need a qcow2 disk (raw .img disks cannot hold them)")
        if self.running():
            cmd = {"save": f"savevm {tag}", "load": f"loadvm {tag}", "delete": f"delvm {tag}", "list": "info snapshots"}[action]
            return self.hmp(cmd, timeout=120.0)
        if action == "list":
            return subprocess.run([disk.qemu_img(), "snapshot", "-l", str(d)], capture_output=True, text=True).stdout
        flag = {"save": "-c", "load": "-a", "delete": "-d"}[action]
        rc = subprocess.run([disk.qemu_img(), "snapshot", flag, tag, str(d)], capture_output=True, text=True)
        if rc.returncode != 0:
            raise VortaxError(rc.stderr.strip()[:300] or "qemu-img snapshot failed")
        return f"snapshot {action} ok: {tag}"


AUTOSAVE = "vortax-auto"


def _snapshot_tags(disk_path) -> set[str]:
    try:
        out = subprocess.run([disk.qemu_img(), "snapshot", "-l", str(disk_path)],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError, VortaxError):
        return set()
    return {parts[1] for parts in (line.split() for line in out.splitlines()) if len(parts) > 1}


def _free_port(start: int) -> int:
    import socket
    for port in range(start, start + 200):
        with socket.socket() as sk:
            try:
                sk.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise VortaxError("no free local port for the SSH forward")


def _is_qemu(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return b"qemu-system" in fh.read()
    except OSError:
        try:
            os.kill(pid, 0)          # no /proc (macOS): fall back to a liveness check
            return not Path("/proc").exists()
        except OSError:
            return False


def _wait_exit(pid: int, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _is_qemu(pid):
            return True
        try:                          # reap if it is our child, otherwise it stays a zombie
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        time.sleep(0.25)
    return not _is_qemu(pid)


def list_instances() -> list[str]:
    root = paths.data_dir() / "run"
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "state.json").exists())


# ---------------------------------------------------------------------------
# prepare: download -> extract -> place files
# ---------------------------------------------------------------------------


def _place(src: Path, dst: Path, consume: bool, link: bool = False) -> None:
    """Put `src` at `dst`: move it (our own download), symlink it (read-only media), or copy it."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if link and not consume:
        dst.symlink_to(src.resolve())
    elif consume:
        shutil.move(str(src), str(dst))
    else:
        shutil.copy2(src, dst)


def prepare(settings: Settings, resolved: catalog.Resolved, inst: Instance, force: bool = False,
            log: Log = print) -> Prepared:
    """Fetch and place everything the VM needs. Never overwrites an existing disk without `force`."""
    notes: list[str] = []
    profile = resolved.profile or {}
    paths.ensure_private_dir(inst.run)
    existing = inst.existing_disk()
    if existing and not force:
        log(f"disk already exists ({existing.name}); keeping it (use --rebuild to start over)")
        fmt = "raw" if existing.suffix == ".img" else "qcow2"
        return Prepared(existing, fmt, inst.iso if inst.iso.exists() else None,
                        inst.floppy if inst.floppy.exists() else None,
                        inst.bios if inst.bios.exists() else None, notes)

    if inst.running():
        raise VortaxError(f"{inst.name} is running; stop it before rebuilding")

    local = fetch.is_local(resolved.source)
    src = fetch.download(resolved.source, paths.cache_dir(), log=log)
    kind, sniffed = extract.classify_download(src)
    log(f"detected file type: {sniffed}")

    disk_path: Path
    disk_format = "qcow2"
    iso_path: Path | None = None

    def finish_disk(media_path: Path, fmt: str, consume: bool) -> tuple[Path, str]:
        for old in inst.disk_candidates():
            old.unlink(missing_ok=True)
        if fmt == "qcow2":
            dst = paths.disks_dir() / f"{inst.name}.qcow2"
            _place(media_path, dst, consume)
            return dst, "qcow2"
        if fmt == "raw":
            dst = paths.disks_dir() / f"{inst.name}.img"
            _place(media_path, dst, consume)
            return dst, "raw"
        log(f"converting {fmt} image to qcow2")
        dst = paths.disks_dir() / f"{inst.name}.qcow2"
        dst.parent.mkdir(parents=True, exist_ok=True)
        disk.convert_to_qcow2(media_path, dst)
        if consume:
            media_path.unlink(missing_ok=True)
        return dst, "qcow2"

    def make_blank_disk() -> tuple[Path, str]:
        for old in inst.disk_candidates():
            old.unlink(missing_ok=True)
        size = profile.get("disk") or f"{resolved.disk_gb}G"
        dst = paths.disks_dir() / f"{inst.name}.qcow2"
        dst.parent.mkdir(parents=True, exist_ok=True)
        log(f"creating a blank {size} qcow2 disk")
        disk.create(dst, size, "qcow2")
        return dst, "qcow2"

    if kind == "iso":
        _place(src, inst.iso, consume=not local, link=True)
        iso_path = inst.iso
        disk_path, disk_format = make_blank_disk()
    elif kind in ("qcow2", "disk"):
        fmt = disk.detect_format(src)
        log(f"disk image format: {fmt}")
        disk_path, disk_format = finish_disk(src, fmt, consume=not local)
    elif kind == "archive":
        work = Path(tempfile.mkdtemp(prefix=f"{inst.name}-", dir=_tmp_root()))
        try:
            log(f"extracting {src.name}")
            extract.extract(src, work, sniffed)
            media = extract.find_media(work)
            if media.kind == "iso":
                _place(media.path, inst.iso, consume=True)
                iso_path = inst.iso
                disk_path, disk_format = make_blank_disk()
            else:
                log(f"disk image format: {media.disk_format}")
                disk_path, disk_format = finish_disk(media.path, media.disk_format, consume=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if not local:
            src.unlink(missing_ok=True)
            src.with_name(src.name + ".done").unlink(missing_ok=True)
    else:
        raise VortaxError(f"{src.name}: not an ISO, disk image or supported archive (detected: {sniffed})")

    if kind in ("iso", "qcow2", "disk") and not local:
        src.with_name(src.name + ".done").unlink(missing_ok=True)

    prep = Prepared(disk_path, disk_format, iso_path, notes=notes)
    prep.floppy_path = _prepare_floppy(settings, inst, log, notes)
    prep.bios_path = _prepare_bios(settings, inst, log, notes)
    return prep


def _tmp_root() -> str:
    root = paths.data_dir() / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _prepare_floppy(settings: Settings, inst: Instance, log: Log, notes: list[str]) -> Path | None:
    if not settings.floppy_url:
        return None
    log("fetching floppy image")
    src = fetch.download(settings.floppy_url, paths.cache_dir(), log=log)
    _place(src, inst.floppy, consume=not fetch.is_local(settings.floppy_url))
    return inst.floppy


def _prepare_bios(settings: Settings, inst: Instance, log: Log, notes: list[str]) -> Path | None:
    if not settings.bios_url:
        return None
    if settings.firmware == "uefi":
        notes.append("--bios-url only applies in BIOS mode; ignored for this UEFI run")
        return None
    log("fetching custom BIOS")
    src = fetch.download(settings.bios_url, paths.cache_dir(), log=log)
    if extract.sniff(src) == "zip":
        with zipfile.ZipFile(src) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".bin")]
            if not names:
                raise VortaxError("no .bin file found inside the custom BIOS zip")
            with zf.open(names[0]) as member, open(inst.bios, "wb") as out:
                shutil.copyfileobj(member, out)
    else:
        _place(src, inst.bios, consume=not fetch.is_local(settings.bios_url))
    return inst.bios


# ---------------------------------------------------------------------------
# plan + start
# ---------------------------------------------------------------------------


_SECRET_KEYS = ("password", "access_password")


def save_settings(inst: Instance, s: Settings) -> None:
    """Remember how a VM was launched so `start` / the dashboard can bring it back without arguments."""
    data = {k: v for k, v in s.__dict__.items() if k not in _SECRET_KEYS}
    paths.write_private(inst.run / "settings.json", json.dumps(data, indent=2))


# What an existing VM may have changed after it was created. Identity (name, system, source, arch, boot mode), the
# disk (see `vortax disk`) and passwords are deliberately not here. The disk itself is never touched by an edit.
VM_EDITABLE = ("ram_mb", "vcpus", "accel", "nic", "audio", "mttcg", "boot", "ssh", "snapshot_on_stop", "notify",
               "runtime", "extra_qemu_args", "virtio_iso")


def edit_settings(inst: Instance, changes: dict) -> dict:
    """Change how a VM will be launched next time. Returns the editable values after the change."""
    bad = sorted(set(changes) - set(VM_EDITABLE))
    if bad:
        raise VortaxError(f"cannot edit {', '.join(bad)} on an existing VM (editable: {', '.join(VM_EDITABLE)})")
    saved = load_saved_settings(inst)
    if not saved:
        raise VortaxError(f"{inst.name} has no saved launch settings: start it once first")
    if changes.get("virtio_iso"):
        iso = Path(os.path.expanduser(str(changes["virtio_iso"]))).resolve()
        if not iso.is_file():
            raise VortaxError(f"driver ISO not found: {iso}")
        changes = {**changes, "virtio_iso": str(iso)}
    merged = settings_mod.resolve(environ={}, config={**saved, **changes})      # validates every value
    if changes:
        save_settings(inst, merged)
    return {k: getattr(merged, k) for k in VM_EDITABLE}


def load_saved_settings(inst: Instance) -> dict:
    try:
        return json.loads((inst.run / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _used_vnc_displays(skip: str) -> set[int]:
    used = set()
    for name in list_instances():
        if name == skip:
            continue
        inst = Instance(name)
        if inst.running():
            port = inst.state().get("vnc", {}).get("port")
            if port:
                used.add(int(port) - VNC_BASE_PORT)
    return used


def pick_display(requested: int, name: str) -> int:
    used = _used_vnc_displays(name)
    candidates = [requested] if requested >= 0 else range(0, 100)
    for n in candidates:
        if n not in used and not tunnels.port_open("127.0.0.1", VNC_BASE_PORT + n, 0.2):
            return n
    raise VortaxError(
        f"VNC display :{requested} (port {VNC_BASE_PORT + requested}) is busy" if requested >= 0
        else "no free VNC display found"
    )


def make_plan(settings: Settings, resolved: catalog.Resolved, inst: Instance, prepared: Prepared,
              display: int, vnc_password: bool, extra_forwards: list | tuple = ()) -> qemu.VMPlan:
    warnings = list(prepared.notes)
    profile = resolved.profile
    firmware = settings.firmware
    if profile and firmware == "uefi":
        warnings.append(f"profile {resolved.profile_name} predates UEFI: forcing BIOS mode")
        firmware = "bios"
    if resolved.arch == "aarch64":
        firmware = "uefi"

    plan = qemu.VMPlan(
        name=inst.name, arch=resolved.arch, accel=qemu.detect_accel(resolved.arch, settings.accel),
        vcpus=settings.vcpus, ram_mb=settings.ram_mb or qemu.default_ram_mb(), firmware=firmware,
        disk_path=str(prepared.disk_path), disk_format=prepared.disk_format,
        iso_path=str(prepared.iso_path) if (prepared.iso_path and settings.boot != "disk") else "",
        floppy_path=str(prepared.floppy_path) if prepared.floppy_path else "",
        bios_file=str(prepared.bios_path) if prepared.bios_path else "",
        nic=settings.nic if resolved.arch == "x86_64" else "virtio-net-pci",
        vnc_bind=settings.vnc_bind, vnc_display=display, vnc_password=vnc_password,
        monitor_sock=str(inst.sock), virtio_iso=settings.virtio_iso,
        forwards=[parse_forward(f) for f in settings.forward] + list(extra_forwards),
        shares=[parse_share(x) for x in settings.shares], usb=[parse_usb(x) for x in settings.usb],
        audio=settings.audio, mttcg=settings.mttcg,
        profile=profile, gpu_mode=settings.gpu_mode, extra_args=settings.extra_args(), warnings=warnings,
    )

    if profile and profile.get("boot") == "floppy" and not plan.floppy_path:
        warnings.append(f"{resolved.profile_name} normally boots from a floppy (--floppy-url); none given, "
                        "so it will boot from CD/disk and probably will not install")
    if plan.accel == "tcg" and resolved.arch == "x86_64":
        warnings.append("no KVM available: running under software emulation (TCG), which is much slower")

    if firmware == "uefi":
        fw = qemu.find_uefi(resolved.arch)
        if "code" in fw:
            plan.uefi_code = fw["code"]
            vars_copy = inst.run / "uefi_vars.fd"
            if not vars_copy.exists():
                paths.ensure_private_dir(inst.run)
                shutil.copy(fw["vars"], vars_copy)
            plan.uefi_vars = str(vars_copy)
        elif "single" in fw:
            plan.uefi_single = fw["single"]
    return plan


def _load_vnc_password(inst: Instance, settings: Settings, log: Log) -> str:
    """Return the password to apply ('' only when the user explicitly opted out)."""
    if settings.no_password:
        if settings.vnc_bind not in ("127.0.0.1", "localhost", "::1"):
            raise VortaxError("--no-password is only allowed while VNC listens on 127.0.0.1")
        inst.vnc_pass_file.unlink(missing_ok=True)
        return ""
    pw = settings.password or inst.vnc_password() or new_password()
    if len(pw) > 8:
        log("note: the VNC protocol only uses the first 8 characters of a password; truncating")
        pw = pw[:8]
    return pw


def start(settings: Settings, resolved: catalog.Resolved, inst: Instance, prepared: Prepared,
          log: Log = print, dry_run: bool = False) -> tuple[dict, list[str]]:
    """Start QEMU. Returns (state, argv). With dry_run nothing is created or launched."""
    if inst.running() and not dry_run:
        raise VortaxError(f"{inst.name} is already running (pid {inst.pid()})")
    if not dry_run:
        qemu.require_binary(resolved.arch)
    display = pick_display(settings.vnc_display, inst.name)
    password = "" if dry_run else _load_vnc_password(inst, settings, log)
    ssh_port = 0
    extra_fw: list = []
    if settings.ssh:
        ssh_port = _free_port(2222)
        extra_fw.append(("tcp", ssh_port, 22))
    plan = make_plan(settings, resolved, inst, prepared, display,
                     vnc_password=(settings.no_password is False) if dry_run else bool(password),
                     extra_forwards=extra_fw)
    if settings.snapshot_on_stop and prepared.disk_format == "qcow2" and not dry_run \
            and AUTOSAVE in _snapshot_tags(prepared.disk_path):
        plan.extra_args = list(plan.extra_args) + ["-loadvm", AUTOSAVE]
        log("resuming the saved session state")
    argv = qemu.build_command(plan)
    for w in plan.warnings:
        log(f"warning: {w}")
    if dry_run:
        return {}, argv

    paths.ensure_private_dir(inst.run)
    inst.sock.unlink(missing_ok=True)
    qemu_exe = qemu.require_binary(resolved.arch)
    argv[0] = qemu_exe
    logfh = open(inst.log_file, "ab")
    try:
        logfh.write(f"\n--- {_now()} start\n{qemu.command_line(argv)}\n".encode())
        logfh.flush()
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=logfh, stderr=subprocess.STDOUT,
                                start_new_session=True)
    finally:
        logfh.close()

    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise VortaxError(f"QEMU exited immediately (code {proc.returncode}):\n{tail(inst.log_file, 12)}")
        if inst.sock.exists():
            break
        time.sleep(0.2)
    else:
        proc.kill()
        raise VortaxError(f"QEMU did not create its monitor socket in 25s:\n{tail(inst.log_file, 12)}")

    if password:
        try:
            monitor.set_vnc_password(str(inst.sock), password)
        except VortaxError:
            proc.kill()
            raise
        paths.write_private(inst.vnc_pass_file, password)

    state = {
        "name": inst.name, "pid": proc.pid, "started": _now(),
        "system": resolved.system.id if resolved.system else "",
        "arch": resolved.arch, "accel": plan.accel, "disk_format": prepared.disk_format,
        "ram_mb": qemu.effective_resources(plan)[0], "vcpus": qemu.effective_resources(plan)[1],
        "profile": resolved.profile_name, "firmware": plan.firmware,
        "vnc": {"bind": plan.vnc_bind, "display": display, "port": VNC_BASE_PORT + display,
                "password": bool(password)},
        "access": settings.access, "tunnel": {}, "cmd": argv,
        "runtime": settings.runtime,
        "deadline": "",
        "ssh_port": ssh_port,
        "snapshot_on_stop": bool(settings.snapshot_on_stop),
        "notify": bool(settings.notify),
    }
    if settings.runtime:
        state["deadline"] = datetime.fromtimestamp(time.time() + settings.runtime * 60, timezone.utc) \
            .isoformat(timespec="seconds")
    inst.save_state(state)
    return state, argv


def start_tunnel(inst: Instance, kind: str, bore_server: str = "bore.pub") -> dict:
    st = inst.state()
    if not inst.running():
        raise VortaxError(f"{inst.name} is not running")
    tunnels.stop(st.get("tunnel"))
    port = int(st["vnc"]["port"])
    t = tunnels.start(kind, port, inst.run, hostname=f"vortax-{inst.name}", bore_server=bore_server)
    inst.update_state(tunnel=t.to_dict())
    return t.to_dict()


def tail(path: Path, lines: int = 50) -> str:
    try:
        data = path.read_bytes()[-64_000:].decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-lines:])


# ---------------------------------------------------------------------------
# export (replaces the Mega upload)
# ---------------------------------------------------------------------------


def export(inst: Instance, out: Path | None = None, rclone_dest: str = "", log: Log = print) -> Path:
    if inst.running():
        raise VortaxError(f"stop {inst.name} before exporting so the disk is consistent")
    d = inst.existing_disk()
    if not d:
        raise VortaxError(f"{inst.name} has no disk to export")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out or Path.cwd() / f"{inst.name}-{stamp}"
    if shutil.which("7z") or shutil.which("7zz"):
        out = out.with_suffix(".7z") if out.suffix != ".7z" else out
        exe = shutil.which("7z") or shutil.which("7zz")
        log(f"compressing to {out.name} with 7z")
        rc = subprocess.run([exe, "a", "-t7z", "-mx=5", "-y", str(out), str(d)], stdout=subprocess.DEVNULL).returncode
    elif shutil.which("zstd"):
        out = out.with_suffix(".zst") if out.suffix != ".zst" else out
        log(f"compressing to {out.name} with zstd")
        rc = subprocess.run(["zstd", "-T0", "-q", "-f", "-o", str(out), str(d)]).returncode
    else:
        import gzip
        out = out.with_suffix(".gz") if out.suffix != ".gz" else out
        log(f"compressing to {out.name} with gzip (install p7zip or zstd for better ratios)")
        with open(d, "rb") as src, gzip.open(out, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        rc = 0
    if rc != 0:
        raise VortaxError("compression failed")
    if rclone_dest:
        exe = shutil.which("rclone")
        if not exe:
            raise VortaxError("rclone not found; install it or copy the file yourself")
        log(f"uploading with rclone to {rclone_dest}")
        if subprocess.run([exe, "copy", str(out), rclone_dest]).returncode != 0:
            raise VortaxError("rclone upload failed")
    return out
