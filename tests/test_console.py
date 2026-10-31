# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
import base64
import os
import socket
import struct
import threading
import time
import unittest

from vortax import session, vncauth
from vortax.dashboard import server, ws
from tests.fake_rfb import FakeRFB, H, W, framebuffer
from tests.helpers import Sandbox

TOKEN = "t" * 32


def ws_connect(port, path, token=TOKEN, origin=None, host=None):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    req = [f"GET {path} HTTP/1.1", f"Host: {host or '127.0.0.1:%d' % port}", "Upgrade: websocket", "Connection: Upgrade",
           f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
    if token is not None:
        req.append(f"Sec-WebSocket-Protocol: vortax.v1, tok.{token}")
    if origin:
        req.append(f"Origin: {origin}")
    s.sendall(("\r\n".join(req) + "\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        c = s.recv(1)
        if not c:
            break
        head += c
    return s, head.decode(), key


def send_masked(s, payload: bytes, opcode=2):
    mask = os.urandom(4)
    n = len(payload)
    hdr = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else b"\xfe" + struct.pack(">H", n))
    s.sendall(hdr + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(payload)))


class Reader:
    def __init__(self, sock):
        self.ws = ws.WebSocket.__new__(ws.WebSocket)
        self.sock = sock
        self.buf = b""

    def read(self, n):
        while len(self.buf) < n:
            b1, b2 = self._exact(2)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._exact(8))[0]
            self.buf += self._exact(length)
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _exact(self, n):
        out = b""
        while len(out) < n:
            c = self.sock.recv(n - len(out))
            if not c:
                raise EOFError
            out += c
        return out


class DES(unittest.TestCase):
    def test_matches_reference_and_known_vector(self):
        self.assertEqual(vncauth.des_encrypt_block(bytes.fromhex("133457799BBCDFF1"), bytes.fromhex("0123456789ABCDEF")).hex(),
                         "85e813540f0ab405")
        try:
            from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
            from cryptography.hazmat.primitives.ciphers import Cipher, modes
        except ImportError:
            self.skipTest("cryptography not installed")
        for _ in range(50):
            k, b = os.urandom(8), os.urandom(8)
            self.assertEqual(vncauth.des_encrypt_block(k, b), Cipher(TripleDES(k * 3), modes.ECB()).encryptor().update(b))

    def test_respond_truncates_to_eight_chars(self):
        c = os.urandom(16)
        self.assertEqual(vncauth.respond("12345678", c), vncauth.respond("12345678zzzz", c))
        self.assertNotEqual(vncauth.respond("12345678", c), vncauth.respond("1234567x", c))


