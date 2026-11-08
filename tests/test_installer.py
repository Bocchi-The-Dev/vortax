# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""`vortax doctor --fix`. No test here installs anything: the package manager is always a fake."""
import contextlib
import io
import re
import unittest
from pathlib import Path
from unittest import mock

from vortax import cli, doctor, installer
from vortax.doctor import Check
from vortax.errors import VortaxError
from tests.helpers import Sandbox


def missing(*names, detail="not found"):
    return [Check(n, False, n.startswith("qemu-s") and "x86" in n or n == "qemu-img", detail, f"hint for {n}")
            for n in names]


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Planning(unittest.TestCase):
    def test_tiers_and_optional(self):
        checks = missing("qemu-system-x86_64", "qemu-img", "7z", "UEFI firmware (x86_64)", "zstd", "rclone")
        plan = installer.make_plan(checks, "apt")
        self.assertEqual(plan.packages, ["qemu-system-x86", "qemu-utils", "p7zip-full", "ovmf"])
        self.assertEqual(plan.skipped_optional, ["zstd", "rclone"])
        plan = installer.make_plan(checks, "apt", include_optional=True)
        self.assertEqual(plan.packages[-2:], ["zstd", "rclone"])
        self.assertEqual(plan.skipped_optional, [])

    def test_ok_checks_are_ignored(self):
        checks = [Check("qemu-img", True, True, "/usr/bin/qemu-img")]
        plan = installer.make_plan(checks, "apt", include_optional=True)
        self.assertEqual((plan.install, plan.manual), ([], []))

    def test_same_package_is_listed_once(self):
        plan = installer.make_plan(missing("qemu-system-x86_64", "qemu-img"), "brew")
        self.assertEqual(plan.packages, ["qemu"])

    def test_no_known_package_falls_back_to_the_hint(self):
        plan = installer.make_plan(missing("UEFI firmware (x86_64)"), "pkg")
        self.assertEqual(plan.install, [])
        self.assertEqual(plan.manual, [("UEFI firmware (x86_64)", "hint for UEFI firmware (x86_64)")])

    def test_third_party_tools_are_never_installed(self):
        plan = installer.make_plan(missing("bore", "ngrok", "tailscale"), "apt", include_optional=True)
        self.assertEqual(plan.install, [])
        self.assertEqual([n for n, _ in plan.manual], ["bore", "ngrok", "tailscale"])

    def test_kvm_is_only_mentioned_when_it_is_a_permission_problem(self):
        absent = installer.make_plan(missing("KVM acceleration", detail="absent: guests run under TCG"), "apt")
        self.assertEqual(absent.manual, [])
        denied = installer.make_plan(missing("KVM acceleration", detail="present but not accessible (add...)"), "apt")
        self.assertEqual([n for n, _ in denied.manual], ["KVM acceleration"])
        self.assertEqual(denied.install, [])

    def test_unknown_failing_check_keeps_its_hint(self):
        plan = installer.make_plan([Check("python >= 3.9", False, True, "3.8", "install a newer python3")], "apt")
        self.assertEqual(plan.manual, [("python >= 3.9", "install a newer python3")])


class Commands(unittest.TestCase):
    def plan(self, manager):
        return installer.make_plan(missing("qemu-system-x86_64", "qemu-img"), manager)

    def test_apt_refreshes_lists_then_installs_one_package_per_command(self):
        cmds = installer.commands(self.plan("apt"), ["sudo"])
        self.assertEqual(cmds, [["sudo", "apt-get", "update"],
                                ["sudo", "apt-get", "install", "-y", "qemu-system-x86"],
                                ["sudo", "apt-get", "install", "-y", "qemu-utils"]])

    def test_dnf_has_no_refresh_step(self):
        cmds = installer.commands(self.plan("dnf"), [])
        self.assertEqual(cmds[0], ["dnf", "install", "-y", "qemu-system-x86-core"])
        self.assertEqual(len(cmds), 2)

    def test_termux_and_brew_never_use_sudo(self):
        with mock.patch("os.geteuid", return_value=1000, create=True), \
                mock.patch("shutil.which", return_value="/usr/bin/sudo"):
            self.assertEqual(installer._privilege_prefix("pkg"), [])
            self.assertEqual(installer._privilege_prefix("brew"), [])
            self.assertEqual(installer._privilege_prefix("apt"), ["sudo"])

    def test_root_needs_no_prefix(self):
        with mock.patch("os.geteuid", return_value=0, create=True):
            self.assertEqual(installer._privilege_prefix("apt"), [])

    def test_no_sudo_and_not_root_is_an_error_but_dry_run_still_shows_commands(self):
        with mock.patch("os.geteuid", return_value=1000, create=True), mock.patch("shutil.which", return_value=None):
            with self.assertRaises(VortaxError):
                installer._privilege_prefix("apt")
            self.assertEqual(installer._safe_prefix("apt"), ["sudo"])


