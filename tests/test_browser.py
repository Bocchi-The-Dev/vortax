# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Drive the real dashboard in headless Chromium against the fake RFB server. Skipped without Playwright."""
import os
import threading
import unittest
from unittest import mock

from vortax import paths, session
from vortax.dashboard import server
from tests.fake_rfb import FakeRFB, H, W, pixel
from tests.helpers import Sandbox

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                           # pragma: no cover
    sync_playwright = None

TOKEN = "b" * 32


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class Browser(Sandbox):
    def test_console_renders_pixels_and_sends_input(self):
        vnc = FakeRFB("pw123456")
        self.addCleanup(vnc.close)
        inst = session.Instance("b1")
        paths.ensure_private_dir(inst.run)
        paths.write_private(inst.vnc_pass_file, "pw123456")
        inst.save_state({"name": "b1", "pid": os.getpid(), "system": "x", "disk_format": "qcow2",
                         "vnc": {"bind": "127.0.0.1", "port": vnc.port, "password": True}, "ram_mb": 64, "vcpus": 1})
        srv = server.make_server("127.0.0.1", 0, TOKEN)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)
        port = srv.server_address[1]
        errors = []
        with mock.patch.object(session, "_is_qemu", return_value=True), sync_playwright() as pw:
            browser = pw.chromium.launch()

            # Pass 1: the real Content-Security-Policy is active, so a blocked WebSocket or script shows up as an error.
            page = browser.new_page(viewport={"width": 1100, "height": 800})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(f"http://127.0.0.1:{port}/#token={TOKEN}")
            page.click("text=Console")
            page.wait_for_selector("text=fake-vm", timeout=15000)
            box = page.locator("#screen").bounding_box()
            page.mouse.move(box["x"] + box["width"] / 4, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.up()
            page.locator("#screen").focus()
            page.keyboard.press("Enter")
            page.keyboard.type("a")
            page.wait_for_timeout(500)
            self.assertIn("pointer", [e[0] for e in vnc.events])
            self.assertIn(("key", 1, 0xff0d), vnc.events)
            self.assertIn(("key", 1, ord("a")), vnc.events)
            page.click("text=Settings")
            page.wait_for_selector("#p_theme")
            page.select_option("#p_theme", "light")
            self.assertEqual(page.locator("html").get_attribute("data-theme"), "light")
            page.select_option("#p_lang", "ar")
            self.assertEqual(page.locator("html").get_attribute("dir"), "rtl")
            page.wait_for_selector("text=الإعدادات")
            self.assertTrue(page.locator("#foot a").get_attribute("href").startswith("https://github.com/"))
            page.close()

            # Pass 2: CSP bypassed only so the test can read canvas pixels with page.evaluate.
            ctx = browser.new_context(bypass_csp=True, viewport={"width": 1100, "height": 800})
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{port}/#token={TOKEN}")
            page.click("text=Console")
            page.wait_for_function("document.querySelector('#screen') && document.querySelector('#screen').width === %d" % W, timeout=15000)
            page.wait_for_function("""() => { const c = document.querySelector('#screen');
                return c.getContext('2d').getImageData(50, 30, 1, 1).data[2] === 255; }""", timeout=15000)
            px = page.evaluate("""() => { const g = document.querySelector('#screen').getContext('2d');
                const at = (x, y) => Array.from(g.getImageData(x, y, 1, 1).data); return [at(2, 2), at(10, 30), at(50, 30)]; }""")
            self.assertEqual([tuple(p[:3]) for p in px], [pixel(2, 2), pixel(10, 30), pixel(50, 30)])
            browser.close()
        self.assertEqual([e for e in errors if "favicon" not in e], [])


if __name__ == "__main__":
    unittest.main()
