# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""The system and profile catalogs (built-in data + the user's own overrides)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from . import paths
from .errors import VortaxError
from .settings import Settings

MODE_PREFERENCE = ("img", "qcow2", "iso")


@dataclass
class System:
    id: str
    name: str
    family: str = "linux"
    icon: str = "disk"
    disk_gb: int = 40
    arch: str = "x86_64"
    sources: dict[str, str] = field(default_factory=dict)
    profile: str = ""
    aliases: list[str] = field(default_factory=list)
    notes: str = ""

    def modes(self) -> list[str]:
        return [m for m in MODE_PREFERENCE if self.sources.get(m)]

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["modes"] = self.modes()
        d["sources"] = {k: v for k, v in self.sources.items() if v}
        return d


def _read_builtin(name: str) -> dict[str, Any]:
    text = resources.files("vortax.data").joinpath(name).read_text(encoding="utf-8")
    return json.loads(text)


def _read_user(name: str) -> dict[str, Any]:
    path = paths.config_dir() / name
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VortaxError(f"cannot read {path}: {exc}") from None
    if not isinstance(data, dict):
        raise VortaxError(f"{path} must contain a JSON object")
    return data


def _system_from(entry: dict[str, Any], origin: str) -> System:
    if not isinstance(entry, dict) or not entry.get("id") or not entry.get("name"):
        raise VortaxError(f"{origin}: every system needs an 'id' and a 'name'")
    fields = {k: entry[k] for k in System.__dataclass_fields__ if k in entry}
    sysobj = System(**fields)
    paths.check_name(sysobj.id)
    bad = [m for m in sysobj.sources if m not in ("iso", "qcow2", "img")]
    if bad:
        raise VortaxError(f"{origin}: system {sysobj.id!r} has unknown boot mode(s): {', '.join(bad)}")
    return sysobj


def load_systems() -> dict[str, System]:
    """id -> System. User entries replace built-ins with the same id (case-insensitive)."""
    merged: dict[str, System] = {}
    for origin, doc in (("built-in catalog", _read_builtin("systems.json")),
                        ("user systems.json", _read_user("systems.json"))):
        for entry in doc.get("systems", []):
            sysobj = _system_from(entry, origin)
            for key in [k for k in merged if k.lower() == sysobj.id.lower()]:
                del merged[key]
            merged[sysobj.id] = sysobj
    return merged


def find_system(query: str, systems: dict[str, System] | None = None) -> System:
    systems = load_systems() if systems is None else systems
    q = query.strip().lower()
    for s in systems.values():
        if s.id.lower() == q or q in (a.lower() for a in s.aliases):
            return s
    close = sorted(s.id for s in systems.values() if q and q in s.id.lower())
    hint = f" Did you mean: {', '.join(close)}?" if close else " Run `vortax list` to see the catalog."
    raise VortaxError(f"unknown system {query!r}.{hint}")


def load_profiles() -> dict[str, dict[str, Any]]:
    merged = dict(_read_builtin("profiles.json").get("profiles", {}))
    merged.update(_read_user("profiles.json").get("profiles", {}))
    return merged


def find_profile(name: str, profiles: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    profiles = load_profiles() if profiles is None else profiles
    if name in profiles:
        return profiles[name]
    raise VortaxError(f"unknown profile {name!r}. Available: {', '.join(sorted(profiles))}")


@dataclass
class Resolved:
    """What a run will actually use, after merging the catalog with the settings."""
    system: System | None
    source: str
    mode: str          # iso | qcow2 | img | auto (unknown until the file is inspected)
    profile_name: str
    profile: dict[str, Any] | None
    arch: str
    disk_gb: int


def resolve(settings: Settings) -> Resolved:
    system = find_system(settings.system) if settings.system else None
    source, mode = settings.source.strip(), settings.boot_mode

    if not source:
        if system is None:
            raise VortaxError("nothing to run: pass --system <id> (see `vortax list`) or --source <url|path>")
        if mode == "auto":
            mode = next(iter(system.modes()), "")
            if not mode:
                raise _blank_slot(system)
        source = system.sources.get(mode, "")
        if not source:
            avail = ", ".join(system.modes()) or "none"
            if not system.modes():
                raise _blank_slot(system)
            raise VortaxError(f"{system.id} has no {mode!r} source (available modes: {avail})")

    profile_name = settings.profile or (system.profile if system else "")
    profile = None if profile_name in ("", "none") else find_profile(profile_name)
    arch = settings.arch or (system.arch if system else "") or "x86_64"
    disk_gb = settings.disk_gb or (system.disk_gb if system and system.disk_gb else 0) or 40
    return Resolved(system, source, mode, profile_name if profile else "", profile, arch, disk_gb)


def _blank_slot(system: System) -> VortaxError:
    note = f" {system.notes}" if system.notes else ""
    return VortaxError(f"{system.id} is a template slot with no bundled download.{note}")
