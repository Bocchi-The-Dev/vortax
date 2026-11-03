# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Dashboard HTTP server (standard library only).

Security model, because a VM controller is a juicy target:
  * binds to 127.0.0.1 by default; every /api call needs a secret token
    (sent as `X-Vortax-Token`, compared in constant time)
  * with a loopback bind the Host header must be loopback too (DNS-rebinding guard)
  * strict Content-Security-Policy: the UI loads nothing from anywhere else
  * no shell: every action calls the same Python functions the CLI uses
  * the token travels in the URL *fragment*, which browsers never send to servers
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import __license__, __source_url__, __version__, catalog, presets, disk, doctor, extract, launch, live, paths, session, settings, tunnels
from ..errors import VortaxError
from .stats import Sampler
from .trust import Trust

MAX_BODY = 64 * 1024
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
NOVNC_DIRS = ("/usr/share/novnc", "/usr/share/novnc/utils/../", "/data/data/com.termux/files/usr/share/novnc")
RUN_KEYS = {f for f in settings.Settings.__dataclass_fields__}


# ---------------------------------------------------------------- jobs


class Job:
    def __init__(self, kind: str, name: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.name = kind, name
        self.status = "running"
        self.phase = "starting"
        self.lines: list[str] = []
        self.error = ""
        self.started = time.time()
        self.finished = 0.0

    def log(self, msg: str) -> None:
        for line in str(msg).splitlines():
            self.lines.append(line)
        del self.lines[:-300]
        low = str(msg).lower()
        if "download" in low:
            self.phase = "download"
        elif "extract" in low or "detected" in low or "converting" in low:
            self.phase = "prepare"
        elif "started" in low or "tunnel" in low:
            self.phase = "launch"

    def to_dict(self) -> dict:
        d = {"id": self.id, "kind": self.kind, "name": self.name, "status": self.status, "phase": self.phase,
             "error": self.error, "log": self.lines[-60:], "elapsed": round((self.finished or time.time()) - self.started)}
        if self.status == "running" and self.phase == "download":
            d["downloaded"] = _newest_cache_size()
        return d


def _newest_cache_size() -> int:
    best, best_m = 0, 0.0
    try:
        for p in paths.cache_dir().iterdir():
            if p.suffix == ".done" or not p.is_file():
                continue
            st = p.stat()
            if st.st_mtime >= best_m:
                best, best_m = st.st_size, st.st_mtime
    except OSError:
        pass
    return best


class State:
    """Process-wide dashboard state."""

    def __init__(self, token: str, host: str, port: int, trust: Trust | None = None):
        self.token, self.host, self.port = token, host, port
        self.trust = trust or Trust()
        self.sampler = Sampler()
        self.sampler.start()
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def new_job(self, kind: str, name: str, fn) -> Job:
        job = Job(kind, name)
        with self.lock:
            busy = [j for j in self.jobs.values() if j.status == "running" and j.name == name]
            if busy:
                raise VortaxError(f"{name} already has a job running ({busy[0].kind})")
            self.jobs[job.id] = job
            for old in sorted(self.jobs.values(), key=lambda j: j.started)[:-20]:
                if old.status != "running":
                    self.jobs.pop(old.id, None)

        def target():
            try:
                fn(job)
                job.status = "done"
            except VortaxError as exc:
                job.status, job.error = "failed", str(exc)
            except Exception as exc:                       # keep the server alive whatever happens
                job.status, job.error = "failed", f"unexpected error: {exc!r}"
            job.finished = time.time()

        threading.Thread(target=target, daemon=True).start()
        return job


# ---------------------------------------------------------------- API actions


def _clean_run_body(body: dict) -> dict:
    cli = {}
    for key, value in body.items():
        if key in RUN_KEYS and value not in (None, ""):
            cli[key] = value
    return cli


def _job_run(body: dict):
    cli = _clean_run_body(body)
    preset = presets.get(str(body["preset"])) if body.get("preset") else {}
    save_as = str(body.get("save_preset") or "")

    def fn(job: Job):
        name = cli.get("name", "vortax")
        saved = {}
        if not (cli.get("system") or cli.get("source") or preset):
            saved = session.load_saved_settings(session.Instance(name))
        layered = {**settings.load_config_file(), **saved, **preset}
        s = settings.resolve(cli, config=layered if (saved or preset) else None)
        if save_as:
            presets.save(save_as, {k: v for k, v in cli.items() if k != "name"})
        resolved = catalog.resolve(s)
        inst = session.Instance(s.name)
        job.log(f"preparing {inst.name}")
        prepared = session.prepare(s, resolved, inst, force=bool(body.get("rebuild")), log=job.log)
        launch.launch(s, resolved, inst, prepared, log=job.log)
    return fn


def _instance_summary(name: str, sampler: Sampler) -> dict:
    inst = session.Instance(name)
    st = inst.status()
    snap = sampler.snapshot()["vm"].get(str(st["pid"]), {}) if st["running"] else {}
    st["cpu_pct"] = snap.get("cpu_pct", 0.0)
    st["rss"] = snap.get("rss", 0)
    st["profile"] = inst.state().get("profile", "")
    st["boot"] = session.load_saved_settings(inst).get("boot", "auto")
    return st


def _storage() -> dict:
    items = []
    d = paths.disks_dir()
    if d.exists():
        for p in sorted(d.iterdir()):
            if p.is_file():
                items.append({"kind": "disk", "name": p.name, "size": disk.size_bytes(p)})
    m = paths.media_dir()
    if m.exists():
        for p in sorted(m.iterdir()):
            if p.is_file() or p.is_symlink():
                items.append({"kind": "media", "name": p.name, "size": disk.size_bytes(p)})
    c = paths.cache_dir()
    if c.exists():
        for p in sorted(c.iterdir()):
            if p.is_file():
                items.append({"kind": "cache", "name": p.name, "size": disk.size_bytes(p)})
    return {"data_dir": str(paths.data_dir()), "items": items}


def _delete_storage(kind: str, name: str) -> None:
    base = {"disk": paths.disks_dir(), "media": paths.media_dir(), "cache": paths.cache_dir()}.get(kind)
    if base is None or "/" in name or name.startswith("."):
        raise VortaxError("invalid storage item")
    target = base / name
    if not (target.is_file() or target.is_symlink()):
        raise VortaxError("no such file")
    if kind == "disk":
        vm = target.name.rsplit(".", 1)[0]
        if session.Instance(vm).running():
            raise VortaxError(f"{vm} is running; stop it before deleting its disk")
    target.unlink()
    if kind == "cache":
        target.with_name(target.name + ".done").unlink(missing_ok=True)


def _webvnc(state: State, inst: session.Instance, action: str) -> dict:
    st = inst.state()
    web = st.get("webvnc") or {}
    if action == "stop" or (web.get("pid") and action == "restart"):
        tunnels._kill(int(web.get("pid", 0) or 0))
        inst.update_state(webvnc={})
        if action == "stop":
            return {}
    if not inst.running():
        raise VortaxError(f"{inst.name} is not running")
    exe = shutil.which("websockify")
    novnc = next((d for d in NOVNC_DIRS if Path(d, "vnc.html").exists()), "")
    if not exe or not novnc:
        raise VortaxError("browser VNC needs websockify and noVNC (Debian: apt install websockify novnc; "
                          "Termux: pkg install novnc websockify)")
    port = 6080
    while tunnels.port_open("127.0.0.1", port, 0.2) and port < 6200:
        port += 1
    vnc_port = int(st["vnc"]["port"])
    bind = state.host if state.host not in LOOPBACK else "127.0.0.1"
    proc = subprocess.Popen([exe, "--web", novnc, f"{bind}:{port}", f"127.0.0.1:{vnc_port}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                            start_new_session=True)
    time.sleep(0.8)
    if proc.poll() is not None:
        raise VortaxError("websockify exited immediately")
    info = {"pid": proc.pid, "port": port}
    inst.update_state(webvnc=info)
    return {**info, "path": f"/vnc.html?autoconnect=1&resize=scale&host={{host}}&port={port}"}


# ---------------------------------------------------------------- HTTP handler


class Handler(BaseHTTPRequestHandler):
    server_version = f"vortax/{__version__}"
    protocol_version = "HTTP/1.1"
    state: State

    def log_message(self, fmt, *args):      # keep the terminal quiet; errors go through log_error
        pass

    # --- plumbing
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data).encode(), "application/json; charset=utf-8")

    def _error(self, code: int, msg: str) -> None:
        # An error may leave an unread request body on the socket; do not reuse the connection.
        self.close_connection = True
        self._send(code, json.dumps({"error": msg}).encode(), "application/json; charset=utf-8", {"Connection": "close"})

    def _host_ok(self) -> bool:
        if self.state.host not in LOOPBACK and self.state.host not in ("::1",):
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0] if not (self.headers.get("Host") or "").startswith("[") \
            else (self.headers.get("Host") or "").split("]")[0] + "]"
        return host in LOOPBACK

    def _key_ok(self, supplied: str) -> bool:
        """The owner's master token (kept on disk, used by scripts) or the key of a trusted device."""
        if not supplied:
            return False
        if hmac.compare_digest(supplied.encode(), self.state.token.encode()):
            return True
        return self.state.trust.verify(supplied)

    def _authed(self) -> bool:
        return self._key_ok(self.headers.get("X-Vortax-Token", ""))

    def _auth_route(self, method: str, path: str) -> None:
        """Sign-in endpoints. They work without a key, so they are throttled per address and need JSON."""
        trust, who = self.state.trust, self.client_address[0]
        name = path[len("/api/auth/"):].strip("/")
        if method == "GET" and name == "info":
            return self._json(trust.info())
        if method != "POST" or name not in ("claim", "login"):
            return self._error(404, "no such endpoint")
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._error(415, "POST bodies must be application/json")
        body = self._body()
        label = body.get("label", "")
        key = trust.claim(body.get("code", ""), label, who) if name == "claim" else trust.login(body.get("password", ""), label, who)
        return self._json({"key": key})

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise VortaxError("bad Content-Length") from None
        if length > MAX_BODY:
            raise VortaxError("request too large")
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            raise VortaxError("body must be JSON") from None
        if not isinstance(data, dict):
            raise VortaxError("body must be a JSON object")
        return data

    # --- routing
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method: str) -> None:
        try:
            if not self._host_ok():
                return self._error(403, "bad Host header")
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            if path == "/ws/vnc":
                return self._console(query)
            if method == "GET" and not path.startswith("/api/"):
                return self._static(path)
            if path.startswith("/api/auth/"):
                return self._auth_route(method, path)
            if not self._authed():
                return self._error(401, "missing or wrong token")
            if method == "POST" and "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._error(415, "POST bodies must be application/json")
            handler = getattr(self, "api_" + path[len("/api/"):].strip("/").replace("/", "_"), None)
            if handler is None or not callable(handler):
                return self._error(404, "no such endpoint")
            body = self._body() if method == "POST" else {}
            q = {k: v[0] for k, v in query.items()}
            if (method == "POST") != getattr(handler, "post", False):
                return self._error(405, "wrong method")
            return self._json(handler(body, q))
        except VortaxError as exc:
            return self._error(400, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:           # never leak a traceback to the client
            sys.stderr.write(f"dashboard: unhandled {exc!r}\n")
            return self._error(500, "internal error")

    def _console(self, query: dict) -> None:
        """WebSocket -> VNC. The token rides in the subprotocol header since browsers cannot set headers."""
        from . import ws
        protos = [p.strip() for p in (self.headers.get("Sec-WebSocket-Protocol") or "").split(",")]
        token = next((p[4:] for p in protos if p.startswith("tok.")), "")
        if not self._key_ok(token):
            return self._error(401, "missing or wrong token")
        origin = self.headers.get("Origin") or ""
        if origin and urlparse(origin).netloc != (self.headers.get("Host") or ""):
            return self._error(403, "cross-origin WebSocket refused")
        key = self.headers.get("Sec-WebSocket-Key")
        if "websocket" not in (self.headers.get("Upgrade") or "").lower() or not key:
            return self._error(400, "expected a WebSocket upgrade")
        inst = session.Instance((query.get("name") or ["vortax"])[0])
        st = inst.state()
        if not inst.running() or not st.get("vnc"):
            return self._error(409, f"{inst.name} is not running")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws.accept_key(key))
        self.send_header("Sec-WebSocket-Protocol", "vortax.v1")
        self.end_headers()
        self.close_connection = True
        conn = ws.WebSocket(self.connection)
        try:
            ws.serve_console(conn, int(st["vnc"]["port"]), inst.vnc_password())
        except (VortaxError, OSError, ws.WSClosed):
            pass
        finally:
            conn.close()

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        if "/" in name or name.startswith(".") or Path(name).suffix not in CONTENT_TYPES:
            return self._error(404, "not found")
        try:
            data = resources.files("vortax.dashboard").joinpath("static").joinpath(name).read_bytes()
        except (OSError, FileNotFoundError):
            return self._error(404, "not found")
        self._send(200, data, CONTENT_TYPES[Path(name).suffix])

    # --- endpoints (method name = path with / -> _ ; `post = True` marks mutations)
    def api_overview(self, body, q):
        names = session.list_instances()
        pids = [session.Instance(n).pid() for n in names]
        self.state.sampler.watch([p for p in pids if p])
        return {
            "version": __version__, "stats": self.state.sampler.snapshot(),
            "instances": [_instance_summary(n, self.state.sampler) for n in names],
            "jobs": [j.to_dict() for j in sorted(self.state.jobs.values(), key=lambda j: -j.started)[:6]],
        }

    def api_systems(self, body, q):
        return {"systems": [s.to_dict() for s in catalog.load_systems().values()],
                "profiles": {k: {"name": v.get("name", k), "notes": v.get("notes", ""), "boot": v.get("boot", "")}
                             for k, v in catalog.load_profiles().items()},
                "options": {"tunnels": settings.TUNNELS, "access": settings.ACCESS, "nics": settings.NICS,
                            "gpu_modes": settings.GPU_MODES}}

    def api_about(self, body, q):
        return {"version": __version__, "license": __license__, "source": __source_url__}

    def api_settings(self, body, q):
        cfg = settings.load_config_file()
        eff = settings.resolve(environ={}, config=cfg)
        return {"values": {k: getattr(eff, k) for k in settings.EDITABLE}, "custom": [k for k in cfg if k in settings.EDITABLE],
                "options": {"tunnel": list(settings.TUNNELS), "accel": list(settings.ACCEL), "firmware": list(settings.FIRMWARE),
                            "nic": list(settings.NICS), "audio": list(settings.AUDIO), "access": list(settings.ACCESS)}}

    def api_settings_save(self, body, q):
        settings.save_config_file({k: v for k, v in body.items() if k in settings.EDITABLE or k in body})
        return self.api_settings({}, {})
    api_settings_save.post = True

    def api_presets(self, body, q):
        return {"presets": presets.load()}

    def api_presets_save(self, body, q):
        name = str(body.get("name") or "")
        values = {k: v for k, v in (body.get("values") or {}).items() if k in RUN_KEYS}
        return {"saved": presets.save(name, values)}
    api_presets_save.post = True

    def api_presets_delete(self, body, q):
        presets.remove(str(body.get("name") or ""))
        return {"ok": True}
    api_presets_delete.post = True

    def api_job(self, body, q):
        job = self.state.jobs.get(q.get("id", ""))
        if not job:
            raise VortaxError("unknown job")
        return job.to_dict()

    def api_logs(self, body, q):
        inst = session.Instance(q.get("name", "vortax"))
        which = q.get("source", "qemu")
        path = {"qemu": inst.log_file, "tunnel": inst.run / "tunnel.log"}.get(which)
        if path is None:
            raise VortaxError("source must be qemu or tunnel")
        try:
            lines = max(1, min(500, int(q.get("lines", 80))))
        except ValueError:
            lines = 80
        return {"text": session.tail(path, lines)}

    def api_password(self, body, q):
        inst = session.Instance(q.get("name", "vortax"))
        return {"password": inst.vnc_password(), "access_password": _read(inst.access_pass_file)}

    def api_storage(self, body, q):
        out = _storage()
        out["stats"] = self.state.sampler.snapshot()["disk"]
        return out

    def api_doctor(self, body, q):
        return {"checks": [c.to_dict() for c in doctor.run_checks()]}

    # POST endpoints
    def api_run(self, body, q):
        job = self.state.new_job("run", str(body.get("name") or "vortax"), _job_run(body))
        return {"job": job.id}
    api_run.post = True

    def api_vm(self, body, q):
        inst = session.Instance(str(body.get("name") or "vortax"))
        action = body.get("action")
        if action == "stop":
            job = self.state.new_job("stop", inst.name, lambda j: inst.stop(graceful=True, timeout=45, log=j.log))
            return {"job": job.id}
        if action == "kill":
            inst.kill()
        elif action == "reboot":
            inst.reboot()
        elif action == "start":
            return {"job": self.state.new_job("run", inst.name, _job_run({"name": inst.name})).id}
        else:
            raise VortaxError("action must be start, stop, kill or reboot")
        return {"ok": True}
    api_vm.post = True

    def api_vm_edit(self, body, q):
        """Read (no `changes`) or change the saved launch settings of an existing VM. The disk is never touched."""
        inst = session.Instance(str(body.get("name") or "vortax"))
        changes = body.get("changes") or {}
        if not isinstance(changes, dict):
            raise VortaxError("changes must be an object")
        values = session.edit_settings(inst, changes)
        return {"values": values, "running": inst.running(), "saved": bool(changes),
                "options": {"accel": list(settings.ACCEL), "nic": list(settings.NICS), "audio": list(settings.AUDIO)}}
    api_vm_edit.post = True

    def api_vm_live(self, body, q):
        """Change a RUNNING VM through the monitor: swap a CD, plug in or pull out a sound card."""
        inst = session.Instance(str(body.get("name") or "vortax"))
        return {"message": live.apply(inst, str(body.get("action", "")), str(body.get("value", "")))}
    api_vm_live.post = True

    def api_password_set(self, body, q):
        inst = session.Instance(str(body.get("name") or "vortax"))
        pw = session.new_password() if body.get("generate") else str(body.get("password") or "")
        return {"password": inst.set_vnc_password(pw)}
    api_password_set.post = True

    def api_tunnel(self, body, q):
        inst = session.Instance(str(body.get("name") or "vortax"))
        kind = body.get("kind", "stop")
        if kind == "stop":
            tunnels.stop(inst.state().get("tunnel"))
            inst.update_state(tunnel={})
            return {}
        if kind not in settings.TUNNELS or kind == "none":
            raise VortaxError("kind must be bore, ngrok, tailscale or stop")
        job = self.state.new_job("tunnel", inst.name, lambda j: (j.log(f"opening {kind} tunnel"), session.start_tunnel(inst, kind)))
        return {"job": job.id}
    api_tunnel.post = True

    def api_snapshot(self, body, q):
        inst = session.Instance(str(body.get("name") or "vortax"))
        return {"text": inst.snapshot(str(body.get("action", "list")), str(body.get("tag", "")))}
    api_snapshot.post = True

    def api_storage_delete(self, body, q):
        _delete_storage(str(body.get("kind", "")), str(body.get("name", "")))
        return {"ok": True}
    api_storage_delete.post = True

    def api_webvnc(self, body, q):
        inst = session.Instance(str(body.get("name") or "vortax"))
        return _webvnc(self.state, inst, str(body.get("action", "start")))
    api_webvnc.post = True

    # --- trusted devices
    def _current_device(self) -> str | None:
        return self.state.trust.device_id(self.headers.get("X-Vortax-Token", ""))

    def api_devices(self, body, q):
        t = self.state.trust
        return {"devices": t.devices(), "current": self._current_device(), "max_devices": t.max_devices,
                "password": t.has_password}

    def api_devices_invite(self, body, q):
        return {"code": self.state.trust.new_claim()}
    api_devices_invite.post = True

    def api_devices_remove(self, body, q):
        self.state.trust.remove(str(body.get("id", "")))
        return {"ok": True}
    api_devices_remove.post = True

    def api_devices_limit(self, body, q):
        self.state.trust.set_max(body.get("max_devices"))
        return {"ok": True}
    api_devices_limit.post = True

    def api_devices_password(self, body, q):
        if body.get("clear"):
            self.state.trust.clear_password()
        else:
            self.state.trust.set_password(str(body.get("password", "")))
        return {"ok": True}
    api_devices_password.post = True

    def api_secret(self, body, q):
        settings.set_secret(str(body.get("name", "")), str(body.get("value", "")))
        return {"ok": True}
    api_secret.post = True


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- entry point


