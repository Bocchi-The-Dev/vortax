import gzip
import io
import json
import os
import tarfile
import time
import unittest
from pathlib import Path

from vortax import catalog, launch, monitor, qemu, selftest, session, settings
from vortax.errors import VortaxError
from tests.helpers import Sandbox

QCOW = b"QFI\xfb" + b"\0" * 600


def iso_bytes():
    b = bytearray(40000)
    b[0x8001:0x8006] = b"CD001"
    return bytes(b)


class Lifecycle(Sandbox):
    def make(self, **cli):
        cli.setdefault("name", "t1")
        cli.setdefault("ram_mb", 64)
        s = settings.resolve(cli, environ={}, config={})
        return s, catalog.resolve(s), session.Instance(s.name)

    def write(self, name, data):
        p = self.tmp / name
        p.write_bytes(data)
        return p

    # ---- prepare
    def test_prepare_iso_creates_blank_disk_and_symlinks_local_iso(self):
        src = self.write("in.iso", iso_bytes())
        s, r, inst = self.make(source=str(src))
        prep = session.prepare(s, r, inst, log=lambda m: None)
        self.assertEqual(prep.disk_format, "qcow2")
        self.assertTrue(prep.disk_path.name.endswith(".qcow2") and prep.disk_path.exists())
        self.assertTrue(inst.iso.is_symlink())
        self.assertTrue(src.exists())                      # the user's file is untouched

    def test_prepare_local_qcow2_is_copied_not_moved(self):
        src = self.write("d.qcow2", QCOW)
        s, r, inst = self.make(source=str(src))
        prep = session.prepare(s, r, inst, log=lambda m: None)
        self.assertEqual(prep.disk_format, "qcow2")
        self.assertTrue(src.exists())
        self.assertNotEqual(os.stat(src).st_ino, os.stat(prep.disk_path).st_ino)

    def test_raw_img_is_kept_raw_not_renamed_to_qcow2(self):
        # the original workflow renamed raw .img to .qcow2 without converting
        src = self.write("batocera.img", b"\0" * 4096)
        s, r, inst = self.make(source=str(src))
        prep = session.prepare(s, r, inst, log=lambda m: None)
        self.assertEqual(prep.disk_format, "raw")
        self.assertEqual(prep.disk_path.suffix, ".img")

    def test_img_gz_roundtrip(self):
        src = self.write("b.img.gz", gzip.compress(b"\0" * 4096))
        s, r, inst = self.make(source=str(src))
        prep = session.prepare(s, r, inst, log=lambda m: None)
        self.assertEqual((prep.disk_format, prep.disk_path.suffix), ("raw", ".img"))

    def test_foreign_format_is_converted(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo("vm.vmdk"); info.size = 100
            tf.addfile(info, io.BytesIO(b"K" * 100))
        src = self.write("v.tar.gz", buf.getvalue())
        s, r, inst = self.make(source=str(src))
        prep = session.prepare(s, r, inst, log=lambda m: None)
        self.assertEqual(prep.disk_format, "qcow2")

    def test_existing_disk_is_never_overwritten_without_rebuild(self):
        src = self.write("d.qcow2", QCOW)
        s, r, inst = self.make(source=str(src))
        prep = session.prepare(s, r, inst, log=lambda m: None)
        prep.disk_path.write_bytes(QCOW + b"user data")
        session.prepare(s, r, inst, log=lambda m: None)
        self.assertIn(b"user data", prep.disk_path.read_bytes())
        session.prepare(s, r, inst, force=True, log=lambda m: None)
        self.assertNotIn(b"user data", prep.disk_path.read_bytes())

    def test_unusable_file_is_a_clear_error(self):
        src = self.write("junk.bin", b"hello")
        s, r, inst = self.make(source=str(src))
        with self.assertRaisesRegex(VortaxError, "not an ISO"):
            session.prepare(s, r, inst, log=lambda m: None)

    # ---- start / control (fake qemu)
    def start(self, **cli):
        src = self.write("d.qcow2", QCOW)
        s, r, inst = self.make(source=str(src), **cli)
        prep = session.prepare(s, r, inst, log=lambda m: None)
        state = launch.launch(s, r, inst, prep, log=lambda m: None)
        self.addCleanup(inst.kill)
        return s, r, inst, state

    def test_start_sets_password_through_the_monitor_not_argv(self):
        s, r, inst, state = self.start()
        pw = inst.vnc_password()
        self.assertEqual(len(pw), 8)
        self.assertNotIn(pw, " ".join(state["cmd"]))
        self.assertEqual((inst.run / "vnc.pass").stat().st_mode & 0o777, 0o600)
        time.sleep(0.2)
        self.assertEqual(Path(str(inst.sock) + ".vncpw").read_text(), pw)   # fake qemu recorded what it was sent

    def test_running_status_and_control(self):
        s, r, inst, state = self.start()
        self.assertTrue(inst.running())
        st = inst.status()
        self.assertEqual((st["running"], st["vnc"]["port"] >= 5900), (True, True))
        self.assertIn("running", inst.hmp("info status"))
        inst.reboot()
        self.assertTrue(inst.stop(graceful=True, timeout=10))
        self.assertFalse(inst.running())
        self.assertFalse(inst.status()["running"])

    def test_double_start_is_refused(self):
        s, r, inst, _ = self.start()
        with self.assertRaisesRegex(VortaxError, "already running"):
            session.start(s, r, inst, session.Prepared(inst.existing_disk(), "qcow2"), log=lambda m: None)

    def test_password_change_applies_live_and_truncates(self):
        s, r, inst, _ = self.start()
        out = inst.set_vnc_password("abcdefghijkl")
        self.assertEqual(out, "abcdefgh")
        time.sleep(0.2)
        self.assertEqual(Path(str(inst.sock) + ".vncpw").read_text(), "abcdefgh")

    def test_qemu_failing_to_start_reports_its_log(self):
        os.environ["FAKE_QEMU_FAIL"] = "1"
        src = self.write("d.qcow2", QCOW)
        s, r, inst = self.make(source=str(src))
        prep = session.prepare(s, r, inst, log=lambda m: None)
        with self.assertRaisesRegex(VortaxError, "refusing to start"):
            session.start(s, r, inst, prep, log=lambda m: None)

    def test_two_vms_get_different_vnc_ports(self):
        _, _, a, sa = self.start(name="a1")
        _, _, b, sb = self.start(name="b1")
        self.assertNotEqual(sa["vnc"]["port"], sb["vnc"]["port"])

    def test_no_password_requires_loopback(self):
        src = self.write("d.qcow2", QCOW)
        s, r, inst = self.make(source=str(src), no_password=True, vnc_bind="0.0.0.0")
        prep = session.prepare(s, r, inst, log=lambda m: None)
        with self.assertRaisesRegex(VortaxError, "127.0.0.1"):
            session.start(s, r, inst, prep, log=lambda m: None)

    def test_saved_settings_exclude_passwords_and_enable_restart(self):
        src = self.write("d.qcow2", QCOW)
        s, r, inst = self.make(source=str(src))
        s.password = "secret12"
        prep = session.prepare(s, r, inst, log=lambda m: None)
        launch.launch(s, r, inst, prep, log=lambda m: None)
        self.addCleanup(inst.kill)
        saved = session.load_saved_settings(inst)
        self.assertEqual(saved["source"], str(src))
        self.assertNotIn("secret12", json.dumps(saved))

    def test_snapshots_via_monitor_and_offline(self):
        s, r, inst, _ = self.start()
        self.assertIn("snap1", inst.snapshot("list"))
        inst.snapshot("save", "before")
        inst.stop(graceful=False)
        self.assertIn("snap1", inst.snapshot("list"))
        with self.assertRaises(VortaxError):
            inst.snapshot("save", "bad name!")

    def test_export_requires_stopped_vm(self):
        s, r, inst, _ = self.start()
        with self.assertRaisesRegex(VortaxError, "stop"):
            session.export(inst, self.tmp / "out", log=lambda m: None)
        inst.stop(graceful=False)
        out = session.export(inst, self.tmp / "out", log=lambda m: None)
        self.assertTrue(out.exists() and out.stat().st_size > 0)

    def test_boot_disk_option_drops_the_installer(self):
        src = self.write("in.iso", iso_bytes())
        s, r, inst = self.make(source=str(src), boot="disk")
        prep = session.prepare(s, r, inst, log=lambda m: None)
        _, argv = session.start(s, r, inst, prep, log=lambda m: None, dry_run=True)
        self.assertNotIn("-cdrom", argv)


class Monitor(Sandbox):
    def test_unreachable_socket_is_a_vortax_error(self):
        with self.assertRaises(VortaxError):
            monitor.hmp(str(self.tmp / "nope.sock"), "info status")

    def test_newlines_are_rejected(self):
        with self.assertRaises(VortaxError):
            monitor.hmp("/x", "quit\nsystem_reset")


class SelfTest(Sandbox):
    def test_plans_are_consistent(self):
        self.assertEqual(selftest.check_plans(), [])

    def test_boot_sector_shape(self):
        b = selftest.boot_sector()
        self.assertEqual((len(b), b[510:]), (512, b"\x55\xaa"))
        self.assertEqual(b[0x16:0x16 + 9], b"VORTAX-OK")

    def test_end_to_end_with_fake_qemu(self):
        logs = []
        selftest.boot_test(timeout=20, log=logs.append)
        self.assertTrue(any("marker" in m for m in logs))


if __name__ == "__main__":
    unittest.main()