class Fix(unittest.TestCase):
    """installer.fix with a fake package manager that records what it was asked to run."""

    def setUp(self):
        self.ran = []
        self.fail_on = set()
        self.lines = []
        for p in (mock.patch.object(installer, "detect_manager", return_value="apt"),
                  mock.patch.object(installer, "_privilege_prefix", return_value=["sudo"]),
                  mock.patch.object(installer, "_run", side_effect=self._fake_run)):
            p.start()
            self.addCleanup(p.stop)

    def _fake_run(self, argv, manager):
        self.ran.append(argv)
        return 1 if argv[-1] in self.fail_on else 0

    def fix(self, checks, **kw):
        return installer.fix(checks, log=self.lines.append, **kw)

    def test_dry_run_runs_nothing_and_shows_the_commands(self):
        res = self.fix(missing("qemu-img"), dry_run=True)
        self.assertEqual(self.ran, [])
        self.assertFalse(res.ran)
        self.assertIn("  sudo apt-get install -y qemu-utils", self.lines)
        self.assertTrue(any("nothing was installed" in l for l in self.lines))

    def test_yes_installs_everything_in_order(self):
        res = self.fix(missing("qemu-system-x86_64", "qemu-img"), yes=True)
        self.assertEqual(self.ran, [["sudo", "apt-get", "update"],
                                    ["sudo", "apt-get", "install", "-y", "qemu-system-x86"],
                                    ["sudo", "apt-get", "install", "-y", "qemu-utils"]])
        self.assertEqual((res.installed, res.failed, res.ran), (["qemu-system-x86", "qemu-utils"], [], True))

    def test_one_failing_package_does_not_block_the_others(self):
        self.fail_on = {"qemu-system-x86"}
        res = self.fix(missing("qemu-system-x86_64", "qemu-img"), yes=True)
        self.assertEqual(res.failed, ["qemu-system-x86"])
        self.assertEqual(res.installed, ["qemu-utils"])
        self.assertTrue(any("could not install: qemu-system-x86" in l for l in self.lines))

    def test_failed_refresh_is_only_a_warning(self):
        self.fail_on = {"update"}
        res = self.fix(missing("qemu-img"), yes=True)
        self.assertEqual(res.installed, ["qemu-utils"])
        self.assertTrue(any(l.startswith("warning:") for l in self.lines))

    def test_asks_first_and_cancelling_installs_nothing(self):
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch.object(installer, "_confirm", return_value=False):
            res = self.fix(missing("qemu-img"))
        self.assertEqual(self.ran, [])
        self.assertFalse(res.ran)
        self.assertTrue(any("cancelled" in l for l in self.lines))

    def test_answering_yes_installs(self):
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch.object(installer, "_confirm", return_value=True):
            res = self.fix(missing("qemu-img"))
        self.assertEqual(res.installed, ["qemu-utils"])

    def test_without_a_terminal_it_refuses_instead_of_guessing(self):
        with mock.patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(VortaxError):
                self.fix(missing("qemu-img"))
        self.assertEqual(self.ran, [])

    def test_nothing_missing_means_no_commands(self):
        res = self.fix([Check("qemu-img", True, True, "/usr/bin/qemu-img")], yes=True)
        self.assertEqual((self.ran, res.ran), ([], False))

    def test_no_package_manager_is_a_clear_error(self):
        with mock.patch.object(installer, "detect_manager", return_value=None):
            with self.assertRaises(VortaxError):
                self.fix(missing("qemu-img"), yes=True)


