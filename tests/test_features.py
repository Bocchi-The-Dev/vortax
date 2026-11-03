# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
import contextlib
import io
import json
import os
import struct
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

from vortax import catalog, cli, disk, envinfo, fetch, launch, png, presets, qemu, resolvers, session, settings
from vortax.errors import VortaxError
from tests.helpers import Sandbox

QCOW = b"QFI\xfb" + b"\0" * 600


def run_cli(*argv, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        if stdin is not None:
            with mock.patch("builtins.input", side_effect=list(stdin)):
                rc = cli.main(list(argv))
        else:
            rc = cli.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class Builder(unittest.TestCase):
    def plan(self, **kw):
        return qemu.build_command(qemu.VMPlan(name="t", disk_path="/d", monitor_sock="/m", **kw))

    def test_share_usb_audio(self):
        a = self.plan(shares=[("/h/x", "x")], usb=[(0x46d, 0xc52b)], audio="hda")
        self.assertIn("local,path=/h/x,mount_tag=x,security_model=none,id=share0", a)
        self.assertIn("usb-host,vendorid=0x046d,productid=0xc52b,bus=ehci.0", a)
        self.assertIn("none,id=snd0", a)
        self.assertIn("hda-duplex,audiodev=snd0", a)

    def test_audio_is_offered_to_the_vnc_console(self):
        a = self.plan(audio="ac97")
        self.assertLess(a.index("-audiodev"), a.index("-vnc"))          # defined before the VNC server uses it
        self.assertEqual(a[a.index("-vnc") + 1], "127.0.0.1:0,password=on,audiodev=snd0")
        self.assertEqual(a.count("-audiodev"), 1)
        self.assertIn("AC97,audiodev=snd0", a)
        self.assertIn("audiodev=snd0", self.plan()[self.plan().index("-vnc") + 1])     # always there, so a card can be added live
        self.assertNotIn("AC97", self.plan())

    def test_new_options_validate_and_read_from_env(self):
        from vortax import settings
        self.assertIn("es1370", settings.AUDIO)
        self.assertTrue(settings.resolve({"mttcg": True}, environ={}, config={}).mttcg)
        self.assertTrue(settings.resolve(None, environ={"VORTAX_MTTCG": "1"}, config={}).mttcg)
        self.assertFalse(settings.resolve(None, environ={}, config={}).mttcg)
        self.assertEqual(settings.resolve({"audio": "es1370"}, environ={}, config={}).audio, "es1370")
        ns = cli.build_parser().parse_args(["start", "--mttcg", "--audio", "es1370", "--vcpus", "4"])
        self.assertTrue(ns.mttcg)
        self.assertEqual((ns.audio, ns.vcpus), ("es1370", 4))
        self.assertIsNone(cli.build_parser().parse_args(["start"]).mttcg)      # unset = keep the saved or default value

    def test_defaults_have_no_extras(self):
        a = self.plan()
        for bad in ("-virtfs", "usb-host", "AC97", "hda-duplex"):
            self.assertNotIn(bad, a)

    def test_termux_caps_ram(self):
        with mock.patch.dict(os.environ, {"TERMUX_VERSION": "1"}), mock.patch.object(qemu, "host_ram_mb", return_value=16000):
            self.assertEqual(qemu.default_ram_mb(), 2048)


class Parsing(unittest.TestCase):
    def test_share_and_usb(self):
        self.assertEqual(settings.parse_share("/a/b:tag"), ("/a/b", "tag"))
        self.assertEqual(settings.parse_share("/a/My Files")[1], "My_Files")
        self.assertEqual(settings.parse_usb("046d:c52b"), (0x46d, 0xc52b))
        with self.assertRaises(VortaxError):
            settings.parse_usb("nonsense")

    def test_png_roundtrip(self):
        data = png.ppm_to_png(b"P6\n# c\n2 1\n255\n" + bytes(range(6)))
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        i = data.index(b"IDAT")
        n = struct.unpack(">I", data[i - 4:i])[0]
        self.assertEqual(zlib.decompress(data[i + 4:i + 4 + n]), b"\x00" + bytes(range(6)))
        with self.assertRaises(VortaxError):
            png.ppm_to_png(b"P5\n1 1\n255\n\x00")

    def test_resolver_picks_newest(self):
        listing = 'a href="debian-12.5.0-amd64-netinst.iso" debian-12.10.0-amd64-netinst.iso debian-12.9.0-amd64-netinst.iso'
        self.assertEqual(resolvers.pick_latest(listing, resolvers.INDEXES["debian"][1]),
                         "debian-12.10.0-amd64-netinst.iso")
        self.assertTrue(resolvers.resolve_latest("debian", lambda u: listing).endswith("12.10.0-amd64-netinst.iso"))
        with self.assertRaises(VortaxError):
            resolvers.resolve_latest("nope")
        with self.assertRaises(VortaxError):
            resolvers.pick_latest("nothing", r"x\d")

    def test_progress_throttles(self):
        lines = []
        p = fetch.Progress(100 << 20, 0, lines.append)
        for _ in range(100):
            p.add(1 << 20)
        self.assertLessEqual(len(lines), 21)
        self.assertIn("100%", lines[-1])

    def test_termux_install_command(self):
        self.assertEqual(envinfo.termux_install_command(["qemu-img", "7z", "bogus"]), "pkg install -y p7zip qemu-utils")


class Features(Sandbox):
    def start(self, **extra):
        src = self.tmp / "d.qcow2"
        src.write_bytes(QCOW)
        args = ["start", "--source", str(src), "--name", "f1", "--ram-mb", "64", "--no-password"]
        for k, v in extra.items():
            args += [f"--{k.replace('_', '-')}"] + ([] if v is True else [str(v)])
        rc, out, err = run_cli(*args)
        self.assertEqual(rc, 0, err)
        self.addCleanup(lambda: session.Instance("f1").kill())
        return session.Instance("f1")

    def test_presets_crud_and_use(self):
        rc, out, _ = run_cli("preset", "save", "mine", "--ram-mb", "128", "--ssh", "--share", "/tmp:tmp")
        self.assertEqual(rc, 0, out)
        data = presets.get("mine")
        self.assertEqual(data["ram_mb"], 128)
        self.assertTrue(data["ssh"])
        self.assertEqual(data["shares"], ["/tmp:tmp"])
        self.assertNotIn("password", data)
        self.assertIn("mine", run_cli("preset", "list")[1])
        s = cli._settings_from(cli.build_parser().parse_args(["plan", "mine", "--source", "/x"]))
        self.assertEqual((s.ram_mb, s.ssh, s.shares), (128, True, ["/tmp:tmp"]))
        s = cli._settings_from(cli.build_parser().parse_args(["plan", "--preset", "mine", "--ram-mb", "256", "--source", "/x"]))
        self.assertEqual(s.ram_mb, 256)                      # command line beats preset
        self.assertEqual(run_cli("preset", "rm", "mine")[0], 0)
        self.assertEqual(run_cli("preset", "rm", "mine")[0], 1)

    def test_preset_never_stores_secrets(self):
        with self.assertRaises(VortaxError):
            presets.save("x", {"password": "hunter2"})

    def test_target_is_system_or_file(self):
        ns = cli.build_parser().parse_args(["plan", "alpine"])
        self.assertEqual(cli._settings_from(ns).system, "alpine")
        f = self.tmp / "my.iso"
        f.write_bytes(b"x")
        ns = cli.build_parser().parse_args(["plan", str(f)])
        self.assertEqual(cli._settings_from(ns).source, str(f))
        with self.assertRaises(VortaxError):
            cli._settings_from(cli.build_parser().parse_args(["plan", "no-such-thing"]))

    def test_ssh_forward_and_command(self):
        inst = self.start(ssh=True)
        port = inst.ssh_port()
        self.assertGreaterEqual(port, 2222)
        self.assertIn(f"hostfwd=tcp:127.0.0.1:{port}-:22", " ".join(inst.state()["cmd"]))
        with mock.patch("shutil.which", side_effect=lambda n: f"/usr/bin/{n}"):
            rc, out, _ = run_cli("ssh", "--name", "f1", "--dry-run", "-u", "bob", "uname")
            self.assertEqual(rc, 0)
            self.assertIn(f"-p {port}", out)
            self.assertIn("bob@127.0.0.1 uname", out)
            rc, out, _ = run_cli("cp", "--name", "f1", "--dry-run", "a.txt", "vm:/tmp/")
            self.assertIn("root@127.0.0.1:/tmp/", out)
            self.assertEqual(run_cli("cp", "--name", "f1", "--dry-run", "a", "b")[0], 1)

    def test_ssh_needs_flag(self):
        self.start()
        rc, _, err = run_cli("ssh", "--name", "f1", "--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("--ssh", err)

    def test_screenshot(self):
        self.start()
        out = self.tmp / "s.png"
        rc, _, err = run_cli("screenshot", "--name", "f1", "--out", str(out))
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_logs(self):
        self.start()
        rc, out, _ = run_cli("logs", "--name", "f1", "-n", "5")
        self.assertEqual(rc, 0)
        self.assertIn("start", out)
        self.assertEqual(run_cli("logs", "--name", "nobody")[0], 1)

    def test_disk_tools(self):
        self.start()
        inst = session.Instance("f1")
        self.assertIn("format: qcow2", run_cli("disk", "--name", "f1", "info")[1])
        self.assertEqual(run_cli("disk", "--name", "f1", "resize", "+1G")[0], 1)      # running
        inst.kill()
        self.assertEqual(run_cli("disk", "--name", "f1", "resize", "+1G")[0], 0)
        self.assertEqual(run_cli("disk", "--name", "f1", "resize", "lots")[0], 1)
        rc, out, _ = run_cli("disk", "--name", "f1", "compact")
        self.assertEqual(rc, 0, out)
        self.assertIn("->", out)

    def test_snapshot_on_stop_saves_state(self):
        inst = self.start(snapshot_on_stop=True)
        self.assertTrue(inst.state()["snapshot_on_stop"])
        calls = []
        real = session.monitor.hmp
        session.monitor.hmp = lambda sock, cmd, timeout=5.0: (calls.append(cmd), real(sock, cmd, timeout))[1]
        try:
            inst.stop(graceful=True, timeout=5)
        finally:
            session.monitor.hmp = real
        self.assertEqual(calls[0], f"savevm {session.AUTOSAVE}")
        self.assertIn("quit", calls)
        self.assertFalse(inst.running())

    def test_guard_stops_vm_after_runtime(self):
        inst = self.start()
        inst.update_state(runtime=1)
        ns = cli.build_parser().parse_args(["_guard", "--name", "f1", "--deadline-epoch", str(time.time() + 0.3)])
        cli.cmd_guard(ns)
        self.assertFalse(inst.running())

    def test_guard_leaves_a_restarted_vm_alone(self):
        inst = self.start()
        inst.update_state(runtime=1)
        old_pid = inst.state()["pid"]
        inst.update_state(pid=old_pid + 99999)       # simulate a different run
        ns = cli.build_parser().parse_args(["_guard", "--name", "f1", "--deadline-epoch", str(time.time() + 0.2)])
        cli.cmd_guard(ns)
        inst.update_state(pid=old_pid)
        self.assertTrue(inst.running())

    def test_wizard_saves_preset_and_starts(self):
        src = self.tmp / "x"
        with mock.patch.object(cli, "cmd_start", return_value=0) as started:
            rc, out, err = run_cli("wizard", stdin=["alpine", "w1", "256", "none", "wp", "n"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(presets.get("wp")["ram_mb"], 256)
        self.assertEqual(presets.get("wp")["system"], "alpine")
        started.assert_not_called()

    def test_license_flag(self):
        rc, out, _ = run_cli("--license")
        self.assertEqual(rc, 0)
        self.assertIn("AGPL", out)
        self.assertIn("github.com/CodeVortax/vortax", out)


if __name__ == "__main__":
    unittest.main()


class LatestSource(Sandbox):
    def test_download_resolves_latest_before_caching(self):
        seen = []
        with mock.patch.object(resolvers, "resolve_latest", return_value="https://x.test/a-2.iso"), \
                mock.patch.object(fetch, "candidate_commands", return_value=[]), \
                mock.patch.object(fetch, "_urllib_download", side_effect=lambda u, d, log=None: (seen.append(u), d.write_bytes(b"iso"))):
            p = fetch.download("latest:alpine", self.tmp / "cache", log=lambda m: None)
        self.assertEqual(seen, ["https://x.test/a-2.iso"])
        self.assertIn("a-2.iso", p.name)


class CpuModel(Sandbox):
    def test_cpu_model_reaches_qemu_on_every_x86_path(self):
        from vortax import catalog, qemu, settings
        from tests.test_qemu import plan, flag
        self.assertEqual(flag(qemu.build_command(plan(cpu="core2duo", accel="tcg")), "-cpu"), "core2duo")
        self.assertEqual(flag(qemu.build_command(plan(accel="tcg")), "-cpu"), "max")                       # automatic stays as before
        xp = catalog.load_profiles()["winxp"]
        self.assertEqual(flag(qemu.build_command(plan(profile=xp, accel="tcg")), "-cpu"), "qemu64")        # the preset's own model
        self.assertEqual(flag(qemu.build_command(plan(profile=xp, accel="tcg", cpu="pentium3")), "-cpu"), "pentium3")
        self.assertEqual(flag(qemu.build_command(plan(cpu="host", accel="tcg")), "-cpu"), "max")           # host means max without KVM

    def test_cpu_model_is_validated(self):
        from vortax import settings
        from vortax.errors import VortaxError
        ok = settings.resolve({"cpu": "Skylake-Client,-avx512f"}, environ={}, config={})
        self.assertEqual(ok.cpu, "Skylake-Client,-avx512f")
        for bad in ("qemu64 -smp 9", "a;b", "x,,y", "$(id)", "a" * 60):
            with self.assertRaises(VortaxError):
                settings.resolve({"cpu": bad}, environ={}, config={})

