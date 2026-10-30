"""Optional host-side remote-access programs.

Plain VNC needs nothing here: QEMU serves it directly. This module is for the case where
you would rather reach the *host's* desktop (RDP, NoMachine, RustDesk, AnyDesk) and open
the VM's VNC from there, as the original workflow did.

Nothing is installed unless you run `vortax access install <program>`, every step is
shown first (`--dry-run`), and firewall rules are never changed for you: opening ports
on a public machine is your call.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import fetch
from .errors import VortaxError

DESKTOP_PACKAGES = ["xfce4", "xfce4-goodies", "xorg", "dbus-x11", "tigervnc-viewer", "wmctrl"]


@dataclass
class Program:
    key: str
    label: str
    ports: list[str]
    apt: list[str] = field(default_factory=list)
    deb_url: str = ""
    deb_sha256: str = ""        # fill in to enforce a checksum; empty requires --allow-unverified
    needs_desktop: bool = True
    note: str = ""


PROGRAMS = {
    "vnc": Program("vnc", "VNC (built into QEMU)", ["tcp/5900+"], needs_desktop=False,
                   note="Nothing to install. Connect any VNC client to the address from `vortax info`."),
    "rdp": Program("rdp", "XRDP", ["tcp/3389"], apt=["xrdp"], note="Log in with an existing account on the host."),
    "nomachine": Program("nomachine", "NoMachine", ["tcp/4000", "tcp/4011-4999"],
                         deb_url="https://web9001.nomachine.com/download/9.2/Linux/nomachine_9.2.18_3_amd64.deb"),
    "rustdesk": Program("rustdesk", "RustDesk", ["tcp/21115-21119", "udp/21116"],
                        deb_url="https://github.com/rustdesk/rustdesk/releases/download/1.4.9/rustdesk-1.4.9-x86_64.deb",
                        note="Connects out through RustDesk's relay; no inbound port is needed."),
    "anydesk": Program("anydesk", "AnyDesk", ["tcp/6568"],
                       deb_url="https://download.anydesk.com/linux/anydesk_8.0.4-1_amd64.deb",
                       note="Connects out through AnyDesk's relay; no inbound port is needed."),
}


@dataclass
class Step:
    title: str
    argv: list[str] = field(default_factory=list)
    root: bool = True
    download: str = ""         # url to fetch first (the deb path is appended to argv via {deb})


def install_plan(key: str) -> list[Step]:
    prog = PROGRAMS.get(key)
    if not prog:
        raise VortaxError(f"unknown program {key!r}; choose from {', '.join(PROGRAMS)}")
    steps: list[Step] = []
    if prog.needs_desktop:
        steps += [Step("refresh package lists", ["apt-get", "update", "-qq"]),
                  Step("install a desktop (XFCE) and a VNC viewer",
                       ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "-qq", *DESKTOP_PACKAGES])]
    if prog.apt:
        steps.append(Step(f"install {prog.label}", ["apt-get", "install", "-y", "-qq", *prog.apt]))
    if prog.deb_url:
        steps.append(Step(f"download {prog.label}", download=prog.deb_url, root=False))
        steps.append(Step(f"install {prog.label}", ["apt-get", "install", "-y", "-qq", "{deb}"]))
    if key == "rdp":
        steps.append(Step("enable the xrdp service", ["systemctl", "enable", "--now", "xrdp"]))
    return steps


def run_install(key: str, dry_run: bool = False, allow_unverified: bool = False, log=print) -> None:
    prog = PROGRAMS[key] if key in PROGRAMS else None
    steps = install_plan(key)
    if prog and prog.deb_url and not prog.deb_sha256 and not allow_unverified and not dry_run:
        raise VortaxError(
            f"{prog.label}'s package has no pinned checksum in this release. Review the URL "
            f"({prog.deb_url}) and re-run with --allow-unverified to install it anyway."
        )
    sudo = [] if os.geteuid() == 0 else (["sudo"] if shutil.which("sudo") else None)
    if sudo is None and any(s.root for s in steps) and not dry_run:
        raise VortaxError("root is required to install packages and `sudo` is not available")
    deb_path = ""
    tmp = Path(tempfile.mkdtemp(prefix="vortax-access-"))
    try:
        for step in steps:
            log(f"==> {step.title}")
            if step.download:
                if dry_run:
                    log(f"    would download {step.download}")
                    continue
                deb_path = str(fetch.download(step.download, tmp, sha256=prog.deb_sha256, log=log))
                continue
            argv = [deb_path if a == "{deb}" else a for a in step.argv]
            if step.root:
                argv = [*(sudo or []), *argv]
            log("    " + " ".join(argv))
            if not dry_run and subprocess.run(argv).returncode != 0:
                raise VortaxError(f"step failed: {step.title}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if prog and prog.ports:
        log("ports used by this program (open them in your firewall only if you need inbound access): "
            + ", ".join(prog.ports))
    if prog and prog.note:
        log(prog.note)


def write_launcher(port: int, dest: Path | None = None) -> Path:
    """A one-click 'open the VM in a VNC viewer' script for the host desktop."""
    dest = dest or Path.home() / "Desktop" / "Connect_VNC.sh"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "#!/bin/sh\n"
        "# Opens the Vortax VM display. The password is shown by: vortax password\n"
        f"vncviewer 127.0.0.1::{port} -FullScreen -MenuKey F8 &\n"
        'sleep 3 && command -v wmctrl >/dev/null && wmctrl -r "VNC Viewer" -b add,above\n',
        encoding="utf-8")
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR)
    return dest


def set_access_password(key: str, password: str) -> str:
    """Set the unattended-access password for RustDesk/AnyDesk and return the program's ID."""
    if key == "rustdesk":
        exe = shutil.which("rustdesk")
        if not exe:
            raise VortaxError("rustdesk is not installed (vortax access install rustdesk)")
        subprocess.run([exe, "--password", password], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out = subprocess.run([exe, "--get-id"], capture_output=True, text=True)
        return out.stdout.strip()
    if key == "anydesk":
        exe = shutil.which("anydesk")
        if not exe:
            raise VortaxError("anydesk is not installed (vortax access install anydesk)")
        subprocess.run([exe, "--set-password"], input=password + "\n", text=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out = subprocess.run([exe, "--get-id"], capture_output=True, text=True)
        return out.stdout.strip()
    raise VortaxError(f"{key} has no unattended-access password to set")
