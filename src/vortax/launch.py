# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Start a VM end to end: tunnel, host access program, saved settings.

Shared by the CLI and the dashboard so both do exactly the same thing.
"""

from __future__ import annotations

import shutil

from . import access, catalog, paths, session
from .errors import VortaxError
from .settings import Settings

Log = session.Log


def setup_access(s: Settings, inst: session.Instance, log: Log) -> None:
    if s.access not in ("rustdesk", "anydesk"):
        return
    if not shutil.which(s.access):
        log(f"warning: {s.access} is not installed; run `vortax access install {s.access}`")
        return
    pw = s.access_password
    if not pw:
        try:
            pw = inst.access_pass_file.read_text(encoding="utf-8").strip()
        except OSError:
            pw = ""
    if not pw:
        pw = session.new_password() + session.new_password()
    paths.write_private(inst.access_pass_file, pw)
    ident = access.set_access_password(s.access, pw)
    inst.update_state(access_id=ident)
    log(f"{s.access} unattended access configured (password: `vortax password --access`)")


def spawn_guard(inst: session.Instance) -> None:
    """Detached watcher that stops the VM when its --runtime deadline passes."""
    import subprocess
    import sys
    subprocess.Popen([sys.executable, "-m", "vortax", "_guard", "--name", inst.name], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def launch(s: Settings, resolved: catalog.Resolved, inst: session.Instance, prepared: session.Prepared,
           log: Log = print, guard: bool = True) -> dict:
    from . import envinfo
    if envinfo.is_termux() and envinfo.wake_lock(True):
        log("Termux wake lock acquired so Android keeps the VM alive")
    state, _argv = session.start(s, resolved, inst, prepared, log=log)
    session.save_settings(inst, s)
    log(f"started {inst.name} (pid {state['pid']}, accel {state['accel']})")
    if s.tunnel != "none" and s.access not in ("rustdesk", "anydesk"):
        log(f"opening {s.tunnel} tunnel")
        try:
            session.start_tunnel(inst, s.tunnel)
        except VortaxError:
            inst.stop(graceful=False)
            raise
    elif s.tunnel != "none":
        log(f"{s.access} connects out through its own relay; skipping the {s.tunnel} tunnel")
    if s.access in ("rdp", "nomachine"):
        log(f"wrote {access.write_launcher(state['vnc']['port'])}")
    setup_access(s, inst, log)
    if s.runtime and guard:
        spawn_guard(inst)
    inst._notify("started", inst.state().get("tunnel", {}).get("address", ""))
    return inst.state()
