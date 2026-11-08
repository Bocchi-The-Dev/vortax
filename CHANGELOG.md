# Changelog

## Unreleased

### Added
- `vortax doctor --fix`: install the tools `doctor` reports as missing (QEMU, UEFI firmware, noVNC, websockify, 7z, ...)
  through apt, Termux `pkg`, dnf, zypper, pacman, apk or brew. It shows the commands and asks first (`--dry-run` only
  shows them, `--yes` skips the question, `--optional` adds the extras), installs one package at a time so one
  unknown name cannot block the rest, and re-checks afterwards. bore, ngrok, tailscale and the `kvm` group are only
  explained.
- `vortax doctor` now checks for the noVNC web client that the dashboard's browser console needs.

## 3.1.0b2

### Added
- One-command install on Debian and Ubuntu: `curl -fsSL https://codevortax.github.io/vortax/install.sh | sudo sh`
  (`packaging/install.sh`, published with the apt repository).
- `debian/` is now a real Debian source package (debhelper 13, pybuild, DEP-5 copyright), built and linted in CI on
  Debian stable, Debian unstable and Ubuntu 24.04. See `docs/DEBIAN.md` for the route into the official archive.
- Windows and Linux platform test workflow (`platform-test.yml`).

### Fixed
- `vortax dashboard`: the `open: ...#token=...` line is now flushed, so it reaches log files and `journalctl`
  (it used to stay in the output buffer when stdout was not a terminal).
- CI: Python 3.9 jobs run on Ubuntu 24.04 (`ubuntu-latest` no longer provides 3.9).

### Known limitations
- Windows is not supported yet: starting a VM fails right after QEMU launches (Unix-only process handling).

## 3.1.0b1

### Changed
- Relicensed from MIT to **GNU AGPL v3 only**. `vortax --license`, SPDX headers, a CI license check, the dashboard
  footer and `/api/about` point at the source, as section 13 requires for network use.

### Added
- **Console**: the VM screen in the dashboard (WebSocket to VNC proxy, pure-Python VNC authentication done on the
  server, canvas RFB client with mouse, keyboard, touch and on-screen modifier keys).
- **Dashboard settings** (defaults, presets), dark/light theme, Arabic with RTL layout, browser notifications.
- CLI: `wizard`, `preset`, `logs [-f]`, `screenshot`, `disk info|resize|compact`, `ssh`, `cp`; a positional target
  (preset, catalog id or file) on plan/prepare/start/run.
- Run options: `--share` (9p), `--usb`, `--audio`, `--ssh`, `--snapshot-on-stop`, `--notify`.
- `--runtime` now also works with `start` and the dashboard (a detached watcher stops the VM).
- Termux: capped RAM default, `pkg install` hints in `doctor`, wake lock, `termux-notification`.
- Download progress for the built-in downloader.
- Catalog: alpine, ubuntu-server, arch, tumbleweed via `latest:` sources that resolve the current ISO.
- Release workflow (tag `v*`), signed apt repository builder (`packaging/make-apt-repo.sh`, `docs/APT.md`).

## 3.0.0b1

Vortax is now an installable package (`.deb` and `pip`) instead of one ~1000-line workflow file.

### Added
- `vortax` CLI: `list`, `profiles`, `plan`, `prepare`, `start`, `run`, `stop`, `kill`, `reboot`, `status`, `info`,
  `password`, `snapshot`, `export`, `tunnel`, `rm`, `access`, `dashboard`, `doctor`, `selftest`, `check-links`.
- Every run setting reads a `VORTAX_*` environment variable (precedence: CLI > env > `config.json`).
- Web dashboard rebuilt from the old scratch: token auth, loopback-only by default, no CDN, phone friendly.
- Local files and `file://` sources; the catalog is JSON and user entries override it from `~/.config/vortax/`.
- `aarch64` guests (UEFI, experimental), automatic KVM/TCG selection, per-VM instances (several at once).
- Snapshots (qcow2), `export` with optional `rclone` upload, `--boot disk` after an installer run.
- `.deb` package (man page, bash completion, optional systemd user unit) and a build/test-only CI workflow.
- `vortax selftest --boot`: boots a generated 512-byte guest through the real pipeline and reads a serial marker.

### Changed
- File types are detected from magic bytes in Python (the old `file --mime-type` + `*zip*` check sent `.gz` to `unzip`).
- Archives are extracted with path checks (zip-slip, symlinks); extracted disks keep their real format.
- The QEMU command is built by one pure function with unit tests instead of copy-pasted bash.
- VNC listens on 127.0.0.1 with a random per-VM password by default; the monitor is a private unix socket.
- Tunnel tokens are passed by environment/file, never on a command line.
- UEFI firmware comes from the OS package instead of an unpinned third-party download.

### Removed
- Mega upload and the third-party Mega account generator.
- Bundled Microsoft download links (Windows entries are template slots; supply your own media).
- Hard-coded `runner:runner` credentials and the GitHub-runner-only six-hour keep-alive.
- The "rebuild an ISO from an archive with mkisofs" fallback.
