# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Minimal RFC 6455 WebSocket server side, plus the VNC console proxy built on it."""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
import threading

from .. import vncauth
from ..errors import VortaxError

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 1 << 20


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode() + GUID).digest()).decode()


class WSClosed(Exception):
    pass


class WebSocket:
    """Frames over a connected socket. Incoming data messages are exposed as one byte stream."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._buf = b""
        self._lock = threading.Lock()
        self.closed = False

    def _read_exact(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise WSClosed()
            out += chunk
        return out

    def send_frame(self, opcode: int, payload: bytes) -> None:
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([n])
        elif n < 65536:
            head += b"\x7e" + struct.pack(">H", n)
        else:
            head += b"\x7f" + struct.pack(">Q", n)
        with self._lock:
            try:
                self.sock.sendall(head + payload)
            except OSError:
                raise WSClosed() from None

    def send(self, data: bytes) -> None:
        self.send_frame(0x2, data)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.send_frame(0x8, b"\x03\xe8")
            except WSClosed:
                pass

    def _next_message(self) -> bytes:
        message = b""
        while True:
            b1, b2 = self._read_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            masked, length = b2 & 0x80, b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            if length > MAX_FRAME:
                raise WSClosed()
            if not masked:                    # clients must mask (RFC 6455 section 5.1)
                raise WSClosed()
            mask = self._read_exact(4)
            payload = bytearray(self._read_exact(length))
            for i in range(length):
                payload[i] ^= mask[i & 3]
            if opcode == 0x8:
                raise WSClosed()
            if opcode == 0x9:
                self.send_frame(0xA, bytes(payload))
                continue
            if opcode == 0xA:
                continue
            message += bytes(payload)
            if fin:
                return message

    def read(self, n: int) -> bytes:
        while len(self._buf) < n:
            self._buf += self._next_message()
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def read_some(self) -> bytes:
        if not self._buf:
            self._buf = self._next_message()
        out, self._buf = self._buf, b""
        return out


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise VortaxError("the VM closed the VNC connection during the handshake")
        out += chunk
    return out


def vnc_handshake(sock: socket.socket, password: str) -> None:
    """Complete RFB version + security negotiation with QEMU as the client."""
    banner = _recv_exact(sock, 12)
    if not banner.startswith(b"RFB "):
        raise VortaxError("that port is not a VNC server")
    sock.sendall(b"RFB 003.008\n")
    count = _recv_exact(sock, 1)[0]
    if count == 0:
        raise VortaxError("the VNC server refused the connection")
    types = list(_recv_exact(sock, count))
    if 2 in types and password:
        sock.sendall(bytes([2]))
        sock.sendall(vncauth.respond(password, _recv_exact(sock, 16)))
    elif 1 in types:
        sock.sendall(bytes([1]))
    else:
        raise VortaxError("the VM needs a VNC password but none is stored; run `vortax password --new`")
    if _recv_exact(sock, 4) != b"\x00\x00\x00\x00":
        raise VortaxError("VNC authentication failed (stored password does not match the running VM)")


def serve_console(ws: WebSocket, port: int, password: str, host: str = "127.0.0.1") -> None:
    """Authenticate to QEMU ourselves, present the browser with a no-auth RFB server, then relay."""
    upstream = socket.create_connection((host, port), timeout=10)
    try:
        upstream.settimeout(30)
        vnc_handshake(upstream, password)
        upstream.settimeout(None)
        ws.send(b"RFB 003.008\n")
        ws.read(12)
        ws.send(bytes([1, 1]))            # one security type: None (we already authenticated)
        ws.read(1)
        ws.send(b"\x00\x00\x00\x00")      # SecurityResult: OK

        def down() -> None:
            try:
                while True:
                    data = upstream.recv(65536)
                    if not data:
                        break
                    ws.send(data)
            except (OSError, WSClosed):
                pass
            finally:
                ws.close()
                try:
                    ws.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        t = threading.Thread(target=down, daemon=True)
        t.start()
        try:
            while True:
                upstream.sendall(ws.read_some())
        except (OSError, WSClosed):
            pass
    finally:
        try:
            upstream.close()
        except OSError:
            pass
