# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
import contextlib
import http.client
import io
import json
import threading
import time
import unittest

from vortax import cli, session
from vortax.dashboard import server
from tests.helpers import Sandbox

QCOW = b"QFI\xfb" + b"\0" * 600
TOKEN = "t" * 32


class Dashboard(Sandbox):
    def setUp(self):
        super().setUp()
        self.srv = server.make_server("127.0.0.1", 0, TOKEN)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.srv.server_close)

    def call(self, method, path, body=None, token=TOKEN, host=None, ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {}
        if token is not None:
            headers["X-Vortax-Token"] = token
        if host:
            headers["Host"] = host
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = ctype
        elif method == "POST":
            headers["Content-Type"] = ctype
        conn.request(method, path, data, headers)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        try:
            return r.status, json.loads(raw), r.headers
        except ValueError:
            return r.status, raw, r.headers

    def test_static_page_and_security_headers(self):
        status, body, h = self.call("GET", "/", token=None)
        self.assertEqual(status, 200)
        self.assertIn(b"Vortax", body)
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.call("GET", "/app.js", token=None)[0], 200)

    def test_static_cannot_escape(self):
        for p in ("/../server.py", "/%2e%2e/server.py", "/static/../server.py", "/.hidden", "/app.py"):
            self.assertEqual(self.call("GET", p, token=None)[0], 404, p)

    def test_api_requires_the_token(self):
        self.assertEqual(self.call("GET", "/api/overview", token=None)[0], 401)
        self.assertEqual(self.call("GET", "/api/overview", token="wrong")[0], 401)
        self.assertEqual(self.call("GET", "/api/overview")[0], 200)

    def test_dns_rebinding_host_is_refused(self):
        self.assertEqual(self.call("GET", "/api/overview", host="evil.example")[0], 403)
        self.assertEqual(self.call("GET", "/", host="evil.example", token=None)[0], 403)
        self.assertEqual(self.call("GET", "/api/overview", host="localhost:8765")[0], 200)

    def test_post_needs_json_and_right_method(self):
        self.assertEqual(self.call("POST", "/api/run", {"x": 1}, ctype="text/plain")[0], 415)
        self.assertEqual(self.call("GET", "/api/run")[0], 405)
        self.assertEqual(self.call("POST", "/api/overview", {})[0], 405)
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)

    def test_catalog_endpoint(self):
        status, body, _ = self.call("GET", "/api/systems")
        self.assertEqual(status, 200)
        ids = {s["id"] for s in body["systems"]}
        self.assertIn("ubuntu", ids)
        self.assertIn("batocera", body["profiles"])

    def test_run_job_lifecycle_start_connect_stop(self):
        src = self.tmp / "d.qcow2"; src.write_bytes(QCOW)
        status, body, _ = self.call("POST", "/api/run", {"name": "web1", "source": str(src), "ram_mb": 64})
        self.assertEqual(status, 200)
        job = self.wait_job(body["job"])
        self.assertEqual(job["status"], "done", job)
        inst = session.Instance("web1")
        self.addCleanup(inst.kill)
        _, ov, _ = self.call("GET", "/api/overview")
        vm = next(v for v in ov["instances"] if v["name"] == "web1")
        self.assertTrue(vm["running"])
        _, pw, _ = self.call("GET", "/api/password?name=web1")
        self.assertEqual(len(pw["password"]), 8)
        s, new, _ = self.call("POST", "/api/password/set", {"name": "web1", "generate": True})
        self.assertNotEqual(new["password"], pw["password"])
        _, logs, _ = self.call("GET", "/api/logs?name=web1&lines=5")
        self.assertIn("start", logs["text"])
        s, body, _ = self.call("POST", "/api/vm", {"name": "web1", "action": "stop"})
        self.assertEqual(self.wait_job(body["job"])["status"], "done")
        self.assertFalse(inst.running())
        # restart reuses the saved settings with no other input
        s, body, _ = self.call("POST", "/api/vm", {"name": "web1", "action": "start"})
        self.assertEqual(self.wait_job(body["job"])["status"], "done")
        self.assertTrue(inst.running())

    def test_edit_an_existing_vm_keeps_its_disk_and_applies_on_next_start(self):
        src = self.tmp / "d.qcow2"; src.write_bytes(QCOW)
        _, body, _ = self.call("POST", "/api/run", {"name": "ed1", "source": str(src), "ram_mb": 64, "vcpus": 2})
        self.assertEqual(self.wait_job(body["job"])["status"], "done")
        inst = session.Instance("ed1")
        self.addCleanup(inst.kill)
        _, body, _ = self.call("POST", "/api/vm", {"name": "ed1", "action": "stop"})
        self.wait_job(body["job"])
        disk = next(iter(inst.disk_candidates()))
        before = (disk.stat().st_size, disk.stat().st_mtime_ns)

        s, cur, _ = self.call("POST", "/api/vm/edit", {"name": "ed1"})              # read the current values
        self.assertEqual((s, cur["values"]["ram_mb"], cur["values"]["vcpus"], cur["saved"]), (200, 64, 2, False))
        self.assertIn("es1370", cur["options"]["audio"])
        s, out, _ = self.call("POST", "/api/vm/edit", {"name": "ed1", "changes": {"audio": "es1370", "vcpus": 3, "mttcg": True}})
        self.assertEqual((s, out["values"]["audio"], out["values"]["vcpus"], out["values"]["mttcg"]), (200, "es1370", 3, True))
        self.assertEqual(before, (disk.stat().st_size, disk.stat().st_mtime_ns))     # the disk was not touched

        _, body, _ = self.call("POST", "/api/vm", {"name": "ed1", "action": "start"})   # plain restart picks it up
        self.assertEqual(self.wait_job(body["job"])["status"], "done")
        cmd = inst.state()["cmd"]
        self.assertIn("ES1370,audiodev=snd0", cmd)
        self.assertEqual(cmd[cmd.index("-smp") + 1], "3")
        self.assertEqual(cmd[cmd.index("-m") + 1], "64")                              # untouched settings stay

        self.assertEqual(self.call("POST", "/api/vm/edit", {"name": "ed1", "changes": {"audio": "nope"}})[0], 400)
        for bad in ({"name": "x"}, {"system": "ubuntu"}, {"source": "/etc/passwd"}, {"password": "hunter2"}):
            self.assertEqual(self.call("POST", "/api/vm/edit", {"name": "ed1", "changes": bad})[0], 400, bad)
        self.assertEqual(self.call("POST", "/api/vm/edit", {"name": "ed1", "changes": ["audio"]})[0], 400)
        self.assertEqual(self.call("POST", "/api/vm/edit", {"name": "never-started"})[0], 400)
        self.assertEqual(self.call("POST", "/api/vm/edit", {"name": "../x"})[0], 400)
        self.assertEqual(session.load_saved_settings(inst).get("audio"), "es1370")
        self.assertNotIn("password", session.load_saved_settings(inst))

    def test_edit_from_the_command_line(self):
        src = self.tmp / "d.qcow2"; src.write_bytes(QCOW)
        _, body, _ = self.call("POST", "/api/run", {"name": "ed2", "source": str(src), "ram_mb": 64})
        self.wait_job(body["job"])
        inst = session.Instance("ed2")
        self.addCleanup(inst.kill)
        inst.stop(graceful=False, timeout=5, log=lambda *_: None)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["edit", "--name", "ed2", "--audio", "es1370", "--vcpus", "4", "--mttcg", "--ram-mb", "128"])
            rc_show = cli.main(["edit", "--name", "ed2"])
            rc_off = cli.main(["edit", "--name", "ed2", "--no-mttcg"])
        self.assertEqual((rc, rc_show, rc_off), (0, 0, 0), err.getvalue())
        saved = session.load_saved_settings(inst)
        self.assertEqual((saved["audio"], saved["vcpus"], saved["ram_mb"], saved["mttcg"]), ("es1370", 4, 128, False))
        self.assertIn("the disk was not touched", out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(cli.main(["edit", "--name", "ed2", "--vcpus", "0"]), 0)           # validated
        self.assertEqual(session.load_saved_settings(inst)["vcpus"], 4)                            # and not saved

    def test_edit_driver_iso(self):
        src = self.tmp / "d.qcow2"; src.write_bytes(QCOW)
        _, body, _ = self.call("POST", "/api/run", {"name": "ed3", "source": str(src), "ram_mb": 64})
        self.wait_job(body["job"])
        inst = session.Instance("ed3")
        self.addCleanup(inst.kill)
        inst.stop(graceful=False, timeout=5, log=lambda *_: None)
        iso = self.tmp / "drivers.iso"; iso.write_bytes(b"x" * 2048)
        s, out, _ = self.call("POST", "/api/vm/edit", {"name": "ed3", "changes": {"virtio_iso": str(iso)}})
        self.assertEqual((s, out["values"]["virtio_iso"]), (200, str(iso.resolve())))
        self.assertEqual(self.call("POST", "/api/vm/edit", {"name": "ed3", "changes": {"virtio_iso": str(self.tmp / "nope.iso")}})[0], 400)
        self.assertEqual(session.load_saved_settings(inst)["virtio_iso"], str(iso.resolve()))      # a bad path is not saved
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["edit", "--name", "ed3", "--driver-iso", ""]), 0)           # remove it again
        self.assertEqual(session.load_saved_settings(inst)["virtio_iso"], "")

    def test_job_errors_are_reported_not_raised(self):
        s, body, _ = self.call("POST", "/api/run", {"name": "bad1", "source": "/no/such/file.iso"})
        job = self.wait_job(body["job"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("not found", job["error"])

    def test_invalid_names_and_values_are_400(self):
        self.assertEqual(self.call("POST", "/api/vm", {"name": "../x", "action": "kill"})[0], 400)
        self.assertEqual(self.call("GET", "/api/logs?name=../../etc")[0], 400)
        self.assertEqual(self.call("POST", "/api/tunnel", {"name": "x1", "kind": "sh -c id"})[0], 400)

    def test_storage_listing_and_safe_delete(self):
        (self.tmp / "data" / "cache").mkdir(parents=True)
        (self.tmp / "data" / "cache" / "junk.bin").write_bytes(b"x" * 10)
        (self.tmp / "data" / "outside.txt").write_text("keep")
        _, st, _ = self.call("GET", "/api/storage")
        self.assertIn("junk.bin", [i["name"] for i in st["items"]])
        for name in ("../outside.txt", "..", ".hidden"):
            self.assertEqual(self.call("POST", "/api/storage/delete", {"kind": "cache", "name": name})[0], 400)
        self.assertEqual(self.call("POST", "/api/storage/delete", {"kind": "etc", "name": "passwd"})[0], 400)
        self.assertEqual(self.call("POST", "/api/storage/delete", {"kind": "cache", "name": "junk.bin"})[0], 200)
        self.assertTrue((self.tmp / "data" / "outside.txt").exists())

    def wait_job(self, jid, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            _, j, _ = self.call("GET", f"/api/job?id={jid}")
            if j["status"] != "running":
                return j
            time.sleep(0.2)
        self.fail("job did not finish")


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CLI(Sandbox):
    def test_list_and_json(self):
        code, out, _ = run_cli("list", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(any(s["id"] == "ubuntu" for s in json.loads(out)))
        code, out, _ = run_cli("list", "--family", "retro")
        self.assertIn("win98", out)
        self.assertNotIn("ubuntu", out)

    def test_plan_json_has_no_secrets_and_no_side_effects(self):
        os_env = {"VORTAX_PASSWORD": "topsecret"}
        import os
        os.environ.update(os_env)
        code, out, _ = run_cli("plan", "--system", "ubuntu", "--boot-mode", "iso", "--json")
        self.assertEqual(code, 0)
        self.assertNotIn("topsecret", out)
        doc = json.loads(out)
        self.assertEqual(doc["mode"], "iso")
        self.assertEqual(doc["argv"][0], "qemu-system-x86_64")
        self.assertFalse((self.tmp / "data" / "disks").exists())

    def test_env_vars_drive_plan(self):
        import os
        os.environ.update({"VORTAX_SYSTEM": "bazzite", "VORTAX_RAM_MB": "1234"})
        _, out, _ = run_cli("plan", "--json")
        self.assertEqual(json.loads(out)["argv"][json.loads(out)["argv"].index("-m") + 1], "1234")

    def test_errors_exit_1_with_a_message(self):
        code, _, err = run_cli("plan", "--system", "win98")
        self.assertEqual(code, 1)
        self.assertIn("template slot", err)
        code, _, err = run_cli("plan")
        self.assertIn("nothing to run", err)

    def test_full_flow(self):
        src = self.tmp / "d.qcow2"; src.write_bytes(QCOW)
        self.assertEqual(run_cli("start", "--name", "c1", "--source", str(src), "--ram-mb", "64")[0], 0)
        self.addCleanup(lambda: run_cli("kill", "--name", "c1"))
        code, out, _ = run_cli("status", "--name", "c1")
        self.assertIn("running", out)
        code, out, _ = run_cli("info", "--name", "c1")
        self.assertIn("hidden", out)                       # password is not printed by default
        code, pw, _ = run_cli("password", "--name", "c1")
        self.assertEqual(len(pw.strip()), 8)
        code, out, _ = run_cli("snapshot", "--name", "c1", "list")
        self.assertIn("snap1", out)
        self.assertEqual(run_cli("stop", "--name", "c1", "--force")[0], 0)
        # restart without any arguments reuses the saved launch settings
        self.assertEqual(run_cli("start", "--name", "c1")[0], 0)
        self.assertIn("running", run_cli("status", "--name", "c1")[1])
        self.assertEqual(run_cli("rm", "--name", "c1")[0], 1)         # refuses: running
        run_cli("kill", "--name", "c1")
        self.assertEqual(run_cli("rm", "--name", "c1")[0], 1)         # refuses without --yes
        self.assertEqual(run_cli("rm", "--name", "c1", "--yes")[0], 0)
        self.assertEqual(session.Instance("c1").existing_disk(), None)

    def test_doctor_reports_stub_tools(self):
        code, out, _ = run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("qemu-img", out)

    def test_selftest_command(self):
        code, out, _ = run_cli("selftest", "--boot", "--timeout", "20")
        self.assertEqual(code, 0, out)
        self.assertIn("end-to-end boot test passed", out)

    def test_access_dry_run_changes_nothing(self):
        code, out, _ = run_cli("access", "install", "rdp", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("xrdp", out)
        code, _, err = run_cli("access", "install", "rustdesk")
        self.assertEqual(code, 1)
        self.assertIn("--allow-unverified", err)

    def test_invalid_vm_name_rejected(self):
        code, _, err = run_cli("stop", "--name", "a b")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
