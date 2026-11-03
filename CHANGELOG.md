# Changelog

## Unreleased

### Added
- **Trusted-device sign-in**: the link `vortax dashboard` prints is now a one-time link. The first browser that opens it is
  trusted (its private key stays in that browser) and the link stops working, so a second phone cannot use it. More phones
  can join only if you raise the limit (`--max-devices N`, then a fresh link from Settings) or set a password
  (`--set-password`; wrong guesses are throttled). `--devices`, `--revoke ID`, `--reset-devices` manage the list, and
  Settings has a Trusted devices card. Only hashes of keys are stored. The owner token in `dashboard.token` still works for scripts.
- **Live edit**: `vortax live --name VM --cd ISO | --eject | --audio ac97|es1370|hda | --no-audio` (and a Live edit button)
  swap a CD or plug a sound card in and out of a running VM through the QEMU monitor. A VM with no CD drive gets a USB CD
  drive. The audio backend is now always defined at start so a card can be added later.
- **New dashboard look**: one token-based theme for dark and light, a scrollable tab bar on phones, bottom-sheet dialogs,
  aligned forms and cards, and a sign-in screen for untrusted browsers.
- **Console sound**: with `--audio hda|ac97|sb16` the dashboard console has a **Sound** button. QEMU streams the guest's
  audio over the same VNC connection (QEMU's audio extension, pseudo-encoding -259) and the browser plays it with a
  small jitter buffer. Mono 16-bit at 22050 Hz, about 44 KB/s, so it stays light over a tunnel. Browsers only unlock
  audio after a tap, which is why sound starts from the button.
- `-vnc` now gets `audiodev=snd0` whenever a sound card is enabled.
- **Windows XP preset** (`winxp`, used by the `XP` catalog entry): 512 MB RAM, a 256 MB cap on QEMU's translation cache
  and one vCPU when running without KVM (extra vCPUs only add overhead there), `qemu64` CPU for 32-bit and x64 XP,
  Cirrus display, RTL8139 network, a USB tablet so the VNC pointer stays aligned. Meant for phones, where QEMU
  growing past Android's memory limit gets Termux killed.
- Preset keys `tb_size_mb` and `vcpus_tcg`.
- `status` and the dashboard now show the RAM and vCPU count QEMU really gets; a preset can override the requested values.
- `vortax edit --name X ...` and an **Edit** button on each VM card in the dashboard change an existing VM's saved settings
  (RAM, vCPUs, sound card, network card, acceleration, `--mttcg`, ...) without recreating it. The disk is never touched and
  the change applies the next time the VM starts. Name, system, source and passwords cannot be edited this way.
- `vortax driver-iso FOLDER [--attach --name X]` builds an ISO from a folder (needs xorriso, genisoimage or mkisofs; Joliet and Rock
  Ridge names, so long file names work in XP) and can attach it to a VM in one step.
- `--driver-iso` (same as `--virtio-iso`) now also works with hardware presets such as Windows XP, so you can attach an ISO of
  drivers; it takes the free slot next to the installer CD. Settable with `vortax edit`.
- Sound card `es1370` (`--audio es1370`): Windows 2000/XP ship a driver for it, which stripped-down XP images often lack for
  the other cards.
- `--mttcg` (`VORTAX_MTTCG`): without KVM, run several vCPUs on several host threads (`-accel tcg,thread=multi`). Experimental
  for x86 guests on ARM phones: QEMU warns that the guest expects stronger memory ordering than the host gives.
  The XP preset puts all vCPUs in one socket so XP Home can use them; XP must use its multiprocessor HAL.
- `vortax doctor` on Termux explains Android's "Process completed (signal 9)" and how to switch the limit off.

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