def load_token(new: bool = False) -> str:
    path = paths.data_dir() / "dashboard.token"
    if not new:
        try:
            token = path.read_text(encoding="utf-8").strip()
            if len(token) >= 20:
                return token
        except OSError:
            pass
    token = secrets.token_urlsafe(24)
    paths.write_private(path, token)
    return token


def make_server(host: str, port: int, token: str, trust: Trust | None = None) -> ThreadingHTTPServer:
    state = State(token, host, port, trust)

    class Bound(Handler):
        pass

    Bound.state = state
    server = ThreadingHTTPServer((host, port), Bound)
    server.daemon_threads = True
    return server


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False, new_token: bool = False,
          trust: Trust | None = None) -> int:
    token = load_token(new_token)
    trust = trust or Trust()
    try:
        server = make_server(host, port, token, trust)
    except OSError as exc:
        raise VortaxError(f"cannot listen on {host}:{port}: {exc}") from None
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    base = f"http://{shown}:{port}/"
    url = base
    print(f"vortax dashboard {__version__} listening on {host}:{port}")
    if trust.has_room():
        url = f"{base}#claim={trust.new_claim()}"
        print(f"one-time link (the first browser that opens it becomes trusted; {trust.count()} of {trust.max_devices} trusted):")
        print(f"open: {url}")
    else:
        print(f"{trust.count()} trusted device(s), limit {trust.max_devices}. Open {base} from a trusted browser.")
        print("another phone can join only if you raise the limit (--max-devices N) or set a password (--set-password).")
    if trust.has_password:
        print(f"password sign-in is on: any browser that knows the password can sign in at {base}")
    if host not in LOOPBACK:
        print("warning: this is plain HTTP on a non-loopback address; anyone on the network who sees the traffic "
              "can steal a session. Prefer an SSH tunnel or Tailscale.", file=sys.stderr)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
