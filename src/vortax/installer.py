# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""`vortax doctor --fix`: install what `vortax doctor` reports as missing, with the system package manager.

Rules this module keeps, so that --fix stays boring and safe:

* Only packages from the table below are ever installed, by exact name, through the distribution's own package
  manager. Nothing is downloaded from anywhere else and nothing is piped into a shell.
* The exact commands are shown first; you must confirm (or pass --yes). --dry-run shows them and stops.
* Packages are installed one at a time, so one unknown package name on your distribution cannot block the rest.
* Tools that only come from third parties (bore, ngrok, tailscale) and system settings (the kvm group) are never
  changed for you; their instructions are printed instead.
* apt (Debian, Ubuntu) and Termux (`pkg`) are the primary targets. dnf, zypper, pacman, apk and brew are
  best-effort: a package name that does not exist there is reported as failed, never guessed at.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from . import envinfo
from .errors import VortaxError

REQUIRED, RECOMMENDED, OPTIONAL = "required", "recommended", "optional"

# doctor check name -> (tier, {package manager: package}). A missing manager entry means "no package known":
# the check's own hint is printed instead.
_PACKAGES: dict[str, tuple[str, dict[str, str]]] = {
    "qemu-system-x86_64": (REQUIRED, {
        "apt": "qemu-system-x86", "pkg": "qemu-system-x86-64-headless", "dnf": "qemu-system-x86-core",
        "zypper": "qemu-x86", "pacman": "qemu-system-x86", "apk": "qemu-system-x86_64", "brew": "qemu"}),
    "qemu-img": (REQUIRED, {
        "apt": "qemu-utils", "pkg": "qemu-utils", "dnf": "qemu-img", "zypper": "qemu-tools",
        "pacman": "qemu-img", "apk": "qemu-img", "brew": "qemu"}),
    "UEFI firmware (x86_64)": (RECOMMENDED, {
        "apt": "ovmf", "dnf": "edk2-ovmf", "zypper": "qemu-ovmf-x86_64", "pacman": "edk2-ovmf", "apk": "ovmf"}),
    "downloader (curl/wget/aria2c)": (RECOMMENDED, {
        "apt": "curl", "pkg": "curl", "dnf": "curl", "zypper": "curl", "pacman": "curl", "apk": "curl", "brew": "curl"}),
    "7z": (RECOMMENDED, {
        "apt": "p7zip-full", "pkg": "p7zip", "dnf": "p7zip", "zypper": "p7zip-full", "pacman": "p7zip",
        "brew": "p7zip"}),
    "websockify": (RECOMMENDED, {
        "apt": "websockify", "pkg": "python-websockify", "dnf": "python3-websockify",
        "zypper": "python3-websockify"}),
    "noVNC web client": (RECOMMENDED, {"apt": "novnc", "pkg": "novnc", "dnf": "novnc"}),
    "termux-wake-lock": (RECOMMENDED, {"pkg": "termux-api"}),
    "aria2c": (OPTIONAL, {
        "apt": "aria2", "pkg": "aria2", "dnf": "aria2", "zypper": "aria2", "pacman": "aria2", "apk": "aria2",
        "brew": "aria2"}),
    "unrar": (OPTIONAL, {"apt": "unrar-free", "pkg": "unrar", "pacman": "unrar", "apk": "unrar"}),
    "zstd": (OPTIONAL, {
        "apt": "zstd", "pkg": "zstd", "dnf": "zstd", "zypper": "zstd", "pacman": "zstd", "apk": "zstd",
        "brew": "zstd"}),
    "rclone": (OPTIONAL, {
        "apt": "rclone", "pkg": "rclone", "dnf": "rclone", "zypper": "rclone", "pacman": "rclone",
        "apk": "rclone", "brew": "rclone"}),
    "qemu-system-aarch64": (OPTIONAL, {
        "apt": "qemu-system-arm", "pkg": "qemu-system-aarch64-headless", "dnf": "qemu-system-aarch64-core",
        "zypper": "qemu-arm", "pacman": "qemu-system-aarch64", "apk": "qemu-system-aarch64", "brew": "qemu"}),
}

# Checks that are never fixed automatically, with the reason shown to the user.
_MANUAL = {
    "bore": "comes from a third party; install it yourself (cargo install bore-cli)",
    "ngrok": "comes from a third party; see https://ngrok.com/download",
    "tailscale": "comes from a third party; see https://tailscale.com/download",
    "KVM acceleration": "/dev/kvm exists but you cannot use it: add your user to the 'kvm' group, then log in again",
}

_COMMANDS = {
    "apt": ["apt-get", "install", "-y"],
    "pkg": ["pkg", "install", "-y"],
    "dnf": ["dnf", "install", "-y"],
    "zypper": ["zypper", "--non-interactive", "install"],
    "pacman": ["pacman", "-S", "--needed", "--noconfirm"],
    "apk": ["apk", "add"],
    "brew": ["brew", "install"],
}
_REFRESH = {
    "apt": ["apt-get", "update"],
    "zypper": ["zypper", "--non-interactive", "refresh"],
    "apk": ["apk", "update"],
}
_NO_ROOT = ("pkg", "brew")      # Termux and Homebrew run as your own user


