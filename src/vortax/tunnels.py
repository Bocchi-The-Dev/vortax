# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Make a VM's VNC port reachable from outside: bore, ngrok, tailscale, or nothing.

Tokens are never put on a command line (visible to every user via `ps`): ngrok reads
NGROK_AUTHTOKEN from its environment and tailscale reads its key from a 0600 file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import paths, settings
from .errors import MissingToolError, VortaxError

_BORE_RE = re.compile(r"(?P<host>[A-Za-z0-9.-]+):(?P<port>\d{2,5})")


@dataclass
class Tunnel:
    kind: str
    address: str = ""
    pid: int = 0

    def to_dict(self):
        return {"kind": self.kind, "address": self.address, "pid": self.pid}


# --- parsers (pure, unit-tested) ---------------------------------------------------


def parse_bore(log: str, server: str = "bore.pub") -> str:
    """Pull `bore.pub:12345` out of bore's output ('listening at bore.pub:12345')."""
    for line in reversed(log.splitlines()):
        if "listening at" in line:
            m = _BORE_RE.search(line.split("listening at", 1)[1])
            if m:
                return f"{m.group('host')}:{m.group('port')}"
    return ""


def parse_ngrok(api_json: str, port: int) -> str:
    """Pick the tcp tunnel for `port` from ngrok's local API response."""
    try:
        tunnels = json.loads(api_json).get("tunnels", [])
    except ValueError:
        return ""
    for t in tunnels:
        addr = str(t.get("config", {}).get("addr", ""))
        if addr.endswith(f":{port}") and t.get("public_url", "").startswith("tcp://"):
            return t["public_url"][len("tcp://"):]
    return ""


# --- process helpers ------------------------------------------------------------------


def _spawn(cmd: list[str], log: Path, env: dict | None = None) -> subprocess.Popen:
    logfh = open(log, "ab")
    try:
        return subprocess.Popen(cmd, stdout=logfh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, env=env)
    finally:
        logfh.close()


def _need(tool: str, hint: str) -> str:
    exe = shutil.which(tool)
    if not exe:
        raise MissingToolError(tool, hint)
    return exe


def _wait_for(fn, timeout: float, interval: float = 0.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return ""


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --- providers ---------------------------------------------------------------------------


def start_bore(port: int, run: Path, server: str = "bore.pub") -> Tunnel:
    exe = _need("bore", "cargo install bore-cli, or download a release from github.com/ekzhang/bore")
    log = run / "tunnel.log"
    log.write_bytes(b"")
    proc = _spawn([exe, "local", str(port), "--to", server], log)

    def addr():
        if proc.poll() is not None:
            raise VortaxError("bore exited immediately; see " + str(log))
        return parse_bore(log.read_text(errors="replace"), server)

    address = _wait_for(addr, 25)
    if not address:
        _kill(proc.pid)
        raise VortaxError(f"bore did not report a public address within 25s; see {log}")
    return Tunnel("bore", address, proc.pid)


def start_ngrok(port: int, run: Path) -> Tunnel:
    exe = _need("ngrok", "https://ngrok.com/download")
    token = settings.get_secret("ngrok_authtoken")
    if not token:
        raise VortaxError("ngrok needs a token: export NGROK_AUTHTOKEN or run `vortax secret set ngrok_authtoken`")
    log = run / "tunnel.log"
    log.write_bytes(b"")
    env = dict(os.environ, NGROK_AUTHTOKEN=token)
    proc = _spawn([exe, "tcp", str(port), "--log=stdout"], log, env)

    def addr():
        if proc.poll() is not None:
            raise VortaxError("ngrok exited immediately; see " + str(log))
        try:
            with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as r:
                return parse_ngrok(r.read().decode(), port)
        except OSError:
            return ""

    address = _wait_for(addr, 30)
    if not address:
        _kill(proc.pid)
        raise VortaxError(f"ngrok did not report a public address within 30s; see {log}")
    return Tunnel("ngrok", address, proc.pid)


def start_tailscale(port: int, run: Path, hostname: str) -> Tunnel:
    exe = _need("tailscale", "https://tailscale.com/download")
    key = settings.get_secret("tailscale_authkey")
    status = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True)
    if status.returncode != 0 or not status.stdout.strip():
        if not key:
            raise VortaxError("tailscale is not logged in: export TAILSCALE_AUTHKEY or run `tailscale up` yourself")
        keyfile = run / "tskey"
        paths.write_private(keyfile, key)
        try:
            up = subprocess.run([exe, "up", f"--auth-key=file:{keyfile}", f"--hostname={hostname}",
                                 "--accept-dns=false", "--ssh"], capture_output=True, text=True, timeout=90)
        finally:
            keyfile.unlink(missing_ok=True)
        if up.returncode != 0:
            raise VortaxError("tailscale up failed: " + (up.stderr.strip() or up.stdout.strip())[:300])
        status = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True)
    ip = status.stdout.strip().splitlines()[0] if status.stdout.strip() else ""
    if not ip:
        raise VortaxError("tailscale is up but reported no IPv4 address")
    return Tunnel("tailscale", f"{ip}:{port}", 0)


def start(kind: str, port: int, run: Path, hostname: str = "vortax", bore_server: str = "bore.pub") -> Tunnel:
    if kind == "none":
        return Tunnel("none", f"127.0.0.1:{port}", 0)
    if kind == "bore":
        return start_bore(port, run, os.environ.get("VORTAX_BORE_SERVER", bore_server))
    if kind == "ngrok":
        return start_ngrok(port, run)
    if kind == "tailscale":
        return start_tailscale(port, run, hostname)
    raise VortaxError(f"unknown tunnel {kind!r}")


def _kill(pid: int) -> None:
    if pid <= 0:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def stop(tunnel: dict | Tunnel | None) -> None:
    if not tunnel:
        return
    pid = tunnel.pid if isinstance(tunnel, Tunnel) else int(tunnel.get("pid", 0) or 0)
    _kill(pid)
