# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
import unittest

from vortax import qemu
from vortax.errors import VortaxError


def plan(**kw):
    base = dict(name="t", arch="x86_64", accel="kvm", disk_path="/d/t.qcow2", monitor_sock="/r/m.sock", ram_mb=2048)
    base.update(kw)
    return qemu.VMPlan(**base)


def flag(argv, name):
    i = argv.index(name)
    return argv[i + 1]


class ModernX86(unittest.TestCase):
    def test_bios_boot_from_disk(self):
        a = qemu.build_command(plan())
        self.assertEqual(a[0], "qemu-system-x86_64")
        self.assertEqual(flag(a, "-accel"), "kvm")
        self.assertEqual(flag(a, "-cpu"), "host")
        self.assertEqual(flag(a, "-boot"), "c")
        self.assertIn("format=qcow2", flag(a, "-drive"))
        self.assertNotIn("-cdrom", a)

    def test_tcg_uses_max_cpu(self):
        a = qemu.build_command(plan(accel="tcg"))
        self.assertEqual(flag(a, "-accel"), "tcg")
        self.assertEqual(flag(a, "-cpu"), "max")

    def test_iso_boots_cdrom(self):
        a = qemu.build_command(plan(iso_path="/m/t.iso"))
        self.assertEqual(flag(a, "-boot"), "d")
        self.assertEqual(flag(a, "-cdrom"), "/m/t.iso")

    def test_uefi_iso_uses_virtio_blk_and_pflash(self):
        a = qemu.build_command(plan(firmware="uefi", iso_path="/m/t.iso", uefi_code="/c.fd", uefi_vars="/v.fd"))
        self.assertIn("virtio-blk-pci,drive=hd0,bootindex=1", a)
        self.assertEqual(sum(1 for x in a if x.startswith("if=pflash")), 2)

    def test_uefi_without_firmware_is_an_error(self):
        with self.assertRaises(VortaxError):
            qemu.build_command(plan(firmware="uefi"))

    def test_vnc_is_loopback_with_password_and_monitor_is_a_unix_socket(self):
        a = qemu.build_command(plan())
        self.assertEqual(flag(a, "-vnc"), "127.0.0.1:0,password=on")
        self.assertTrue(flag(a, "-monitor").startswith("unix:/r/m.sock"))
        self.assertNotIn("tcp:", " ".join(a))

    def test_no_password_drops_the_option(self):
        self.assertEqual(flag(qemu.build_command(plan(vnc_password=False)), "-vnc"), "127.0.0.1:0")

    def test_forwards_are_bound_to_loopback(self):
        a = qemu.build_command(plan(forwards=[("tcp", 2222, 22)]))
        self.assertIn("hostfwd=tcp:127.0.0.1:2222-:22", flag(a, "-netdev"))

    def test_extra_args_are_appended_last_and_verbatim(self):
        a = qemu.build_command(plan(extra_args=["-device", "sb16"]))
        self.assertEqual(a[-2:], ["-device", "sb16"])

    def test_virtio_iso_only_when_given(self):
        self.assertNotIn("ide-cd,bus=ide.1,drive=cd1", qemu.build_command(plan()))
        self.assertIn("ide-cd,bus=ide.1,drive=cd1", qemu.build_command(plan(virtio_iso="/v.iso")))

    def test_ram_omitted_when_none(self):
        self.assertNotIn("-m", qemu.build_command(plan(ram_mb=None)))


