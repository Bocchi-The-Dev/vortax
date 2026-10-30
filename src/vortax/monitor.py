# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Talk to QEMU's human monitor over a unix socket.

The socket lives in the VM's private run directory (0700), so unlike a localhost TCP
monitor it cannot be reached by other users on the machine. The VNC password is sent
through it, which keeps the password out of the process list.
"""

from __future__ import annotations

import re
import socket
import time

from .errors import VortaxError

_PROMPT = b"(qemu)"
_ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\r")


def _read_until_prompt(sock: socket.socket, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    buf = b""
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            break
        buf += chunk
        if _PROMPT in buf:
            break
    return buf


def hmp(sock_path: str, command: str, timeout: float = 5.0) -> str:
    """Run one HMP command and return its output (without echo or prompt)."""
    if "\n" in command or "\r" in command:
        raise VortaxError("monitor commands cannot contain newlines")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(sock_path)
        _read_until_prompt(s, timeout)                 # banner
        s.sendall(command.encode() + b"\n")
        raw = _read_until_prompt(s, timeout)
    except OSError as exc:
        raise VortaxError(f"cannot reach the VM monitor ({exc}); is the VM running?") from None
    finally:
        s.close()
    text = _ANSI.sub(b"", raw)
    text = text.replace(_PROMPT, b"").decode("utf-8", "replace")
    lines = text.split("\n")
    if lines and command[:20] in lines[0]:             # drop the echoed command line
        lines = lines[1:]
    return "\n".join(lines).strip()


def set_vnc_password(sock_path: str, password: str) -> None:
    """Apply a VNC password. Tries `set_password` first, then the older `change` form."""
    if any(c in password for c in "\r\n") or not password:
        raise VortaxError("VNC password must be non-empty and single-line")
    out = hmp(sock_path, f"set_password vnc {password}")
    if "unknown command" in out.lower():
        out = hmp(sock_path, f"change vnc password {password}")
    out = out.replace(password, "***")        # the monitor may echo the command; never repeat a password
    if out and "error" in out.lower():
        raise VortaxError(f"could not set the VNC password: {out}")
