# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""`latest:<name>` catalog sources: look the current ISO up on the distro's own index page.

Distros rename ISOs every release, so a pinned URL rots. These resolvers read the public
directory listing and pick the newest file. They are best-effort and marked unverified in
the catalog; `vortax check-links` and the resolver tests tell you when one breaks.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Callable

from .errors import VortaxError

UA = "Mozilla/5.0 (compatible; vortax)"

# name -> (index page, filename regex, base URL for the file)
INDEXES: dict[str, tuple[str, str]] = {
    "alpine": ("https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/x86_64/",
               r"alpine-virt-\d+\.\d+\.\d+-x86_64\.iso"),
    "debian": ("https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/",
               r"debian-\d+\.\d+\.\d+-amd64-netinst\.iso"),
    "ubuntu-server": ("https://releases.ubuntu.com/noble/",
                      r"ubuntu-24\.04(?:\.\d+)?-live-server-amd64\.iso"),
    "arch": ("https://geo.mirror.pkgbuild.com/iso/latest/",
             r"archlinux-\d{4}\.\d{2}\.\d{2}-x86_64\.iso"),
}
STATIC = {
    "tumbleweed": "https://download.opensuse.org/tumbleweed/iso/openSUSE-Tumbleweed-NET-x86_64-Current.iso",
}


def _version_key(name: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", name))


def pick_latest(listing: str, pattern: str) -> str:
    names = sorted(set(re.findall(pattern, listing)), key=_version_key)
    if not names:
        raise VortaxError("no matching ISO in the directory listing (the distro may have renamed it)")
    return names[-1]


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read(3_000_000).decode("utf-8", "replace")
    except (OSError, urllib.error.URLError) as exc:
        raise VortaxError(f"could not read {url}: {exc}") from None


def resolve_latest(name: str, fetch_text: Callable[[str], str] = _fetch_text) -> str:
    if name in STATIC:
        return STATIC[name]
    if name not in INDEXES:
        raise VortaxError(f"unknown latest: source {name!r} (known: {', '.join(sorted({*INDEXES, *STATIC}))})")
    base, pattern = INDEXES[name]
    return base + pick_latest(fetch_text(base), pattern)
