# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Vortax contributors. See LICENSE.
"""Command line interface."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __source_url__, __version__, access, catalog, disk, doctor, envinfo, fetch, isobuild, launch, paths, presets, qemu, selftest, session, settings
from .errors import VortaxError

DESCRIPTION = """\
Run and manage QEMU virtual machines: pick a system from the catalog (or bring your own
ISO/disk), start it, reach it over VNC, optionally through a tunnel, snapshot and export it.

Every run setting also reads a VORTAX_<NAME> environment variable (VORTAX_SYSTEM,
VORTAX_TUNNEL, VORTAX_PASSWORD, ...). Command line beats environment beats config.json.
"""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- argument plumbing


def _add_run_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("what to run")
    g.add_argument("target", nargs="?", help="a preset name, a catalog id, or a local ISO/disk path")
    g.add_argument("--preset", help="saved preset to start from, see `vortax preset list`")
    g.add_argument("--name", help="VM name; names its disk and state (default: vortax)")
    g.add_argument("-s", "--system", help="catalog id, see `vortax list`")
    g.add_argument("-m", "--boot-mode", choices=settings.BOOT_MODES, help="iso, qcow2 or img (default: auto)")
    g.add_argument("--source", help="URL or local path to an ISO, disk image or archive (overrides the catalog)")
    g = p.add_argument_group("hardware")
    g.add_argument("--profile", help="hardware preset, see `vortax profiles`")
    g.add_argument("--arch", choices=settings.ARCHES)
    g.add_argument("--accel", choices=settings.ACCEL, help="auto uses KVM when available, else TCG")
    g.add_argument("--firmware", choices=settings.FIRMWARE)
    g.add_argument("--boot", choices=settings.BOOT_ORDER, help="disk ignores the installer ISO (use after installing)")
    g.add_argument("--ram-mb", type=int)
    g.add_argument("--vcpus", type=int)
    g.add_argument("--disk-gb", type=int, help="size for a new blank disk (ISO installs)")
    g.add_argument("--nic", help=f"network card model ({', '.join(settings.NICS)})")
    g.add_argument("--gpu-mode", choices=settings.GPU_MODES, help="batocera profile: display device to try")
    g.add_argument("--floppy-url", help="floppy image (.ima) URL or path for retro profiles")
    g.add_argument("--bios-url", help="custom SeaBIOS build, BIOS mode only")
    g.add_argument("--virtio-iso", "--driver-iso", help="an extra CD to attach: virtio-win.iso or an ISO of drivers you made")
    g.add_argument("--forward", action="append", metavar="[tcp|udp:]HOST:GUEST",
                   help="forward a host port (bound to 127.0.0.1) to the guest; repeatable")
    g.add_argument("--share", action="append", metavar="PATH[:TAG]",
                   help="share a host folder with the guest over 9p (mount_tag TAG); repeatable")
    g.add_argument("--usb", action="append", metavar="VID:PID", help="pass a host USB device through; repeatable")
    g.add_argument("--audio", choices=settings.AUDIO, help="emulated sound card (default none)")
    g.add_argument("--mttcg", action="store_true", default=None,
                   help="without KVM, let several vCPUs run on several host cores (experimental: may crash x86 guests on ARM)")
    g.add_argument("--ssh", action="store_true", default=None, help="forward a local port to the guest's SSH (22)")
    g.add_argument("--snapshot-on-stop", action="store_true", default=None,
                   help="save the live state on stop and resume it on next start (qcow2 only)")
    g.add_argument("--notify", action="store_true", default=None, help="send a notification on start/stop")
    g.add_argument("--extra-qemu-args", help="raw QEMU arguments appended verbatim")
    g = p.add_argument_group("access")
    g.add_argument("--vnc-display", type=int, help="VNC display number; port = 5900 + n (default: first free)")
    g.add_argument("--vnc-bind", help="address VNC listens on (default 127.0.0.1)")
    g.add_argument("--no-password", action="store_true", default=None,
                   help="VNC without a password (only allowed on 127.0.0.1)")
    g.add_argument("--tunnel", choices=settings.TUNNELS, help="expose VNC through bore, ngrok or tailscale")
    g.add_argument("--access", choices=settings.ACCESS, help="host remote-access program (default: vnc)")
    g.add_argument("--runtime", type=int, help="minutes before `run` stops the VM (0 = until Ctrl-C)")


_LIST_FIELDS = ("forward", "shares", "usb")
_BOOL_FIELDS = ("no_password", "ssh", "snapshot_on_stop", "notify", "mttcg")


def _apply_target(ns: argparse.Namespace) -> dict:
    """Positional target: preset name, catalog id or a local file. Returns preset values (config layer)."""
    preset = {}
    target = getattr(ns, "target", None)
    if getattr(ns, "preset", None):
        preset = dict(presets.get(ns.preset))
    if target:
        if target in presets.load() and not getattr(ns, "preset", None):
            preset = dict(presets.get(target))
        else:
            try:
                catalog.find_system(target)
                ns.system = ns.system or target
            except VortaxError:
                if os.path.exists(os.path.expanduser(target)) or target.startswith(("http://", "https://")):
                    ns.source = ns.source or target
                else:
                    raise
    return preset


def _settings_from(ns: argparse.Namespace) -> settings.Settings:
    preset = _apply_target(ns)
    ns.share = getattr(ns, "share", None)
    cli = {f: getattr(ns, f, None) for f in settings.Settings.__dataclass_fields__}
    cli["shares"] = ns.share
    for f in _BOOL_FIELDS:
        if cli.get(f) is None:
            cli.pop(f, None)
    for f in _LIST_FIELDS:
        if not cli.get(f):
            cli[f] = None
    # `vortax start --name X` with nothing else brings back how X was last launched.
    name = cli.get("name") or os.environ.get("VORTAX_NAME") or "vortax"
    saved = {}
    if not (cli.get("system") or cli.get("source") or preset or os.environ.get("VORTAX_SYSTEM") or os.environ.get("VORTAX_SOURCE")):
        try:
            saved = session.load_saved_settings(session.Instance(name))
        except VortaxError:
            saved = {}
    config = {**settings.load_config_file(), **saved, **preset} if (saved or preset) else None
    return settings.resolve(cli, config=config)


def _build(ns: argparse.Namespace):
    s = _settings_from(ns)
    resolved = catalog.resolve(s)
    return s, resolved, session.Instance(s.name)


def _instance_arg(ns: argparse.Namespace) -> session.Instance:
    name = getattr(ns, "name", None) or os.environ.get("VORTAX_NAME") or "vortax"
    return session.Instance(name)


# ---------------------------------------------------------------- commands


def cmd_list(ns) -> int:
    systems = catalog.load_systems()
    rows = [s for s in systems.values() if not ns.family or s.family == ns.family]
    if ns.json:
        print(json.dumps([s.to_dict() for s in rows], indent=2))
        return 0
    width = max((len(s.id) for s in rows), default=4)
    print(f"{'ID'.ljust(width)}  {'FAMILY':8} {'MODES':16} NAME")
    for s in rows:
        modes = ",".join(s.modes()) or "bring-your-own"
        print(f"{s.id.ljust(width)}  {s.family:8} {modes:16} {s.name}")
    return 0


def cmd_profiles(ns) -> int:
    profiles = catalog.load_profiles()
    if ns.json:
        print(json.dumps(profiles, indent=2))
        return 0
    for key, p in sorted(profiles.items()):
        print(f"{key:10} {p.get('name', '')}")
        if p.get("notes"):
            print(f"{'':10} {p['notes']}")
    return 0


def _guess_prepared(inst: session.Instance, resolved: catalog.Resolved) -> session.Prepared:
    mode = resolved.mode
    if mode == "auto" and fetch.is_local(resolved.source):
        p = fetch.local_path(resolved.source)
        if p.is_file():
            from . import extract
            mode = {"iso": "iso", "qcow2": "qcow2"}.get(extract.sniff(p), "qcow2")
    if mode == "iso":
        return session.Prepared(paths.disks_dir() / f"{inst.name}.qcow2", "qcow2", inst.iso)
    if mode == "img":
        return session.Prepared(paths.disks_dir() / f"{inst.name}.img", "raw")
    return session.Prepared(paths.disks_dir() / f"{inst.name}.qcow2", "qcow2")


def cmd_plan(ns) -> int:
    s, resolved, inst = _build(ns)
    prepared = _guess_prepared(inst, resolved)
    existing = inst.existing_disk()
    if existing and not ns.rebuild:
        prepared = session.Prepared(existing, "raw" if existing.suffix == ".img" else "qcow2",
                                    inst.iso if inst.iso.exists() else None)
    if s.floppy_url:
        prepared.floppy_path = inst.floppy
    if s.bios_url and s.firmware == "bios":
        prepared.bios_path = inst.bios
    warnings: list[str] = []
    _state, argv = session.start(s, resolved, inst, prepared, log=warnings.append, dry_run=True)
    steps = []
    if not (existing and not ns.rebuild):
        steps.append(f"download {resolved.source}" if not fetch.is_local(resolved.source)
                     else f"use local file {resolved.source}")
        steps.append("detect the file type, extract if archived, place the disk/ISO")
    else:
        steps.append(f"reuse existing disk {existing.name}")
    steps.append("start QEMU" + (f", then open a {s.tunnel} tunnel" if s.tunnel != "none" else ""))
    out = {
        "name": inst.name, "system": resolved.system.id if resolved.system else "", "source": resolved.source,
        "mode": resolved.mode, "profile": resolved.profile_name, "arch": resolved.arch,
        "steps": steps, "qemu": qemu.command_line(argv), "argv": argv,
        "warnings": [w.replace("warning: ", "") for w in warnings],
        "settings": s.redacted(),
    }
    if ns.json:
        print(json.dumps(out, indent=2))
        return 0
    print(f"VM:       {inst.name}")
    if resolved.system:
        print(f"System:   {resolved.system.name} ({resolved.system.id})")
    print(f"Source:   {resolved.source}  [{resolved.mode}]")
    print(f"Profile:  {resolved.profile_name or 'none'}    Arch: {resolved.arch}")
    print("Steps:")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}")
    print("QEMU command:\n  " + out["qemu"])
    for w in out["warnings"]:
        print(f"warning: {w}")
    return 0


def _prepare_for(ns, s, resolved, inst) -> session.Prepared:
    return session.prepare(s, resolved, inst, force=bool(getattr(ns, "rebuild", False)), log=log)


def cmd_prepare(ns) -> int:
    s, resolved, inst = _build(ns)
    prep = _prepare_for(ns, s, resolved, inst)
    print(f"ready: {prep.disk_path} ({prep.disk_format}, {disk.human(disk.size_bytes(prep.disk_path))})")
    return 0


def _print_info(inst: session.Instance, show_password: bool) -> None:
    st = inst.status()
    if not st["running"]:
        print(f"{inst.name}: not running")
        return
    vnc = st["vnc"]
    print(f"{inst.name}: running (pid {st['pid']}, {st['accel']}, {st['ram_mb']} MB, {st['vcpus']} vCPU)")
    print(f"  VNC (local):   {vnc['bind']}:{vnc['port']}")
    t = st["tunnel"]
    if t and t.get("address"):
        print(f"  VNC ({t['kind']}):  {t['address']}")
    if vnc.get("password"):
        if show_password:
            print(f"  VNC password:  {inst.vnc_password()}")
        else:
            print("  VNC password:  hidden (run `vortax password`)")
    else:
        print("  VNC password:  none (loopback only)")
    if inst.ssh_port():
        print(f"  SSH:           vortax ssh   (127.0.0.1:{inst.ssh_port()})")
    if st["deadline"]:
        print(f"  stops at:      {st['deadline']}")
    for key, label in (("rustdesk", "RustDesk"), ("anydesk", "AnyDesk")):
        if st.get("access") == key and st.get("access_id"):
            print(f"  {label} ID:    {st['access_id']}")


def cmd_start(ns) -> int:
    s, resolved, inst = _build(ns)
    prep = _prepare_for(ns, s, resolved, inst)
    launch.launch(s, resolved, inst, prep, log=log)
    if s.runtime:
        log(f"a background watcher will stop the VM after {s.runtime} min")
    _print_info(inst, ns.show_password)
    return 0


def cmd_run(ns) -> int:
    s, resolved, inst = _build(ns)
    prep = _prepare_for(ns, s, resolved, inst)
    launch.launch(s, resolved, inst, prep, log=log, guard=bool(ns.detach))
    _print_info(inst, ns.show_password)
    if ns.detach:
        return 0
    deadline = time.monotonic() + s.runtime * 60 if s.runtime else None
    stop_requested = []
    signal.signal(signal.SIGTERM, lambda *_: stop_requested.append(1))
    log("running; press Ctrl-C to stop" + (f" (auto-stop in {s.runtime} min)" if s.runtime else ""))
    try:
        while inst.running() and not stop_requested:
            if deadline and time.monotonic() >= deadline:
                log("runtime elapsed")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        log("interrupted")
    if inst.running():
        inst.stop(graceful=True, timeout=ns.grace, log=log)
    if s.notify:
        from . import notify
        notify.send(f"vortax: {inst.name} stopped", "")
    log("stopped")
    return 0


def cmd_stop(ns) -> int:
    inst = _instance_arg(ns)
    if not inst.running():
        inst.stop(graceful=False)       # also tears down a leftover tunnel
        print(f"{inst.name} is not running")
        return 0
    inst.stop(graceful=not ns.force, timeout=ns.grace, log=log)
    print(f"{inst.name} stopped")
    return 0


def cmd_kill(ns) -> int:
    inst = _instance_arg(ns)
    inst.kill()
    print(f"{inst.name} killed")
    return 0


def cmd_reboot(ns) -> int:
    inst = _instance_arg(ns)
    inst.reboot()
    print(f"{inst.name} reset")
    return 0


def cmd_status(ns) -> int:
    names = [ns.name] if ns.name else session.list_instances()
    rows = [session.Instance(n).status() for n in names]
    if ns.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no VMs yet; try `vortax list` then `vortax run --system <id>`")
        return 0
    for r in rows:
        state = "running" if r["running"] else "stopped"
        extra = f"  vnc :{r['vnc'].get('port')}" if r["running"] and r["vnc"] else ""
        print(f"{r['name']:16} {state:8} {r['system'] or '-':14} {disk.human(r['disk_size']):>10}{extra}")
    return 0


def cmd_info(ns) -> int:
    _print_info(_instance_arg(ns), ns.show_password)
    return 0


def cmd_password(ns) -> int:
    inst = _instance_arg(ns)
    if ns.access:
        try:
            print(inst.access_pass_file.read_text(encoding="utf-8").strip())
        except OSError:
            raise VortaxError("no RustDesk/AnyDesk password has been generated for this VM yet") from None
        return 0
    if ns.set or ns.new:
        if ns.new:
            pw = session.new_password()
        elif sys.stdin.isatty():
            pw = getpass.getpass("new VNC password: ")
            if pw != getpass.getpass("again: "):
                raise VortaxError("passwords do not match")
        else:
            pw = sys.stdin.readline().rstrip("\n")
        inst.set_vnc_password(pw, log=log)
        log("password updated" + (" and applied to the running VM" if inst.running() else " (applies on next start)"))
        if ns.new:
            print(inst.vnc_password())
        return 0
    pw = inst.vnc_password()
    if not pw:
        raise VortaxError("no VNC password is set for this VM")
    print(pw)
    return 0


def cmd_snapshot(ns) -> int:
    inst = _instance_arg(ns)
    out = inst.snapshot(ns.action, ns.tag or "")
    if out:
        print(out)
    return 0


def cmd_export(ns) -> int:
    inst = _instance_arg(ns)
    out = session.export(inst, Path(ns.out) if ns.out else None, ns.rclone or "", log=log)
    print(out)
    return 0


def cmd_tunnel(ns) -> int:
    inst = _instance_arg(ns)
    if ns.action == "stop":
        from . import tunnels
        tunnels.stop(inst.state().get("tunnel"))
        inst.update_state(tunnel={})
        print("tunnel stopped")
        return 0
    t = session.start_tunnel(inst, ns.kind)
    print(f"{t['kind']}: {t['address']}")
    return 0


def cmd_edit(ns) -> int:
    inst = _instance_arg(ns)
    changes = {k: getattr(ns, k) for k in session.VM_EDITABLE if getattr(ns, k, None) is not None}
    values = session.edit_settings(inst, changes)
    width = max(len(k) for k in values)
    for k, v in values.items():
        print(f"  {k.ljust(width)}  {v!r}")
    if not changes:
        print("nothing changed; give options such as --audio es1370 --vcpus 2 --mttcg")
        return 0
    print(f"saved for {inst.name}; the disk was not touched")
    if inst.running():
        print(f"{inst.name} is running: it picks this up after a restart "
              f"(vortax stop --name {inst.name}, then vortax start --name {inst.name})")
    else:
        print(f"applies the next time you run: vortax start --name {inst.name}")
    return 0


def cmd_driver_iso(ns) -> int:
    src = Path(ns.folder).expanduser().resolve()
    out = Path(ns.out).expanduser().resolve() if ns.out else src.with_name(src.name + ".iso")
    iso = isobuild.build(src, out)
    print(f"made {iso} ({iso.stat().st_size / 1e6:.1f} MB)")
    inst = _instance_arg(ns)
    if ns.attach:
        session.edit_settings(inst, {"virtio_iso": str(iso)})
        print(f"attached to {inst.name}: it shows up as a CD drive after the next start")
        if inst.running():
            print(f"{inst.name} is running: restart it (vortax stop --name {inst.name}, then vortax start --name {inst.name})")
    else:
        print(f"attach it with: vortax edit --name {inst.name} --driver-iso {iso}")
    return 0


def cmd_rm(ns) -> int:
    inst = _instance_arg(ns)
    if inst.running():
        raise VortaxError(f"{inst.name} is running; stop it first")
    targets = [p for p in (*inst.disk_candidates(), inst.iso, inst.floppy, inst.bios) if p.exists() or p.is_symlink()]
    targets.append(inst.run)
    if not ns.yes:
        print("would delete:")
        for t in targets:
            print(f"  {t}")
        print("re-run with --yes to delete")
        return 1
    for t in targets:
        if t.is_dir() and not t.is_symlink():
            shutil.rmtree(t, ignore_errors=True)
        else:
            t.unlink(missing_ok=True)
    print(f"removed {inst.name}")
    return 0


def cmd_access(ns) -> int:
    if ns.action == "list":
        for p in access.PROGRAMS.values():
            print(f"{p.key:10} {p.label}  ports: {', '.join(p.ports)}")
        return 0
    if ns.action == "launcher":
        inst = _instance_arg(ns)
        port = inst.state().get("vnc", {}).get("port", 5900)
        print(access.write_launcher(int(port)))
        return 0
    if not ns.program:
        raise VortaxError("name a program: vortax access install <rdp|nomachine|rustdesk|anydesk>")
    access.run_install(ns.program, dry_run=ns.dry_run, allow_unverified=ns.allow_unverified, log=print)
    return 0


def cmd_doctor(ns) -> int:
    checks = doctor.run_checks()
    if ns.json:
        print(json.dumps([c.to_dict() for c in checks], indent=2))
    else:
        print(doctor.format_report(checks))
    return doctor.exit_code(checks)


def cmd_selftest(ns) -> int:
    problems = selftest.check_plans()
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        return 1
    print("ok: catalog, profiles and command builder are consistent")
    if ns.boot:
        selftest.boot_test(timeout=ns.timeout, log=lambda m: print(f"ok: {m}"))
        print("ok: end-to-end boot test passed")
    return 0


def cmd_check_links(ns) -> int:
    bad = 0
    for s in catalog.load_systems().values():
        for mode, url in s.sources.items():
            if not url:
                continue
            code, final = fetch.head_status(url)
            good = 200 <= code < 400
            bad += 0 if good else 1
            print(f"{'ok  ' if good else 'FAIL'} {code:3} {s.id}/{mode}  {url}")
    return 1 if bad else 0


def cmd_config(ns) -> int:
    if ns.action == "path":
        print(f"data:   {paths.data_dir()}\nconfig: {paths.config_dir()}")
        return 0
    s = settings.resolve()
    print(json.dumps(s.redacted(), indent=2))
    return 0


def cmd_secret(ns) -> int:
    if sys.stdin.isatty():
        value = getpass.getpass(f"{ns.name}: ")
    else:
        value = sys.stdin.readline().rstrip("\n")
    if not value:
        raise VortaxError("empty value; nothing saved")
    settings.set_secret(ns.name, value)
    print(f"saved {ns.name} to {settings.secrets_path()} (mode 600)")
    return 0


def cmd_logs(ns) -> int:
    inst = _instance_arg(ns)
    f = inst.log_file
    if not f.exists():
        raise VortaxError(f"no log for {inst.name} yet")
    print(session.tail(f, ns.lines), flush=True)
    if not ns.follow:
        return 0
    try:
        with open(f, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            while True:
                chunk = fh.read()
                if chunk:
                    sys.stdout.write(chunk.decode("utf-8", "replace"))
                    sys.stdout.flush()
                elif ns.until_stop and not inst.running():
                    break
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_guard(ns) -> int:
    """Hidden: stop a VM when its runtime deadline passes (spawned by `start --runtime`)."""
    inst = _instance_arg(ns)
    st = inst.state()
    minutes = int(st.get("runtime") or 0)
    if not minutes:
        return 0
    started = st.get("pid")
    end = time.time() + (minutes * 60 if not ns.deadline_epoch else max(0, ns.deadline_epoch - time.time()))
    while time.time() < end:
        if not inst.running() or inst.state().get("pid") != started:
            return 0
        time.sleep(min(5, max(0.1, end - time.time())))
    if inst.running() and inst.state().get("pid") == started:
        inst.stop(graceful=True, timeout=30.0)
        inst._notify("stopped", "runtime elapsed")
    return 0


def cmd_preset(ns) -> int:
    if ns.action == "list":
        data = presets.load()
        if not data:
            print("no presets yet; `vortax preset save NAME --system alpine --ram-mb 2048`")
        for name, v in sorted(data.items()):
            print(f"{name:16} {', '.join(f'{k}={v[k]}' for k in sorted(v))}")
        return 0
    if not ns.preset_name:
        raise VortaxError(f"vortax preset {ns.action}: give a preset name")
    if ns.action == "show":
        print(json.dumps(presets.get(ns.preset_name), indent=2))
    elif ns.action == "rm":
        presets.remove(ns.preset_name)
        print(f"removed preset {ns.preset_name}")
    else:
        ns.target = None
        s = _settings_from(ns)
        raw = {k: getattr(s, k) for k in settings.Settings.__dataclass_fields__
               if getattr(ns, k, None) not in (None, [], False, "") or (k == "shares" and ns.share)}
        raw["shares"] = ns.share or raw.get("shares")
        saved = presets.save(ns.preset_name, {k: v for k, v in raw.items() if v})
        print(f"saved preset {ns.preset_name}: {', '.join(sorted(saved))}")
    return 0


def cmd_disk(ns) -> int:
    inst = _instance_arg(ns)
    d = inst.existing_disk()
    if not d:
        raise VortaxError(f"{inst.name} has no disk yet; run `vortax prepare` first")
    if ns.action == "info":
        i = disk.info(d)
        print(f"{d}\n  format: {i.get('format')}\n  virtual size: {disk.human(i.get('virtual-size', 0))}"
              f"\n  on disk: {disk.human(disk.size_bytes(d))}")
        return 0
    if inst.running():
        raise VortaxError(f"{inst.name} is running; stop it before changing its disk")
    if ns.action == "resize":
        if not ns.value:
            raise VortaxError("give a size, e.g. `vortax disk resize +20G`")
        disk.resize(d, ns.value)
        print(f"resized {d.name} ({ns.value}); grow the partition inside the guest to use it")
    elif ns.action == "compact":
        if d.suffix != ".qcow2":
            raise VortaxError("compact only works on qcow2 disks")
        before, after = disk.compact(d)
        print(f"{disk.human(before)} -> {disk.human(after)}")
    return 0


def _ssh_target(ns) -> tuple[session.Instance, int]:
    inst = _instance_arg(ns)
    if not inst.running():
        raise VortaxError(f"{inst.name} is not running")
    port = inst.ssh_port()
    if not port:
        raise VortaxError("this VM was started without --ssh; restart it with `vortax start --ssh`")
    return inst, port


def cmd_ssh(ns) -> int:
    _inst, port = _ssh_target(ns)
    exe = shutil.which("ssh")
    if not exe:
        raise VortaxError("ssh is not installed" + (f" ({envinfo.termux_install_command(['ssh'])})" if envinfo.is_termux() else ""))
    argv = [exe, "-p", str(port), "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=" + str(paths.data_dir() / "known_hosts"), f"{ns.user}@127.0.0.1", *ns.command]
    if ns.dry_run:
        print(" ".join(argv))
        return 0
    os.execv(exe, argv)
    return 0


def cmd_cp(ns) -> int:
    """`vortax cp ./file vm:/tmp/` or `vortax cp vm:/etc/os-release .`"""
    _inst, port = _ssh_target(ns)
    exe = shutil.which("scp")
    if not exe:
        raise VortaxError("scp is not installed (install openssh)")

    def conv(x: str) -> str:
        return f"{ns.user}@127.0.0.1:{x[3:]}" if x.startswith("vm:") else x
    if not (ns.src.startswith("vm:") or ns.dst.startswith("vm:")):
        raise VortaxError("one side must start with vm: (the guest), e.g. vm:/home/user/")
    argv = [exe, "-r", "-P", str(port), "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=" + str(paths.data_dir() / "known_hosts"), conv(ns.src), conv(ns.dst)]
    if ns.dry_run:
        print(" ".join(argv))
        return 0
    return subprocess_call(argv)


def subprocess_call(argv: list[str]) -> int:
    import subprocess
    return subprocess.call(argv)


def cmd_screenshot(ns) -> int:
    inst = _instance_arg(ns)
    out = Path(ns.out) if ns.out else Path.cwd() / f"{inst.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    print(inst.screenshot(out))
    return 0


def cmd_wizard(ns) -> int:
    ask = lambda q, d="": (input(f"{q}{f' [{d}]' if d else ''}: ").strip() or d)
    systems = [s for s in catalog.load_systems().values() if s.modes()]
    print("Pick a system:")
    for i, s in enumerate(systems, 1):
        print(f"  {i:2}. {s.id:16} {s.name}")
    pick = ask("number or id", "1")
    try:
        system = systems[int(pick) - 1].id if pick.isdigit() else catalog.find_system(pick).id
    except (IndexError, VortaxError):
        raise VortaxError(f"not a valid choice: {pick}") from None
    name = ask("VM name", system)
    ram = ask("RAM in MB (empty = automatic)")
    tunnel = ask(f"tunnel ({'/'.join(settings.TUNNELS)})", "none")
    save = ask("save these choices as a preset? name or empty")
    args = ["--system", system, "--name", name, "--tunnel", tunnel] + (["--ram-mb", ram] if ram else [])
    if save:
        pns = build_parser().parse_args(["preset", "save", save, *args])
        pns.fn(pns)
    if ask("start now? (y/n)", "y").lower().startswith("y"):
        sns = build_parser().parse_args(["start", *args])
        return sns.fn(sns)
    return 0


def cmd_dashboard(ns) -> int:
    from .dashboard import server
    return server.serve(ns.host, ns.port, open_browser=ns.open)


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vortax", description=DESCRIPTION,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"vortax {__version__}")
    p.add_argument("--license", action="store_true", help="show the license and where to get the source")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    def add(name, fn, help_, **kw):
        sp = sub.add_parser(name, help=help_, description=help_, **kw)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("list", cmd_list, "show the system catalog")
    sp.add_argument("--family", choices=["linux", "windows", "android", "game", "retro"])
    sp.add_argument("--json", action="store_true")

    sp = add("profiles", cmd_profiles, "show hardware presets")
    sp.add_argument("--json", action="store_true")

    sp = add("plan", cmd_plan, "show what a run would do and the exact QEMU command (no changes made)")
    _add_run_options(sp)
    sp.add_argument("--rebuild", action="store_true")
    sp.add_argument("--json", action="store_true")

    sp = add("prepare", cmd_prepare, "download and set up the disk without starting the VM")
    _add_run_options(sp)
    sp.add_argument("--rebuild", action="store_true", help="discard an existing disk and start over")

    for name, fn, help_ in (("start", cmd_start, "prepare if needed, start the VM in the background"),
                            ("run", cmd_run, "prepare, start, and stay in the foreground until Ctrl-C or --runtime")):
        sp = add(name, fn, help_)
        _add_run_options(sp)
        sp.add_argument("--rebuild", action="store_true", help="discard an existing disk and start over")
        sp.add_argument("--show-password", action="store_true", help="print the VNC password after starting")
        if name == "run":
            sp.add_argument("--detach", action="store_true", help="return right after starting (same as `start`)")
            sp.add_argument("--grace", type=float, default=30.0, help="seconds to wait for a clean guest shutdown")

    def named(sp):
        sp.add_argument("--name", help="VM name (default: vortax, or $VORTAX_NAME)")
        return sp

    sp = named(add("stop", cmd_stop, "shut the guest down (ACPI power button), then QEMU"))
    sp.add_argument("--force", action="store_true", help="do not wait for the guest")
    sp.add_argument("--grace", type=float, default=30.0)
    named(add("kill", cmd_kill, "terminate QEMU immediately"))
    named(add("reboot", cmd_reboot, "reset the running guest"))
    sp = add("status", cmd_status, "list VMs and whether they run")
    sp.add_argument("--name")
    sp.add_argument("--json", action="store_true")
    sp = named(add("info", cmd_info, "connection details for a running VM"))
    sp.add_argument("--show-password", action="store_true")

    sp = named(add("password", cmd_password, "show or change the VNC password"))
    sp.add_argument("--set", action="store_true", help="prompt for a new password (or read one line from stdin)")
    sp.add_argument("--new", action="store_true", help="generate a new random password")
    sp.add_argument("--access", action="store_true", help="show the RustDesk/AnyDesk password instead")

    sp = named(add("snapshot", cmd_snapshot, "save, load, list or delete qcow2 snapshots"))
    sp.add_argument("action", choices=["save", "load", "list", "delete"])
    sp.add_argument("tag", nargs="?")

    sp = named(add("export", cmd_export, "compress a stopped VM's disk (optionally upload with rclone)"))
    sp.add_argument("--out", help="output file (extension is chosen by the compressor)")
    sp.add_argument("--rclone", metavar="REMOTE:PATH", help="upload the result with rclone")

    sp = named(add("tunnel", cmd_tunnel, "start or stop a tunnel for a running VM"))
    sp.add_argument("action", choices=["start", "stop"])
    sp.add_argument("kind", nargs="?", choices=[t for t in settings.TUNNELS if t != "none"], default="bore")

    sp = named(add("edit", cmd_edit, "change the saved settings of an existing VM (RAM, CPUs, sound...); keeps its disk"))
    flag = argparse.BooleanOptionalAction
    sp.add_argument("--ram-mb", type=int, help="0 = automatic")
    sp.add_argument("--vcpus", type=int)
    sp.add_argument("--accel", choices=settings.ACCEL)
    sp.add_argument("--nic", help=f"network card model ({', '.join(settings.NICS)})")
    sp.add_argument("--audio", choices=settings.AUDIO, help="emulated sound card")
    sp.add_argument("--mttcg", action=flag, default=None, help="without KVM, run vCPUs on several host cores (experimental)")
    sp.add_argument("--boot", choices=settings.BOOT_ORDER, help="disk ignores the installer ISO")
    sp.add_argument("--ssh", action=flag, default=None)
    sp.add_argument("--snapshot-on-stop", action=flag, default=None)
    sp.add_argument("--notify", action=flag, default=None)
    sp.add_argument("--runtime", type=int, help="minutes before the VM is stopped (0 = never)")
    sp.add_argument("--extra-qemu-args", help="raw QEMU arguments appended verbatim")
    sp.add_argument("--driver-iso", "--virtio-iso", dest="virtio_iso",
                    help="an extra CD of drivers to attach (an ISO file); give \"\" to remove it")

    sp = named(add("driver-iso", cmd_driver_iso, "make an ISO from a folder of drivers, optionally attach it to a VM"))
    sp.add_argument("folder", help="folder whose files go on the ISO")
    sp.add_argument("-o", "--out", help="ISO path (default: next to the folder, FOLDER.iso)")
    sp.add_argument("--attach", action="store_true", help="also attach it to the VM given by --name")

    sp = named(add("rm", cmd_rm, "delete a VM's disk, media and state"))
    sp.add_argument("--yes", action="store_true")

    sp = named(add("access", cmd_access, "install host remote-access programs (rdp, nomachine, rustdesk, anydesk)"))
    sp.add_argument("action", choices=["install", "list", "launcher"])
    sp.add_argument("program", nargs="?")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--allow-unverified", action="store_true", help="install a package that has no pinned checksum")


    sp = named(add("logs", cmd_logs, "show the QEMU log (-f to follow)"))
    sp.add_argument("-n", "--lines", type=int, default=50)
    sp.add_argument("-f", "--follow", action="store_true")
    sp.add_argument("--until-stop", action="store_true", help=argparse.SUPPRESS)

    sp = add("_guard", cmd_guard, "internal: stop a VM when its runtime ends")
    sub._choices_actions = [a for a in sub._choices_actions if a.dest != "_guard"]
    sp.add_argument("--name")
    sp.add_argument("--deadline-epoch", type=float, default=0.0)

    sp = add("preset", cmd_preset, "save, list, show or remove named run presets")
    sp.add_argument("action", choices=["save", "list", "show", "rm"])
    sp.add_argument("preset_name", nargs="?")
    _add_run_options(sp)

    sp = named(add("disk", cmd_disk, "inspect, resize or compact a VM disk"))
    sp.add_argument("action", choices=["info", "resize", "compact"])
    sp.add_argument("value", nargs="?", help="resize: new size such as +20G")

    sp = named(add("ssh", cmd_ssh, "ssh into a VM started with --ssh"))
    sp.add_argument("-u", "--user", default="root")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("command", nargs="*")

    sp = named(add("cp", cmd_cp, "copy files to/from a VM started with --ssh (use vm:PATH)"))
    sp.add_argument("src")
    sp.add_argument("dst")
    sp.add_argument("-u", "--user", default="root")
    sp.add_argument("--dry-run", action="store_true")

    sp = named(add("screenshot", cmd_screenshot, "save the guest screen as a PNG"))
    sp.add_argument("--out")

    add("wizard", cmd_wizard, "answer a few questions and start a VM")

    sp = add("doctor", cmd_doctor, "check the installation")
    sp.add_argument("--json", action="store_true")

    sp = add("selftest", cmd_selftest, "verify the install; --boot also boots a tiny test guest")
    sp.add_argument("--boot", action="store_true")
    sp.add_argument("--timeout", type=float, default=60.0)

    add("check-links", cmd_check_links, "HEAD-check every catalog download URL")

    sp = add("config", cmd_config, "show resolved settings or the data/config paths")
    sp.add_argument("action", choices=["show", "path"], nargs="?", default="show")

    sp = add("secret", cmd_secret, "store a tunnel token in ~/.config/vortax/secrets.json (mode 600)")
    sp.add_argument("name", choices=["tailscale_authkey", "ngrok_authtoken"])

    sp = add("dashboard", cmd_dashboard, "serve the web dashboard")
    sp.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--open", action="store_true", help="open a browser when it starts")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)      # `vortax list | head` should not traceback
    parser = build_parser()
    ns = parser.parse_args(argv)
    if getattr(ns, "license", False):
        print(f"vortax {__version__} is free software under the GNU AGPL v3 (AGPL-3.0-only).\n"
              f"Corresponding source: {__source_url__}\n"
              "Full text: /usr/share/doc/vortax/LICENSE or https://www.gnu.org/licenses/agpl-3.0.html\n"
              "Section 13: if you run a modified version as a network service, offer its users the source.")
        return 0
    if not getattr(ns, "fn", None):
        parser.print_help()
        return 0
    try:
        return int(ns.fn(ns) or 0)
    except VortaxError as exc:
        print(f"vortax: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
