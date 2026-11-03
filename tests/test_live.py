# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Live changes to a running VM, against a recording fake monitor."""
import contextlib
import io

from vortax import cli, live
from vortax.errors import VortaxError
from tests.helpers import Sandbox

INFO_WITH_CD = """ide0-hd0 (#block110): /d/xp.qcow2 (qcow2)
    Attached to:      /machine/unattached/device[18]
    Cache mode:       writeback

ide1-cd0 (#block240): [not inserted]
    Attached to:      /machine/unattached/device[19]
    Removable device: not locked, tray closed
"""
INFO_NO_CD = """ide0-hd0 (#block110): /d/xp.qcow2 (qcow2)
    Attached to:      /machine/unattached/device[18]
"""


class FakeInst:
    name = "vm"

    def __init__(self, info, replies=None, running=True):
        self.info, self.replies, self.cmds, self._state, self._running = info, replies or {}, [], {}, running

    def running(self):
        return self._running

    def state(self):
        return dict(self._state)

    def update_state(self, **kw):
        self._state.update(kw)

    def hmp(self, cmd, timeout=5.0):
        self.cmds.append(cmd)
        if cmd == "info block":
            return self.info
        for prefix, out in self.replies.items():
            if cmd.startswith(prefix):
                return out
        return ""


class Live(Sandbox):
    def iso(self, name="a b.iso"):
        p = self.tmp / name
        p.write_bytes(b"x" * 10)
        return str(p)

    def test_removable_drive_parser(self):
        self.assertEqual(live.removable_drives(INFO_WITH_CD), ["ide1-cd0"])
        self.assertEqual(live.removable_drives(INFO_NO_CD), [])

    def test_swap_disc_in_existing_drive_quotes_the_path(self):
        inst = FakeInst(INFO_WITH_CD)
        msg = live.apply(inst, "cd_attach", self.iso())
        self.assertEqual(inst.cmds[1], "eject -f ide1-cd0")
        self.assertTrue(inst.cmds[2].startswith('change ide1-cd0 "') and inst.cmds[2].endswith('a b.iso"'))
        self.assertIn("inserted", msg)
        self.assertIn("ejected", live.apply(inst, "cd_eject"))

    def test_no_drive_adds_usb_cd_then_removes_it(self):
        inst = FakeInst(INFO_NO_CD)
        live.apply(inst, "cd_attach", self.iso("d.iso"))
        self.assertIn("drive_add 0 if=none,id=live_cd,file=", inst.cmds[1])
        self.assertIn("device_add usb-storage,drive=live_cd,id=live_cd_dev,removable=on", inst.cmds)
        self.assertEqual(inst.state()["live_cd"], "usb")
        live.apply(inst, "cd_eject")
        self.assertEqual(inst.cmds[-2:], ["device_del live_cd_dev", "drive_del live_cd"])
        self.assertEqual(inst.state()["live_cd"], "")

    def test_usb_cd_failure_cleans_up(self):
        inst = FakeInst(INFO_NO_CD, {"device_add": "Bus 'usb-bus.0' not found"})
        with self.assertRaises(VortaxError) as ctx:
            live.apply(inst, "cd_attach", self.iso("d.iso"))
        self.assertIn("usb-bus.0", str(ctx.exception))
        self.assertIn("drive_del live_cd", inst.cmds)
        with self.assertRaises(VortaxError):
            live.apply(FakeInst(INFO_NO_CD), "cd_attach", self.iso())          # spaces: refused for a new drive

    def test_missing_iso_and_stopped_vm(self):
        with self.assertRaises(VortaxError):
            live.apply(FakeInst(INFO_WITH_CD), "cd_attach", str(self.tmp / "nope.iso"))
        with self.assertRaises(VortaxError) as ctx:
            live.apply(FakeInst(INFO_WITH_CD, running=False), "cd_eject")
        self.assertIn("vortax edit", str(ctx.exception))

    def test_audio_add_and_remove(self):
        inst = FakeInst(INFO_NO_CD)
        live.apply(inst, "audio_add", "ac97")
        self.assertEqual(inst.cmds, ["device_add AC97,audiodev=snd0,id=live_snd"])
        live.apply(inst, "audio_add", "hda")                                     # replaces the first card
        self.assertEqual(inst.cmds[1], "device_del live_snd")
        self.assertEqual(inst.cmds[2:], ["device_add intel-hda,id=live_hda",
                                         "device_add hda-duplex,bus=live_hda.0,audiodev=snd0,id=live_hdacodec"])
        live.apply(inst, "audio_remove")
        self.assertEqual(inst.cmds[-1], "device_del live_hda")
        with self.assertRaises(VortaxError):
            live.apply(inst, "audio_remove")
        with self.assertRaises(VortaxError):
            live.apply(inst, "audio_add", "sb16")
        with self.assertRaises(VortaxError):
            live.apply(inst, "audio_add", "bogus")

    def test_old_vm_without_audio_backend_gets_a_hint(self):
        inst = FakeInst(INFO_NO_CD, {"device_add": "Property 'AC97.audiodev' can't find value 'snd0'"})
        with self.assertRaises(VortaxError) as ctx:
            live.apply(inst, "audio_add", "ac97")
        self.assertIn("restart it once", str(ctx.exception))
        self.assertEqual(inst.state().get("live_audio"), None)

    def test_cli_requires_an_option(self):
        ns = cli.build_parser().parse_args(["live", "--name", "x"])
        with self.assertRaises(VortaxError):
            cli.cmd_live(ns)
        ns = cli.build_parser().parse_args(["live", "--cd", "/a.iso", "--audio", "es1370"])
        self.assertEqual((ns.cd, ns.audio), ("/a.iso", "es1370"))
        self.assertTrue(contextlib.redirect_stdout(io.StringIO()))
