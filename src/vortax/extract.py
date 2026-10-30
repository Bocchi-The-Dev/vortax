# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Work out what a downloaded file is and turn it into something QEMU can boot.

File type detection reads magic bytes in Python instead of shelling out to `file`
(whose "gzip contains 'zip'" substring collision is exactly what broke the
original workflow on Batocera's .img.gz). Archive extraction validates every
member path so a hostile archive cannot write outside the work directory.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import os
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import disk
from .errors import MissingToolError, VortaxError

DISK_EXTS = (".qcow2", ".img", ".raw", ".vmdk", ".vdi", ".vhd", ".vhdx", ".qcow")
ARCHIVE_KINDS = ("gzip", "zip", "7z", "rar", "xz", "bzip2", "zstd", "tar")


def sniff(path: Path) -> str:
    """Return one of: iso qcow2 gzip zip 7z rar xz bzip2 zstd tar raw-or-unknown."""
    with open(path, "rb") as fh:
        head = fh.read(512)
        fh.seek(0x8001)
        iso1 = fh.read(5)
        fh.seek(0x8801)
        iso2 = fh.read(5)
        fh.seek(0x9001)
        iso3 = fh.read(5)
    if head[:4] == b"QFI\xfb":
        return "qcow2"
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:4] == b"PK\x03\x04" or head[:4] == b"PK\x05\x06":
        return "zip"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    if head[:4] == b"Rar!":
        return "rar"
    if head[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if head[:3] == b"BZh":
        return "bzip2"
    if head[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd"
    if b"CD001" in (iso1, iso2, iso3):
        return "iso"
    if head[257:262] == b"ustar":
        return "tar"
    return "unknown"


def _safe_target(base: Path, member: str) -> Path:
    target = (base / member).resolve()
    if base.resolve() not in target.parents and target != base.resolve():
        raise VortaxError(f"refusing archive member outside the work directory: {member}")
    return target


def _gzip_is_tar(path: Path) -> bool:
    try:
        with gzip.open(path, "rb") as gz:
            block = gz.read(512)
    except (OSError, EOFError):
        return False
    return block[257:262] == b"ustar"


def _extract_tar(path: Path, out: Path) -> None:
    with tarfile.open(path, "r:*") as tf:
        for member in tf.getmembers():
            _safe_target(out, member.name)
            if member.issym() or member.islnk():
                raise VortaxError(f"refusing archive link member: {member.name}")
        kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        tf.extractall(out, members=[m for m in tf.getmembers() if m.isfile() or m.isdir()], **kwargs)


def _extract_zip(path: Path, out: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            _safe_target(out, name)
        zf.extractall(out)


def _decompress_single(path: Path, out: Path, opener, suffixes: tuple[str, ...]) -> None:
    name = path.name
    for suf in suffixes:
        if name.lower().endswith(suf):
            name = name[: -len(suf)]
            break
    if not name or name == path.name:
        name = "extracted.img"
    if not name.lower().endswith(DISK_EXTS + (".iso",)):
        name += ".img"
    with opener(path, "rb") as src, open(out / name, "wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)


def _external(tool_names: tuple[str, ...], args_fn, path: Path, out: Path, hint: str) -> None:
    exe = next((shutil.which(t) for t in tool_names if shutil.which(t)), None)
    if not exe:
        raise MissingToolError(tool_names[0], hint)
    rc = subprocess.run(args_fn(exe, path, out), stdout=subprocess.DEVNULL).returncode
    if rc != 0:
        raise VortaxError(f"{os.path.basename(exe)} failed to extract {path.name} (exit {rc})")


def extract(path: Path, out: Path, kind: str | None = None) -> None:
    kind = kind or sniff(path)
    out.mkdir(parents=True, exist_ok=True)
    if kind == "gzip":
        if _gzip_is_tar(path):
            _extract_tar(path, out)
        else:
            _decompress_single(path, out, gzip.open, (".gz",))
    elif kind == "tar":
        _extract_tar(path, out)
    elif kind == "zip":
        _extract_zip(path, out)
    elif kind == "xz":
        try:
            _extract_tar(path, out)
        except tarfile.TarError:
            _decompress_single(path, out, lzma.open, (".xz",))
    elif kind == "bzip2":
        try:
            _extract_tar(path, out)
        except tarfile.TarError:
            _decompress_single(path, out, bz2.open, (".bz2",))
    elif kind == "7z":
        _external(("7z", "7zz", "7za", "7zr"), lambda exe, p, o: [exe, "x", "-y", f"-o{o}", str(p)],
                  path, out, "Debian: apt install p7zip-full; Termux: pkg install p7zip")
    elif kind == "rar":
        _external(("unrar", "unrar-free", "7z"),
                  lambda exe, p, o: [exe, "x", "-y", str(p), str(o)] if "unrar" in exe else [exe, "x", "-y", f"-o{o}", str(p)],
                  path, out, "Debian: apt install unrar-free")
    elif kind == "zstd":
        _external(("zstd",), lambda exe, p, o: [exe, "-d", "-q", "-f", "-o", str(o / (p.stem if p.suffix == ".zst" else "extracted.img")), str(p)],
                  path, out, "Debian: apt install zstd")
    else:
        raise VortaxError(f"don't know how to extract a {kind} file")


@dataclass
class Media:
    kind: str                 # "disk" | "iso"
    path: Path                # file to move into place
    disk_format: str = ""     # qcow2 | raw  (disk only)
    note: str = ""


def _walk_files(root: Path):
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            yield Path(dirpath) / f


def find_media(work: Path, depth: int = 0) -> Media:
    """Locate a disk image or ISO inside an extraction directory (one nested archive level allowed)."""
    files = list(_walk_files(work))
    disks = sorted((f for f in files if f.suffix.lower() in DISK_EXTS), key=lambda f: f.stat().st_size, reverse=True)
    if disks:
        return _classify_disk(disks[0])
    isos = sorted((f for f in files if f.suffix.lower() == ".iso"), key=lambda f: f.stat().st_size, reverse=True)
    if isos:
        return Media("iso", isos[0])
    if depth < 2:
        for f in sorted(files, key=lambda f: f.stat().st_size, reverse=True):
            if sniff(f) in ARCHIVE_KINDS:
                nested = f.parent / (f.name + ".d")
                extract(f, nested)
                return find_media(nested, depth + 1)
    raise VortaxError("no disk image (.qcow2/.img/.raw/...) or ISO found inside the download")


def _classify_disk(path: Path) -> Media:
    fmt = disk.detect_format(path)
    return Media("disk", path, fmt)


def classify_download(path: Path) -> tuple[str, str]:
    """('iso'|'archive'|'qcow2'|'disk'|'unknown', sniffed kind)"""
    kind = sniff(path)
    if kind == "iso":
        return "iso", kind
    if kind == "qcow2":
        return "qcow2", kind
    if kind in ARCHIVE_KINDS:
        return "archive", kind
    if path.suffix.lower() in DISK_EXTS:
        return "disk", kind
    return "unknown", kind
