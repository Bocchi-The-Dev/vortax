# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
import json
import unittest

from vortax import catalog, settings
from vortax.errors import VortaxError
from tests.helpers import Sandbox


class SettingsTests(Sandbox):
    def test_precedence_cli_over_env_over_config(self):
        cfg = {"vcpus": 3, "tunnel": "bore"}
        env = {"VORTAX_VCPUS": "4", "VORTAX_RAM_MB": "512"}
        s = settings.resolve({"vcpus": 8}, environ=env, config=cfg)
        self.assertEqual((s.vcpus, s.ram_mb, s.tunnel), (8, 512, "bore"))
        s = settings.resolve({}, environ=env, config=cfg)
        self.assertEqual(s.vcpus, 4)

    def test_env_drives_everything(self):
        s = settings.resolve({}, environ={"VORTAX_SYSTEM": "ubuntu", "VORTAX_BOOT_MODE": "iso", "VORTAX_NO_PASSWORD": "yes",
                                          "VORTAX_FORWARD": "tcp:2222:22,8080:80"}, config={})
        self.assertEqual((s.system, s.boot_mode, s.no_password), ("ubuntu", "iso", True))
        self.assertEqual(s.forward, ["tcp:2222:22", "8080:80"])

    def test_validation(self):
        for bad in ({"tunnel": "carrier-pigeon"}, {"vcpus": 0}, {"name": "../etc"}, {"nic": "e1000,evil=1"},
                    {"forward": ["tcp:99999:22"]}, {"extra_qemu_args": "'unterminated"}):
            with self.assertRaises(VortaxError, msg=str(bad)):
                settings.resolve(bad, environ={}, config={})

    def test_unknown_config_key_is_rejected(self):
        with self.assertRaises(VortaxError):
            settings.resolve({}, environ={}, config={"colour": "blue"})

    def test_passwords_never_appear_in_repr(self):
        s = settings.resolve({}, environ={"VORTAX_PASSWORD": "hunter22", "VORTAX_ACCESS_PASSWORD": "swordfish"}, config={})
        self.assertNotIn("hunter22", repr(s))
        self.assertNotIn("swordfish", json.dumps(s.redacted()))

    def test_secrets_file_is_private(self):
        settings.set_secret("ngrok_authtoken", "tok")
        mode = settings.secrets_path().stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.assertEqual(settings.get_secret("ngrok_authtoken"), "tok")
        with self.assertRaises(VortaxError):
            settings.set_secret("nope", "x")


class CatalogTests(Sandbox):
    def test_every_builtin_loads_and_profiles_exist(self):
        systems, profiles = catalog.load_systems(), catalog.load_profiles()
        self.assertGreater(len(systems), 25)
        for s in systems.values():
            if s.profile:
                self.assertIn(s.profile, profiles)

    def test_alias_and_case_insensitive_lookup(self):
        self.assertEqual(catalog.find_system("bazite").id, "bazzite")
        self.assertEqual(catalog.find_system("xp").id, "XP")

    def test_unknown_system_suggests(self):
        with self.assertRaisesRegex(VortaxError, "Did you mean"):
            catalog.find_system("ubunt")

    def test_blank_slots_fail_loudly_not_silently_default(self):
        with self.assertRaisesRegex(VortaxError, "template slot"):
            catalog.resolve(settings.resolve({"system": "win98"}, environ={}, config={}))
        with self.assertRaisesRegex(VortaxError, "template slot"):
            catalog.resolve(settings.resolve({"system": "win10"}, environ={}, config={}))

    def test_no_copyrighted_windows_links_are_bundled(self):
        for s in catalog.load_systems().values():
            if s.family in ("windows", "retro"):
                self.assertEqual(s.modes(), [], s.id)

    def test_auto_mode_prefers_img_then_qcow2_then_iso(self):
        self.assertEqual(catalog.resolve(settings.resolve({"system": "batocera"}, environ={}, config={})).mode, "img")
        self.assertEqual(catalog.resolve(settings.resolve({"system": "ubuntu"}, environ={}, config={})).mode, "qcow2")
        self.assertEqual(catalog.resolve(settings.resolve({"system": "bazzite"}, environ={}, config={})).mode, "iso")

    def test_missing_mode_names_what_is_available(self):
        with self.assertRaisesRegex(VortaxError, "available modes: iso"):
            catalog.resolve(settings.resolve({"system": "bazzite", "boot_mode": "qcow2"}, environ={}, config={}))

    def test_profile_comes_from_system_and_source_overrides_catalog(self):
        r = catalog.resolve(settings.resolve({"system": "win98", "source": "/tmp/x.iso"}, environ={}, config={}))
        self.assertEqual((r.profile_name, r.source), ("win98", "/tmp/x.iso"))

    def test_user_overrides_fill_a_blank_slot(self):
        (self.tmp / "config").mkdir()
        (self.tmp / "config" / "systems.json").write_text(json.dumps(
            {"systems": [{"id": "win10", "name": "Windows 10", "family": "windows", "sources": {"iso": "https://example.test/w10.iso"}}]}))
        r = catalog.resolve(settings.resolve({"system": "win10"}, environ={}, config={}))
        self.assertEqual(r.source, "https://example.test/w10.iso")
        self.assertEqual(len([s for s in catalog.load_systems() if s.lower() == "win10"]), 1)

    def test_bad_user_catalog_is_a_clear_error(self):
        (self.tmp / "config").mkdir()
        (self.tmp / "config" / "systems.json").write_text("{not json")
        with self.assertRaisesRegex(VortaxError, "cannot read"):
            catalog.load_systems()


if __name__ == "__main__":
    unittest.main()
