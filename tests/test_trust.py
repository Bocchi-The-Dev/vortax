# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""One-way trusted-device sign-in for the dashboard."""
import contextlib
import http.client
import io
import json
import stat
import threading

from vortax import cli
from vortax.dashboard import server
from vortax.dashboard.trust import Trust
from vortax.errors import VortaxError
from tests.helpers import Sandbox

TOKEN = "t" * 32


class TrustUnit(Sandbox):
    def setUp(self):
        super().setUp()
        self.t = Trust(self.tmp / "auth.json")

    def test_link_works_once_and_second_phone_is_refused(self):
        code = self.t.new_claim()
        key = self.t.claim(code, "Phone A", "1.1.1.1")
        self.assertTrue(self.t.verify(key))
        with self.assertRaises(VortaxError):
            self.t.claim(code, "Phone B", "2.2.2.2")                 # same link, burned
        with self.assertRaises(VortaxError):
            self.t.new_claim()                                         # limit 1 reached: no second invite

    def test_limit_allows_more_devices_one_link_each(self):
        self.t.set_max(2)
        a = self.t.claim(self.t.new_claim(), "A", "x")
        b = self.t.claim(self.t.new_claim(), "B", "y")
        self.assertTrue(self.t.verify(a) and self.t.verify(b))
        with self.assertRaises(VortaxError):
            self.t.new_claim()

    def test_unknown_key_and_wrong_code_fail(self):
        self.assertFalse(self.t.verify("nope"))
        self.assertFalse(self.t.verify(""))
        with self.assertRaises(VortaxError):
            self.t.claim("guess", "x", "9.9.9.9")

    def test_password_signs_in_any_device_and_throttles(self):
        with self.assertRaises(VortaxError):
            self.t.login("whatever", "x", "a")                         # not enabled
        with self.assertRaises(VortaxError):
            self.t.set_password("short")
        self.t.set_password("correct horse")
        key = self.t.login("correct horse", "Other phone", "a")
        self.assertTrue(self.t.verify(key))
        for _ in range(5):
            with self.assertRaises(VortaxError):
                self.t.login("bad", "x", "attacker")
        with self.assertRaises(VortaxError) as ctx:
            self.t.login("correct horse", "x", "attacker")             # even the right one waits
        self.assertIn("wait", str(ctx.exception))
        self.assertTrue(self.t.verify(self.t.login("correct horse", "x", "someone else")))   # other addresses unaffected
        self.t.clear_password()
        with self.assertRaises(VortaxError):
            self.t.login("correct horse", "x", "a")

    def test_remove_persist_and_private_file(self):
        key = self.t.claim(self.t.new_claim(), "Phone <script>", "x")
        dev = self.t.devices()[0]
        self.assertNotIn("<", dev["label"])
        again = Trust(self.tmp / "auth.json")                          # survives a restart
        self.assertTrue(again.verify(key))
        self.assertNotIn(key, (self.tmp / "auth.json").read_text())   # only a hash is stored
        self.assertEqual(stat.S_IMODE((self.tmp / "auth.json").stat().st_mode), 0o600)
        again.remove(dev["id"])
        self.assertFalse(again.verify(key))
        with self.assertRaises(VortaxError):
            again.remove("ffffffff")
        with self.assertRaises(VortaxError):
            again.set_max(0)


class TrustServer(Sandbox):
    def setUp(self):
        super().setUp()
        self.trust = Trust(self.tmp / "auth.json")
        self.srv = server.make_server("127.0.0.1", 0, TOKEN, self.trust)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.srv.server_close)

    def call(self, method, path, body=None, key=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"X-Vortax-Token": key} if key else {}
        data = None
        if body is not None or method == "POST":
            headers["Content-Type"] = "application/json"
            data = json.dumps(body or {})
        conn.request(method, path, data, headers)
        r = conn.getresponse()
        out = json.loads(r.read() or b"{}")
        conn.close()
        return r.status, out

    def test_first_phone_gets_in_second_is_refused(self):
        code = self.trust.new_claim()
        st, body = self.call("POST", "/api/auth/claim", {"code": code, "label": "Pixel"})
        self.assertEqual(st, 200)
        key = body["key"]
        self.assertEqual(self.call("GET", "/api/about", key=key)[0], 200)             # trusted phone works
        self.assertEqual(self.call("GET", "/api/about")[0], 401)                      # a phone without a key does not
        self.assertEqual(self.call("GET", "/api/about", key="x" * 40)[0], 401)
        st, body = self.call("POST", "/api/auth/claim", {"code": code, "label": "Other"})
        self.assertEqual(st, 400)                                                     # the same link cannot be reused
        self.assertIn("already used", body["error"])
        self.assertEqual(self.call("GET", "/api/auth/info")[1], {"password": False, "claimable": False})

    def test_master_token_still_works_for_scripts(self):
        self.assertEqual(self.call("GET", "/api/about", key=TOKEN)[0], 200)

    def test_owner_raises_limit_and_invites_from_trusted_device(self):
        key = self.call("POST", "/api/auth/claim", {"code": self.trust.new_claim(), "label": "A"})[1]["key"]
        self.assertEqual(self.call("POST", "/api/devices/invite", key=key)[0], 400)   # limit 1
        self.assertEqual(self.call("POST", "/api/devices/limit", {"max_devices": 2}, key=key)[0], 200)
        code = self.call("POST", "/api/devices/invite", key=key)[1]["code"]
        self.assertEqual(self.call("POST", "/api/auth/claim", {"code": code, "label": "B"})[0], 200)
        st, body = self.call("GET", "/api/devices", key=key)
        self.assertEqual(len(body["devices"]), 2)
        self.assertEqual(body["current"], body["devices"][0]["id"])

    def test_password_login_and_revoke(self):
        key = self.call("POST", "/api/auth/claim", {"code": self.trust.new_claim(), "label": "A"})[1]["key"]
        self.call("POST", "/api/devices/password", {"password": "s3cret-pass"}, key=key)
        self.assertTrue(self.call("GET", "/api/auth/info")[1]["password"])
        st, body = self.call("POST", "/api/auth/login", {"password": "wrong", "label": "B"})
        self.assertEqual(st, 400)
        st, body = self.call("POST", "/api/auth/login", {"password": "s3cret-pass", "label": "B"})
        self.assertEqual(st, 200)
        key_b = body["key"]
        self.assertEqual(self.call("GET", "/api/about", key=key_b)[0], 200)
        dev_b = [d for d in self.call("GET", "/api/devices", key=key)[1]["devices"] if d["label"] == "B"][0]
        self.call("POST", "/api/devices/remove", {"id": dev_b["id"]}, key=key)
        self.assertEqual(self.call("GET", "/api/about", key=key_b)[0], 401)           # revoked at once

    def test_auth_endpoints_need_json_and_unknown_is_404(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/auth/login", "password=x", {"Content-Type": "text/plain"})
        self.assertEqual(conn.getresponse().status, 415)
        conn.close()
        self.assertEqual(self.call("GET", "/api/auth/other")[0], 404)

    def test_cli_options(self):
        from vortax.dashboard import trust as trust_mod
        key_dir = self.tmp
        buf = io.StringIO()
        ns = cli.build_parser().parse_args(["dashboard", "--devices"])
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.cmd_dashboard(ns), 0)
        self.assertIn("0 trusted device(s), limit 1", buf.getvalue())
        ns = cli.build_parser().parse_args(["dashboard", "--max-devices", "3", "--devices"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.cmd_dashboard(ns)
        self.assertIn("limit 3", buf.getvalue())
        self.assertTrue(key_dir and trust_mod)
