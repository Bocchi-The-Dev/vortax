# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Run the dashboard console's audio client against byte-exact QEMU messages. Skipped without Node."""
import shutil
import subprocess
import unittest
from pathlib import Path

NODE = shutil.which("node")


@unittest.skipIf(NODE is None, "node not installed")
class AudioClient(unittest.TestCase):
    def test_protocol_and_playback(self):
        script = Path(__file__).with_name("audio_client_check.js")
        r = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL PASSED", r.stdout)


if __name__ == "__main__":
    unittest.main()
