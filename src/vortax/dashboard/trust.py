# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Trusted devices for the dashboard.

The link that `vortax dashboard` prints carries a one-time claim code, not a permanent secret. The first
browser that opens it is exchanged for a private device key (kept in that browser) and becomes trusted;
the code is then burned, so a second phone with the same link is refused. Other phones can only get in if
the owner raises the device limit (and shares a fresh one-time link) or sets a password.

Only a hash of every device key is stored. Wrong guesses are throttled per address.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path

from .. import paths
from ..errors import VortaxError

CLAIM_TTL = 3600           # seconds a one-time link stays valid
MAX_DEVICES_HARD = 50
PBKDF2_ROUNDS = 200_000
FAIL_LIMIT, FAIL_WINDOW = 5, 60


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _label(raw) -> str:
    text = re.sub(r"[^\w .,()+/-]", "", str(raw or ""))[:40].strip()
    return text or "unknown device"


def _hash_password(password: str, salt: bytes, rounds: int = PBKDF2_ROUNDS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds).hex()


class Trust:
    def __init__(self, path: Path | None = None):
        self.path = path or (paths.data_dir() / "dashboard.auth.json")
        self.lock = threading.RLock()
        self._claims: dict[str, float] = {}          # sha256(code) -> expiry
        self._fails: dict[str, list[float]] = {}
        self._seen_write = 0.0
        self.data = self._load()

    # --- storage
    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("max_devices", 1)
        data.setdefault("password", None)
        data["devices"] = [d for d in data.get("devices", []) if isinstance(d, dict) and d.get("key_hash")]
        return data

    def _save(self) -> None:
        paths.write_private(self.path, json.dumps(self.data, indent=1))

    # --- throttle
    def _blocked(self, who: str) -> bool:
        now = time.time()
        recent = [t for t in self._fails.get(who, []) if now - t < FAIL_WINDOW]
        self._fails[who] = recent
        return len(recent) >= FAIL_LIMIT

    def _fail(self, who: str) -> None:
        self._fails.setdefault(who, []).append(time.time())

    # --- queries
    @property
    def max_devices(self) -> int:
        return int(self.data["max_devices"])

    @property
    def has_password(self) -> bool:
        return bool(self.data["password"])

    def count(self) -> int:
        with self.lock:
            return len(self.data["devices"])

    def has_room(self) -> bool:
        return self.count() < self.max_devices

    def info(self) -> dict:
        with self.lock:
            return {"password": self.has_password, "claimable": self.has_room()}

    def devices(self) -> list[dict]:
        with self.lock:
            return [{"id": d["id"], "label": d["label"], "added": d["added"], "last_seen": d.get("last_seen", d["added"]),
                     "how": d.get("how", "link")} for d in self.data["devices"]]

    # --- one-time links
    def new_claim(self) -> str:
        """A fresh one-time code. Refused when the device limit is reached and no password is set."""
        with self.lock:
            if not self.has_room():
                raise VortaxError(f"device limit reached ({self.max_devices}); raise it or remove a device first")
            now = time.time()
            self._claims = {h: t for h, t in self._claims.items() if t > now}
            code = secrets.token_urlsafe(12)
            self._claims[_sha(code)] = now + CLAIM_TTL
            return code

    def claim(self, code: str, label: str, who: str) -> str:
        with self.lock:
            if self._blocked(who):
                raise VortaxError("too many wrong attempts, wait a minute")
            digest = _sha(str(code or ""))
            expiry = self._claims.get(digest)
            if not expiry or expiry < time.time():
                self._fail(who)
                raise VortaxError("this link was already used or has expired")
            if not self.has_room():
                raise VortaxError(f"device limit reached ({self.max_devices})")
            del self._claims[digest]                      # one way: the code can never be used again
            return self._add(label, "link")

    # --- password
    def set_password(self, password: str) -> None:
        if len(password or "") < 6:
            raise VortaxError("password must be at least 6 characters")
        with self.lock:
            salt = secrets.token_bytes(16)
            self.data["password"] = {"salt": salt.hex(), "rounds": PBKDF2_ROUNDS, "hash": _hash_password(password, salt)}
            self._save()

    def clear_password(self) -> None:
        with self.lock:
            self.data["password"] = None
            self._save()

    def login(self, password: str, label: str, who: str) -> str:
        with self.lock:
            rec = self.data["password"]
            if not rec:
                raise VortaxError("password login is not enabled on this dashboard")
            if self._blocked(who):
                raise VortaxError("too many wrong attempts, wait a minute")
            salt = bytes.fromhex(rec["salt"])
            ok = hmac.compare_digest(_hash_password(str(password or ""), salt, int(rec["rounds"])), rec["hash"])
            if not ok:
                self._fail(who)
                raise VortaxError("wrong password")
            return self._add(label, "password")

    # --- devices
    def _add(self, label: str, how: str) -> str:
        if len(self.data["devices"]) >= MAX_DEVICES_HARD:
            raise VortaxError("too many devices; remove some first")
        key = secrets.token_urlsafe(32)
        now = int(time.time())
        self.data["devices"].append({"id": secrets.token_hex(4), "key_hash": _sha(key), "label": _label(label),
                                     "added": now, "last_seen": now, "how": how})
        self._save()
        return key

    def device_id(self, key: str) -> str | None:
        digest = _sha(key or "")
        with self.lock:
            for d in self.data["devices"]:
                if hmac.compare_digest(d["key_hash"], digest):
                    return d["id"]
        return None

    def verify(self, key: str) -> bool:
        if not key:
            return False
        digest = _sha(key)
        with self.lock:
            for d in self.data["devices"]:
                if hmac.compare_digest(d["key_hash"], digest):
                    now = int(time.time())
                    if now - d.get("last_seen", 0) > 300 and now - self._seen_write > 60:   # do not hit the disk on every poll
                        d["last_seen"], self._seen_write = now, now
                        self._save()
                    return True
        return False

    def remove(self, device_id: str) -> None:
        with self.lock:
            before = len(self.data["devices"])
            self.data["devices"] = [d for d in self.data["devices"] if d["id"] != device_id]
            if len(self.data["devices"]) == before:
                raise VortaxError("no such device")
            self._save()

    def reset(self) -> None:
        with self.lock:
            self.data["devices"] = []
            self._claims.clear()
            self._save()

    def set_max(self, n: int) -> None:
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_DEVICES_HARD:
            raise VortaxError(f"device limit must be between 1 and {MAX_DEVICES_HARD}")
        with self.lock:
            self.data["max_devices"] = n
            self._save()
