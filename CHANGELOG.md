# Changelog

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
