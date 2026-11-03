# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Change a running VM through QEMU's monitor: swap or add a CD, plug in or pull out a sound card.

Nothing here restarts the guest. What the guest does with a new device is up to the guest: Linux notices it
at once, Windows XP usually needs a rescan (Device Manager > Action > Scan for hardware changes).
"""

from __future__ import annotations

import re
from pathlib import Path

from .errors import VortaxError

# PCI cards can be plugged in while running. sb16 is an ISA card and cannot.
HOT_AUDIO = {"ac97": ("live_snd", ["AC97,audiodev=snd0,id=live_snd"]),
             "es1370": ("live_snd", ["ES1370,audiodev=snd0,id=live_snd"]),
             "hda": ("live_hda", ["intel-hda,id=live_hda", "hda-duplex,bus=live_hda.0,audiodev=snd0,id=live_hdacodec"])}
ACTIONS = ("cd_attach", "cd_eject", "audio_add", "audio_remove")


def _ok(out: str, hint: str = "") -> None:
    """A successful monitor command prints nothing; anything it prints is an error message."""
    text = (out or "").strip()
    if text:
        if "snd0" in text and "audiodev" in text:
            text += " (this VM was started without an audio backend: restart it once, then sound cards can be added live)"
        raise VortaxError(f"{hint}{text}")


def removable_drives(info_block: str) -> list[str]:
    """Names of CD-style drives from `info block` (a block starts at column 0 and lists 'Removable device')."""
    names, cur = [], None
    for line in info_block.splitlines():
        if line and not line[0].isspace():
            cur = line.split(" ", 1)[0]
        elif cur and "Removable device" in line:
            names.append(cur)
            cur = None
    return names


def _quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _iso_path(raw: str) -> str:
    p = Path(str(raw or "")).expanduser()
    if not p.is_file():
        raise VortaxError(f"ISO not found: {p}")
    if any(c in str(p) for c in "\r\n"):
        raise VortaxError("bad characters in the file name")
    return str(p.resolve())


def attach_cd(inst, raw_path: str) -> str:
    iso = _iso_path(raw_path)
    state = inst.state()
    if state.get("live_cd") == "usb":
        detach_cd(inst)
    drives = removable_drives(inst.hmp("info block"))
    if drives:                                                  # swap the disc in an existing CD drive
        dev = drives[0]
        inst.hmp(f"eject -f {dev}")
        _ok(inst.hmp(f"change {dev} {_quote(iso)}"), "could not insert the disc: ")
        return f"inserted {Path(iso).name} into {dev}"
    if re.search(r"\s", iso):
        raise VortaxError("this VM has no CD drive, and a new one can only be added for a path without spaces")
    opt = iso.replace(",", ",,")
    _ok(inst.hmp(f"drive_add 0 if=none,id=live_cd,file={opt},media=cdrom,readonly=on"), "could not open the ISO: ")
    out = inst.hmp("device_add usb-storage,drive=live_cd,id=live_cd_dev,removable=on")
    if out.strip():
        inst.hmp("drive_del live_cd")
        _ok(out, "could not add a USB CD drive: ")
    inst.update_state(live_cd="usb")
    return f"added {Path(iso).name} as a USB CD drive"


def detach_cd(inst) -> str:
    if inst.state().get("live_cd") == "usb":
        inst.hmp("device_del live_cd_dev")
        inst.hmp("drive_del live_cd")
        inst.update_state(live_cd="")
        return "removed the added CD drive"
    drives = removable_drives(inst.hmp("info block"))
    if not drives:
        raise VortaxError("this VM has no CD drive")
    for dev in drives:
        inst.hmp(f"eject -f {dev}")
    return f"ejected the disc from {', '.join(drives)}"


def add_audio(inst, model: str) -> str:
    if model == "sb16":
        raise VortaxError("sb16 is an ISA card and cannot be plugged in while running; use ac97, es1370 or hda")
    if model not in HOT_AUDIO:
        raise VortaxError(f"unknown sound card {model!r} (choose {', '.join(HOT_AUDIO)})")
    if inst.state().get("live_audio"):
        remove_audio(inst)
    for cmd in HOT_AUDIO[model][1]:
        _ok(inst.hmp(f"device_add {cmd}"), "could not add the sound card: ")
    inst.update_state(live_audio=model)
    return f"added a {model} sound card; in Windows XP run Device Manager > Action > Scan for hardware changes"


def remove_audio(inst) -> str:
    model = inst.state().get("live_audio")
    if not model:
        raise VortaxError("no sound card was added live to this VM (cards present from the start need a restart to change)")
    _ok(inst.hmp(f"device_del {HOT_AUDIO[model][0]}"), "could not remove the sound card: ")
    inst.update_state(live_audio="")
    return f"removed the {model} sound card"


def apply(inst, action: str, value: str = "") -> str:
    if not inst.running():
        raise VortaxError(f"{inst.name} is not running; use `vortax edit` for a stopped VM")
    if action == "cd_attach":
        return attach_cd(inst, value)
    if action == "cd_eject":
        return detach_cd(inst)
    if action == "audio_add":
        return add_audio(inst, value)
    if action == "audio_remove":
        return remove_audio(inst)
    raise VortaxError(f"unknown action {action!r} (choose {', '.join(ACTIONS)})")
