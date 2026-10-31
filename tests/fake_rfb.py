# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""A tiny RFB 3.8 server with VNC authentication and a recognisable framebuffer, for tests."""
import os
import socket
import struct
import threading

from vortax import vncauth

W, H = 64, 48
NAME = b"fake-vm"


def pixel(x, y):
    """Left half red, right half blue, with a white 8x8 square in the top-left corner."""
    if x < 8 and y < 8:
        return (255, 255, 255)
    return (255, 0, 0) if x < W // 2 else (0, 0, 255)


def framebuffer() -> bytes:
    return b"".join(bytes(pixel(x, y)) + b"\x00" for y in range(H) for x in range(W))


class FakeRFB:
    def __init__(self, password: str = "pw123456"):
        self.password = password
        self.events = []            # ("pointer", x, y, buttons) / ("key", down, keysym)
        self.auth_ok = None
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self.sock.close()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _exact(conn, n):
        out = b""
        while len(out) < n:
            c = conn.recv(n - len(out))
            if not c:
                raise EOFError
            out += c
        return out

    def _serve(self, conn):
        try:
            conn.sendall(b"RFB 003.008\n")
            self._exact(conn, 12)
            conn.sendall(bytes([1, 2]))
            self._exact(conn, 1)
            challenge = os.urandom(16)
            conn.sendall(challenge)
            reply = self._exact(conn, 16)
            self.auth_ok = reply == vncauth.respond(self.password, challenge)
            conn.sendall(struct.pack(">I", 0 if self.auth_ok else 1))
            if not self.auth_ok:
                return
            self._exact(conn, 1)                                            # ClientInit
            pf = bytes([32, 24, 0, 1]) + struct.pack(">HHH", 255, 255, 255) + bytes([0, 8, 16, 0, 0, 0])
            conn.sendall(struct.pack(">HH", W, H) + pf + struct.pack(">I", len(NAME)) + NAME)
            while True:
                t = self._exact(conn, 1)[0]
                if t == 0:
                    self._exact(conn, 19)
                elif t == 2:
                    n = struct.unpack(">H", self._exact(conn, 3)[1:])[0]
                    self._exact(conn, 4 * n)
                elif t == 3:
                    self._exact(conn, 9)
                    conn.sendall(struct.pack(">BxH", 0, 1) + struct.pack(">HHHHi", 0, 0, W, H, 0) + framebuffer())
                elif t == 4:
                    d = self._exact(conn, 7)
                    self.events.append(("key", d[0], struct.unpack(">I", d[3:])[0]))
                elif t == 5:
                    d = self._exact(conn, 5)
                    self.events.append(("pointer", struct.unpack(">H", d[1:3])[0], struct.unpack(">H", d[3:5])[0], d[0]))
        except (EOFError, OSError):
            pass
        finally:
            conn.close()
