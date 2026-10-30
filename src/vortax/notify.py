# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Tell the user something finished: Termux notification, desktop notification, or a webhook (ntfy-style).

Always best effort: a failing notification must never break a VM operation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request


def send(title: str, text: str, url: str = "") -> list[str]:
    """Return the list of channels that accepted the message."""
    sent: list[str] = []
    for tool, argv in (("termux-notification", ["termux-notification", "--title", title, "--content", text]),
                       ("notify-send", ["notify-send", title, text])):
        if shutil.which(tool) and (tool != "notify-send" or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            try:
                if subprocess.run(argv, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                    sent.append(tool)
            except (OSError, subprocess.SubprocessError):
                pass
    url = url or os.environ.get("VORTAX_NOTIFY_URL", "")
    if url.startswith(("http://", "https://")):
        req = urllib.request.Request(url, data=text.encode("utf-8"), method="POST",
                                     headers={"Title": title.encode("ascii", "replace").decode(), "User-Agent": "vortax"})
        try:
            with urllib.request.urlopen(req, timeout=8):
                sent.append("webhook")
        except OSError:
            pass
    return sent
