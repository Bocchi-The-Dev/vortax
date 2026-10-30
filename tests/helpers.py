# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
import os
import shutil
import tempfile
import unittest
from pathlib import Path

STUBS = str(Path(__file__).parent / "stubs")


class Sandbox(unittest.TestCase):
    """Isolated data/config dirs and the fake qemu tools first on PATH."""
    use_stubs = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vortax-test-"))
        self._env = dict(os.environ)
        os.environ["VORTAX_HOME"] = str(self.tmp / "data")
        os.environ["VORTAX_CONFIG"] = str(self.tmp / "config")
        for k in [k for k in os.environ if k.startswith("VORTAX_") and k not in ("VORTAX_HOME", "VORTAX_CONFIG")]:
            del os.environ[k]
        if self.use_stubs:
            os.environ["PATH"] = STUBS + os.pathsep + os.environ["PATH"]
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)