@dataclass
class Plan:
    manager: str
    install: list[tuple[str, str, str]] = field(default_factory=list)   # (check name, tier, package)
    manual: list[tuple[str, str]] = field(default_factory=list)         # (check name, what to do)
    skipped_optional: list[str] = field(default_factory=list)

    @property
    def packages(self) -> list[str]:
        seen: list[str] = []
        for _, _, pkg in self.install:
            if pkg not in seen:
                seen.append(pkg)
        return seen


@dataclass
class Result:
    installed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    ran: bool = False


def detect_manager() -> str | None:
    if envinfo.is_termux() and shutil.which("pkg"):
        return "pkg"
    for name, exe in (("apt", "apt-get"), ("dnf", "dnf"), ("zypper", "zypper"), ("pacman", "pacman"),
                      ("apk", "apk"), ("brew", "brew")):
        if shutil.which(exe):
            return name
    return None


def make_plan(checks, manager: str, include_optional: bool = False) -> Plan:
    """Turn doctor's failed checks into packages to install and notes for the things we leave alone."""
    plan = Plan(manager)
    for c in checks:
        if c.ok:
            continue
        if c.name in _PACKAGES:
            tier, by_manager = _PACKAGES[c.name]
            pkg = by_manager.get(manager)
            if tier == OPTIONAL and not include_optional:
                plan.skipped_optional.append(c.name)
            elif pkg:
                plan.install.append((c.name, tier, pkg))
            else:
                plan.manual.append((c.name, c.hint or f"no {manager} package is known for this; install it yourself"))
        elif c.name in _MANUAL:
            if c.name == "KVM acceleration" and "not accessible" not in c.detail:
                continue            # no /dev/kvm at all: nothing to fix, guests simply run under TCG
            plan.manual.append((c.name, _MANUAL[c.name]))
        elif c.hint:
            plan.manual.append((c.name, c.hint))
    return plan


def _privilege_prefix(manager: str) -> list[str]:
    if manager in _NO_ROOT or getattr(os, "geteuid", lambda: 0)() == 0:
        return []
    for tool in ("sudo", "doas"):
        if shutil.which(tool):
            return [tool]
    raise VortaxError("installing packages needs root: run this as root, or install sudo")


def commands(plan: Plan, prefix: list[str]) -> list[list[str]]:
    """Every command that would run, in order. One package per command."""
    cmds: list[list[str]] = []
    if plan.packages and plan.manager in _REFRESH:
        cmds.append([*prefix, *_REFRESH[plan.manager]])
    for pkg in plan.packages:
        cmds.append([*prefix, *_COMMANDS[plan.manager], pkg])
    return cmds


def _run(argv: list[str], manager: str) -> int:
    env = dict(os.environ)
    if manager == "apt":
        env["DEBIAN_FRONTEND"] = "noninteractive"
    return subprocess.run(argv, env=env).returncode


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def fix(checks, yes: bool = False, dry_run: bool = False, include_optional: bool = False, log=print) -> Result:
    manager = detect_manager()
    if not manager:
        raise VortaxError("no supported package manager found (apt, pkg, dnf, zypper, pacman, apk, brew); "
                          "install the tools listed above by hand")
    plan = make_plan(checks, manager, include_optional)
    log(f"\npackage manager: {manager}")

    if plan.manual:
        log("not installed automatically:")
        for name, note in plan.manual:
            log(f"  {name}: {note}")
    if plan.skipped_optional:
        log(f"optional, skipped (add --optional to include): {', '.join(plan.skipped_optional)}")

    if not plan.install:
        log("nothing to install with the package manager.")
        return Result()

    log("will install:")
    for name, tier, pkg in plan.install:
        log(f"  {pkg}   ({name}, {tier})")

    prefix = _privilege_prefix(manager) if not dry_run else _safe_prefix(manager)
    cmds = commands(plan, prefix)
    log("commands:")
    for argv in cmds:
        log("  " + " ".join(argv))

    if dry_run:
        log("dry run: nothing was installed.")
        return Result()
    if not yes:
        if not sys.stdin.isatty():
            raise VortaxError("--fix asks before installing; run it in a terminal, or add --yes")
        if not _confirm("proceed? [y/N] "):
            log("cancelled; nothing was installed.")
            return Result()

    result = Result(ran=True)
    refresh = len(cmds) - len(plan.packages)       # 1 when a "refresh package lists" command comes first
    for argv in cmds[:refresh]:
        if _run(argv, manager) != 0:
            log(f"warning: `{' '.join(argv)}` failed; continuing with the installs")
    for pkg, argv in zip(plan.packages, cmds[refresh:]):
        log(f"==> {pkg}")
        if _run(argv, manager) == 0:
            result.installed.append(pkg)
        else:
            result.failed.append(pkg)
    if result.installed:
        log("installed: " + ", ".join(result.installed))
    if result.failed:
        log("could not install: " + ", ".join(result.failed))
        hint = " Try `pkg update` first." if manager == "pkg" else ""
        log("the package name may differ on your distribution, or the package lists are stale." + hint)
    return result


def _safe_prefix(manager: str) -> list[str]:
    """For --dry-run: show the commands as they would be run, even where sudo is missing."""
    try:
        return _privilege_prefix(manager)
    except VortaxError:
        return ["sudo"]