class Legacy(unittest.TestCase):
    P = {
        "winme": {"cpu": "pentium3", "vga": ["-vga", "cirrus"], "nic": "pcnet", "ram_mb": 128, "kvm": False, "rtc": "localtime"},
        "win98": {"cpu": "host", "machine": "pc,hpet=off,acpi=on,usb=on", "vga": ["-device", "VGA,vgamem_mb=64"],
                  "nic": "rtl8139", "ram_mb": 256, "kvm": True, "rtc": "localtime", "extra": ["-device", "usb-tablet"]},
        "win95": {"cpu": None, "vga": ["-vga", "std"], "nic": "pcnet", "ram_mb": None, "kvm": True, "boot": "floppy"},
        "win1x": {"vga": ["-vga", "std"], "nic": "none", "ram_mb": 1, "kvm": True, "boot": "floppy"},
    }

    def test_me_never_uses_kvm(self):
        a = qemu.build_command(plan(profile=self.P["winme"]))
        self.assertEqual(flag(a, "-accel"), "tcg")
        self.assertEqual(flag(a, "-m"), "128")
        self.assertNotIn("usb-ehci,id=ehci", a)             # no virtio/USB-era devices
        self.assertIn("base=localtime", a)

    def test_windows_xp_preset_is_tuned_for_a_phone(self):
        from vortax import catalog
        xp = catalog.load_profiles()["winxp"]
        self.assertEqual(catalog.load_systems()["XP"].profile, "winxp")
        a = qemu.build_command(plan(profile=xp, accel="tcg", ram_mb=2048))
        self.assertEqual(flag(a, "-accel"), "tcg,tb-size=256")        # bounded translation cache
        self.assertEqual(a.count("-accel"), 1)
        self.assertEqual(flag(a, "-m"), "512")                        # the preset wins over a 2 GB phone default
        self.assertEqual(flag(a, "-cpu"), "qemu64")
        self.assertEqual(flag(a, "-vga"), "cirrus")
        self.assertIn("rtl8139,netdev=net0", a)
        self.assertIn("usb-tablet", a)                                # keeps the VNC pointer aligned
        self.assertIn("base=localtime", a)
        self.assertEqual(flag(a, "-smp"), "1")                        # TCG runs every vCPU on one host thread
        k = qemu.build_command(plan(profile=xp, accel="kvm", vcpus=4))
        self.assertEqual(flag(k, "-accel"), "kvm")                    # the cache cap is a TCG-only option
        self.assertEqual(flag(k, "-smp"), "4")                        # with KVM the user's vCPU count is kept
        self.assertEqual(flag(qemu.build_command(plan(profile=self.P["win98"], accel="tcg", vcpus=2)), "-smp"), "2")

    def test_status_shows_what_really_runs(self):
        from vortax import catalog
        xp = catalog.load_profiles()["winxp"]
        self.assertEqual(qemu.effective_resources(plan(profile=xp, accel="tcg", ram_mb=1880, vcpus=2)), (512, 1))
        self.assertEqual(qemu.effective_resources(plan(profile=xp, accel="kvm", ram_mb=1880, vcpus=2)), (512, 2))
        self.assertEqual(qemu.effective_resources(plan(ram_mb=1880, vcpus=2)), (1880, 2))      # no preset: unchanged
        for accel in ("tcg", "kvm"):                      # the number shown matches the command that is built
            for prof in (xp, self.P["win98"], None):
                p = plan(profile=prof, accel=accel, ram_mb=1880, vcpus=3)
                ram, cpus = qemu.effective_resources(p)
                argv = qemu.build_command(p)
                self.assertEqual(flag(argv, "-smp"), str(cpus))
                self.assertEqual(flag(argv, "-m"), str(ram) if ram else None)

    def test_xp_audio_reaches_the_vnc_server_and_a_sound_card(self):
        from vortax import catalog
        a = qemu.build_command(plan(profile=catalog.load_profiles()["winxp"], accel="tcg", audio="sb16"))
        self.assertEqual(flag(a, "-vnc"), "127.0.0.1:0,password=on,audiodev=snd0")
        self.assertIn("sb16,audiodev=snd0", a)
        self.assertLess(a.index("-audiodev"), a.index("-vnc"))

    def test_single_accel_flag_and_host_cpu_fallback(self):
        a = qemu.build_command(plan(profile=self.P["win98"]))
        self.assertEqual(a.count("-accel"), 1)
        self.assertEqual(flag(a, "-M"), "pc,hpet=off,acpi=on,usb=on")
        b = qemu.build_command(plan(profile=self.P["win98"], accel="tcg"))
        self.assertEqual(flag(b, "-cpu"), "max")

    def test_floppy_boot(self):
        a = qemu.build_command(plan(profile=self.P["win95"], floppy_path="/f.ima"))
        self.assertEqual(flag(a, "-boot"), "a")
        self.assertEqual(flag(a, "-fda"), "/f.ima")
        self.assertNotIn("-m", a)                            # null ram_mb: QEMU default

    def test_no_network(self):
        a = qemu.build_command(plan(profile=self.P["win1x"], floppy_path="/f.ima"))
        self.assertIn("-net", a)
        self.assertNotIn("-netdev", a)

    def test_batocera_gpu_modes(self):
        from vortax import catalog
        prof = catalog.load_profiles()["batocera"]
        for mode, expect in (("virtio", "virtio-gpu-pci"), ("bochs", "bochs-display")):
            a = qemu.build_command(plan(profile=prof, gpu_mode=mode, disk_format="raw"))
            self.assertIn(expect, a)
            self.assertIn("hostfwd=tcp:127.0.0.1:5555-:22", flag(a, "-netdev"))
        a = qemu.build_command(plan(profile=prof, gpu_mode="std", disk_format="raw"))
        self.assertIn("std", a)
        self.assertIn("format=raw", flag(a, "-drive"))


class Aarch64(unittest.TestCase):
    def test_needs_uefi(self):
        with self.assertRaises(VortaxError):
            qemu.build_command(plan(arch="aarch64", accel="tcg"))

    def test_virt_machine(self):
        a = qemu.build_command(plan(arch="aarch64", accel="tcg", firmware="uefi", uefi_single="/fw.fd",
                                    iso_path="/i.iso", nic="virtio-net-pci"))
        self.assertEqual(a[0], "qemu-system-aarch64")
        self.assertEqual(flag(a, "-M"), "virt")
        self.assertIn("-bios", a)
        self.assertIn("usb-storage,drive=cd0,bootindex=0", a)


class Accel(unittest.TestCase):
    def test_tcg_request(self):
        self.assertEqual(qemu.detect_accel("x86_64", "tcg"), "tcg")

    def test_cross_arch_is_tcg(self):
        self.assertEqual(qemu.detect_accel("aarch64" if qemu.platform.machine() != "aarch64" else "x86_64"), "tcg")


if __name__ == "__main__":
    unittest.main()
