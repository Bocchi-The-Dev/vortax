# Vortax

Run and manage QEMU virtual machines from one small CLI and a web dashboard.
Pick a system from the catalog, or bring your own ISO, disk image or archive. Vortax downloads it, prepares the
disk, starts QEMU, and gives you VNC access, optionally through a tunnel.

- **Standard library only.** No pip dependencies; the `.deb` is a few hundred KB.
- **KVM when you have it, software emulation when you don't** (phones, most VPSes, CI runners).
- **Safe defaults.** VNC and the QEMU monitor are local-only, passwords are random per VM and never appear in
  process lists, tokens never go on a command line, archives are extracted with path checks.
- **Testable.** The QEMU command is built by one pure function; `vortax selftest --boot` boots a generated
  512-byte guest through the real pipeline to prove an install works.

## Install

Debian / Ubuntu (and Debian inside proot on a phone):

```sh
sudo apt install ./vortax_3.0.0~b1-1_all.deb      # pulls in qemu-system-x86 and qemu-utils
vortax doctor
```

Anywhere with Python 3.9+ (Termux, other distros):

```sh
pipx install .            # or: pip install .
pkg/apt install qemu ...  # you still need qemu-system-x86_64 and qemu-img (Termux: `pkg search qemu`)
```

Build the `.deb` yourself: `MAINTAINER="You <you@example.com>" packaging/build-deb.sh` → `dist/`.

## Quick start

```sh
vortax list                                   # the catalog
vortax plan --system ubuntu --boot-mode iso   # what would happen, and the exact QEMU command
vortax run  --system mint --tunnel bore       # prepare, start, wait; Ctrl-C stops cleanly
vortax start --system debian --name lab       # same, but returns immediately
vortax info --name lab --show-password        # address and VNC password
vortax stop --name lab
vortax start --name lab                       # restart: reuses how it was launched
```

Your own media: `vortax run --source /path/to/any.iso` or a URL; `.7z .zip .tar.* .gz .xz .bz2 .zst` archives
holding a disk image or an ISO are unpacked automatically, and the real disk format is detected (raw stays raw,
qcow2 stays qcow2, anything else is converted).

After an installer finishes, boot from the disk instead of the ISO: `vortax start --name lab --boot disk`.

### More things it can do

```sh
vortax wizard                                   # answer a few questions, optionally save them as a preset
vortax preset save work --system ubuntu-server --ram-mb 4096 --ssh --share ~/code:code
vortax run work                                 # a preset, a catalog id or a local file all work as the target
vortax start lab --ssh && vortax ssh -u me      # SSH forwarded to 127.0.0.1 (random free port)
vortax cp ./notes.txt vm:/home/me/              # scp to/from the guest
vortax start lab --share ~/code:code            # 9p folder; in Linux: mount -t 9p -o trans=virtio code /mnt
vortax start lab --usb 046d:c52b --audio hda    # USB passthrough and an emulated sound card
vortax start lab --snapshot-on-stop             # stop saves the live state, the next start resumes it (qcow2)
vortax start lab --runtime 90                   # a background watcher stops it after 90 minutes
vortax screenshot --name lab --out lab.png      # PNG of the guest display
vortax disk resize +20G --name lab              # also: disk info, disk compact
vortax logs -f --name lab                       # follow the QEMU log
```

New catalog entries (`alpine`, `ubuntu-server`, `arch`, `tumbleweed`) look up the current ISO name when you run
them (`latest:` sources), so they do not go stale when a distro renames files.

On Termux, RAM defaults are capped for phones, `vortax doctor` prints the `pkg install` line for missing tools and a
wake lock is taken while a VM runs (needs `termux-api`). `--notify` uses `termux-notification` when it exists.

### Settings come from the environment too

Every option has a `VORTAX_<NAME>` variable, which is how a script or CI job drives it without pasting values
into shell source:

```sh
VORTAX_SYSTEM=bazzite VORTAX_BOOT_MODE=iso VORTAX_RAM_MB=4096 vortax run
```

Order: command line, then environment, then `~/.config/vortax/config.json`. `vortax config show` prints the
result (passwords redacted). See [docs/MIGRATING.md](docs/MIGRATING.md) for the mapping from the old workflow.

## Dashboard

```sh
vortax dashboard            # prints http://127.0.0.1:8765/#token=...
```

Overview with live CPU/RAM/disk, start/stop/reboot/kill, launching systems from the catalog with progress,
VNC password reveal/change, tunnels, snapshots, storage cleanup, logs and a health page.

* **Console**: the guest's screen in a browser tab, no extra software. The dashboard proxies VNC over a WebSocket and
  answers QEMU's password challenge itself, so the browser never sees the VNC password. Mouse, keyboard, touch
  (tap, long-press = right click), Ctrl/Alt/Win toggles, Ctrl+Alt+Del, "Type text", screenshot, fullscreen.
* **Settings**: edit default RAM/CPU/tunnel/etc, save and run presets, dark or light theme, English or Arabic (RTL),
  browser notifications when a VM starts or stops.
* It works at phone width. The token sits in the URL fragment, so it is never sent to the
server or written to logs. Binding to a non-loopback address is possible (`--host`) but it is plain HTTP:
prefer an SSH tunnel or Tailscale.

## Hardware profiles

`vortax profiles` lists presets for guests that break on modern defaults: Windows NT 4 / Me / 2000 / 98 / 95 /
3.1 / 1.x / 2.x and Batocera (with `--gpu-mode virtio|bochs|std`). Retro catalog entries select their profile
automatically. Windows Me always runs without KVM; floppy-install profiles need `--floppy-url`.

## Tunnels and access

| Option | Needs |
| --- | --- |
| `--tunnel bore` | the `bore` binary (`cargo install bore-cli`); `VORTAX_BORE_SERVER` for your own server |
| `--tunnel ngrok` | `ngrok` and `NGROK_AUTHTOKEN` (or `vortax secret set ngrok_authtoken`) |
| `--tunnel tailscale` | `tailscale` logged in, or `TAILSCALE_AUTHKEY` |
| `--access rdp/nomachine/rustdesk/anydesk` | `sudo vortax access install <name>` (use `--dry-run` first) |

VNC passwords are limited to 8 characters by the protocol, which is thin protection on a public tunnel. For
anything beyond a quick test, prefer Tailscale or an SSH tunnel and keep VNC on loopback.

## Adding systems

Systems are data: add an entry to `src/vortax/data/systems.json` (or to your own
`~/.config/vortax/systems.json`, which overrides it by id). `vortax check-links` HEAD-checks every URL.

## Licensing and terms

- **Windows media is not bundled.** Windows and the retro slots are templates; supply media you are licensed to use.
- Running desktops for hours on **GitHub-hosted runners** is outside GitHub's Actions terms. This repository's CI
  only builds, tests and briefly smoke-tests the package. Run VMs on your own hardware or VPS.
- Respect the terms of any download host or tunnel service you use.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

The tests put fake `qemu-system-x86_64` and `qemu-img` (in `tests/stubs`) first on `PATH`, so they run anywhere.
CI additionally installs the built `.deb` on a clean runner and runs `vortax selftest --boot` against real QEMU.

## License

GNU Affero General Public License v3.0 only (see `LICENSE`). If you run a modified copy as a network
service, including the dashboard, section 13 requires you to offer its users the corresponding source.
The dashboard footer and `vortax --license` point at the source for exactly that reason.