class Detect(unittest.TestCase):
    def test_termux_wins_over_apt(self):
        with mock.patch("vortax.envinfo.is_termux", return_value=True), mock.patch("shutil.which", return_value="/x"):
            self.assertEqual(installer.detect_manager(), "pkg")

    def test_apt_then_others(self):
        have = {"apt-get": "/usr/bin/apt-get", "dnf": "/usr/bin/dnf"}
        with mock.patch("vortax.envinfo.is_termux", return_value=False), \
                mock.patch("shutil.which", side_effect=lambda n: have.get(n)):
            self.assertEqual(installer.detect_manager(), "apt")
            have.pop("apt-get")
            self.assertEqual(installer.detect_manager(), "dnf")
            have.clear()
            self.assertIsNone(installer.detect_manager())


class Table(unittest.TestCase):
    def test_table_is_well_formed(self):
        for name, (tier, by_manager) in installer._PACKAGES.items():
            self.assertIn(tier, (installer.REQUIRED, installer.RECOMMENDED, installer.OPTIONAL), name)
            for manager, pkg in by_manager.items():
                self.assertIn(manager, installer._COMMANDS, f"{name}: unknown manager {manager}")
                self.assertTrue(re.fullmatch(r"[a-z0-9][a-z0-9+._\-]*", pkg), f"{name}: odd package name {pkg!r}")

    def test_every_installable_check_exists_in_doctor(self):
        names = {c.name for c in doctor.run_checks()}
        # termux-wake-lock only exists on Termux
        for name in installer._PACKAGES:
            if name != "termux-wake-lock":
                self.assertIn(name, names, f"installer knows {name!r} but `doctor` has no such check")

    def test_required_tools_have_a_package_everywhere_we_claim_support(self):
        for tool in ("qemu-system-x86_64", "qemu-img"):
            self.assertEqual(set(installer._PACKAGES[tool][1]), set(installer._COMMANDS), tool)

    def test_apt_package_names_match_debian_control(self):
        control = Path(__file__).resolve().parent.parent / "debian" / "control"
        if not control.exists():
            self.skipTest("no debian/ directory in this tree")
        binary = control.read_text().split("Package: vortax", 1)[1]       # Depends / Recommends / Suggests
        for name, (_, by_manager) in installer._PACKAGES.items():
            pkg = by_manager.get("apt")
            if pkg:
                self.assertRegex(binary, rf"(?<![\w.+-]){re.escape(pkg)}(?![\w.+-])",
                                 f"{pkg} ({name}) is not in debian/control")


class Cli(Sandbox):
    def test_fix_with_json_is_rejected(self):
        code, _, err = run_cli("doctor", "--fix", "--json")
        self.assertEqual(code, 1)
        self.assertIn("--json", err)

    def test_modifier_flags_need_fix(self):
        for flag in ("--yes", "--dry-run", "--optional"):
            code, _, err = run_cli("doctor", flag)
            self.assertEqual(code, 1, flag)
            self.assertIn("--fix", err)

    def test_plain_doctor_is_unchanged(self):
        with mock.patch.object(installer, "fix") as fix:
            code, out, _ = run_cli("doctor")
        fix.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn("qemu-img", out)

    def test_fix_dry_run_through_the_cli(self):
        with mock.patch.object(installer, "detect_manager", return_value="apt"), \
                mock.patch.object(installer, "_privilege_prefix", return_value=["sudo"]), \
                mock.patch("vortax.installer._run") as run, \
                mock.patch("shutil.which", side_effect=lambda n: None if n in ("7z", "7zz", "7za") else "/usr/bin/" + n):
            code, out, _ = run_cli("doctor", "--fix", "--dry-run")
        run.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn("package manager: apt", out)
        self.assertIn("sudo apt-get install -y p7zip-full", out)

    def test_after_installing_it_checks_again(self):
        calls = []
        real = doctor.run_checks

        def counting():
            calls.append(1)
            return real()

        with mock.patch.object(doctor, "run_checks", side_effect=counting), \
                mock.patch.object(installer, "fix", return_value=installer.Result(installed=["x"], ran=True)):
            code, out, _ = run_cli("doctor", "--fix", "--yes")
        self.assertEqual(len(calls), 2)
        self.assertIn("checking again", out)


if __name__ == "__main__":
    unittest.main()
