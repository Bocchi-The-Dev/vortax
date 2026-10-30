# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Tiny PPM -> PNG converter (QEMU's `screendump` writes PPM) using only zlib."""

from __future__ import annotations

import struct
import zlib

from .errors import VortaxError


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _tokens(buf: bytes, pos: int, count: int) -> tuple[list[int], int]:
    out: list[int] = []
    while len(out) < count:
        while pos < len(buf) and buf[pos:pos + 1].isspace():
            pos += 1
        if pos < len(buf) and buf[pos:pos + 1] == b"#":
            while pos < len(buf) and buf[pos:pos + 1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < len(buf) and not buf[pos:pos + 1].isspace():
            pos += 1
        if start == pos:
            raise VortaxError("truncated PPM header")
        out.append(int(buf[start:pos]))
    return out, pos + 1          # exactly one whitespace byte separates the header from the pixels


def ppm_to_png(ppm: bytes) -> bytes:
    if ppm[:2] != b"P6":
        raise VortaxError("screendump is not a binary PPM (P6)")
    (w, h, maxv), pos = _tokens(ppm, 2, 3)
    if maxv != 255 or w <= 0 or h <= 0:
        raise VortaxError("unsupported PPM depth or size")
    pixels = ppm[pos:pos + w * h * 3]
    if len(pixels) != w * h * 3:
        raise VortaxError("PPM pixel data is truncated")
    stride = w * 3
    raw = b"".join(b"\x00" + pixels[y * stride:(y + 1) * stride] for y in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b"")