class Console(Sandbox):
    def setUp(self):
        super().setUp()
        self.vnc = FakeRFB("pw123456")
        self.addCleanup(self.vnc.close)
        inst = session.Instance("c1")
        from vortax import paths
        paths.ensure_private_dir(inst.run)
        paths.write_private(inst.vnc_pass_file, "pw123456")
        inst.save_state({"name": "c1", "pid": os.getpid(), "vnc": {"bind": "127.0.0.1", "port": self.vnc.port, "password": True}})
        self.srv = server.make_server("127.0.0.1", 0, TOKEN)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.srv.server_close)

    def test_proxy_authenticates_server_side_and_relays(self):
        # The pid is ours, so session.pid() must accept it as running: patch the qemu check.
        from unittest import mock
        with mock.patch.object(session, "_is_qemu", return_value=True):
            s, head, key = ws_connect(self.port, "/ws/vnc?name=c1", origin=f"http://127.0.0.1:{self.port}")
            self.assertIn("101", head.splitlines()[0], head)
            self.assertIn(ws.accept_key(key), head)
            self.assertIn("Sec-WebSocket-Protocol: vortax.v1", head)
            r = Reader(s)
            self.assertEqual(r.read(12), b"RFB 003.008\n")
            send_masked(s, b"RFB 003.008\n")
            self.assertEqual(r.read(2), bytes([1, 1]))                 # only "None" is offered to the browser
            send_masked(s, bytes([1]))
            self.assertEqual(r.read(4), b"\0\0\0\0")
            send_masked(s, bytes([1]))                                 # ClientInit
            init = r.read(24 + 7)
            self.assertEqual(struct.unpack(">HH", init[:4]), (W, H))
            self.assertEqual(init[-7:], b"fake-vm")
            self.assertTrue(self.vnc.auth_ok)                          # the proxy answered QEMU's challenge
            send_masked(s, bytes([3, 0]) + struct.pack(">HHHH", 0, 0, W, H))
            hdr = r.read(4 + 12)
            self.assertEqual(hdr[0], 0)
            self.assertEqual(r.read(W * H * 4), framebuffer())
            send_masked(s, bytes([5, 1]) + struct.pack(">HH", 10, 20))
            send_masked(s, bytes([4, 1, 0, 0]) + struct.pack(">I", 0xff0d))
            for _ in range(50):
                if len(self.vnc.events) >= 2:
                    break
                time.sleep(0.05)
            self.assertEqual(self.vnc.events, [("pointer", 10, 20, 1), ("key", 1, 0xff0d)])
            s.close()

    def test_rejects_missing_or_wrong_token(self):
        for token in (None, "wrong"):
            sock, head, _ = ws_connect(self.port, "/ws/vnc?name=c1", token=token)
            sock.close()
            self.assertIn("401", head.splitlines()[0])

    def test_rejects_cross_origin(self):
        sock, head, _ = ws_connect(self.port, "/ws/vnc?name=c1", origin="http://evil.example")
        sock.close()
        self.assertIn("403", head.splitlines()[0])

    def test_rejects_bad_host_header(self):
        sock, head, _ = ws_connect(self.port, "/ws/vnc?name=c1", host="evil.example")
        sock.close()
        self.assertIn("403", head.splitlines()[0])

    def test_not_running_is_409(self):
        sock, head, _ = ws_connect(self.port, "/ws/vnc?name=nope")
        sock.close()
        self.assertIn("409", head.splitlines()[0])

    def test_wrong_stored_password_closes_cleanly(self):
        from unittest import mock
        from vortax import paths
        paths.write_private(session.Instance("c1").vnc_pass_file, "different")
        with mock.patch.object(session, "_is_qemu", return_value=True):
            s, head, _ = ws_connect(self.port, "/ws/vnc?name=c1")
            self.assertIn("101", head.splitlines()[0])
            s.settimeout(5)
            data = b""
            try:
                while True:
                    c = s.recv(4096)
                    if not c:
                        break
                    data += c
            except socket.timeout:
                self.fail("connection should close after failed VNC auth")
            finally:
                s.close()
        self.assertFalse(self.vnc.auth_ok)


if __name__ == "__main__":
    unittest.main()


class SettingsApi(Sandbox):
    def setUp(self):
        super().setUp()
        self.srv = server.make_server("127.0.0.1", 0, TOKEN)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.srv.server_close)

    def call(self, method, path, body=None):
        import http.client
        import json
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {"X-Vortax-Token": TOKEN, "Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def test_about_names_license_and_source(self):
        st, d = self.call("GET", "/api/about")
        self.assertEqual((st, d["license"]), (200, "AGPL-3.0-only"))
        self.assertTrue(d["source"].startswith("https://"))

    def test_settings_roundtrip_and_validation(self):
        st, d = self.call("GET", "/api/settings")
        self.assertEqual(st, 200)
        self.assertIn("audio", d["values"])
        st, d = self.call("POST", "/api/settings/save", {"ram_mb": 777, "audio": "ac97"})
        self.assertEqual((st, d["values"]["ram_mb"], d["values"]["audio"]), (200, 777, "ac97"))
        self.assertEqual(sorted(d["custom"]), ["audio", "ram_mb"])
        self.assertEqual(self.call("POST", "/api/settings/save", {"audio": "bogus"})[0], 400)
        self.assertEqual(self.call("POST", "/api/settings/save", {"password": "x"})[0], 400)   # secrets are not editable here
        st, d = self.call("POST", "/api/settings/save", {"ram_mb": "", "audio": ""})
        self.assertEqual(d["custom"], [])

    def test_presets_api(self):
        self.assertEqual(self.call("POST", "/api/presets/save", {"name": "p1", "values": {"ram_mb": 512, "password": "x", "bogus": 1}})[0], 200)
        st, d = self.call("GET", "/api/presets")
        self.assertEqual(d["presets"], {"p1": {"ram_mb": 512}})
        self.assertEqual(self.call("POST", "/api/presets/delete", {"name": "p1"})[0], 200)
        self.assertEqual(self.call("POST", "/api/presets/delete", {"name": "p1"})[0], 400)
