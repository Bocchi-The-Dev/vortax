# Moving from the workflow to the package

| Old workflow input | New |
| --- | --- |
| `system_choice` | `--system` / `VORTAX_SYSTEM` (`vortax list`) |
| `boot_mode` | `--boot-mode` / `VORTAX_BOOT_MODE` (`auto` picks img, qcow2, then iso) |
| `source_url` | `--source` / `VORTAX_SOURCE` (URL **or local path**) |
| `vm_name` | `--name` / `VORTAX_NAME` |
| `runtime` | `--runtime` minutes (`vortax run` only; `0` = until Ctrl-C) |
| `connection_method` | `--tunnel` none/bore/ngrok/tailscale |
| `connection_program` | `--access` vnc/rdp/nomachine/rustdesk/anydesk (`vortax access install ...`) |
| `bios_mode` | `--firmware` bios/uefi |
| `vnc_password` | random per VM; `vortax password` to read, `--set`/`--new` to change. Env `VORTAX_PASSWORD` to choose one |
| `remote_access_password` | env `VORTAX_ACCESS_PASSWORD` (never a flag, so it cannot show up in `ps`) |
| `upload_qcow2` (Mega) | removed: `vortax export [--rclone remote:path]` |
| `network_card` | `--nic` |
| `extra_qemu_args` | `--extra-qemu-args` |
| `legacy_profile` | `--profile` (also set automatically by retro catalog entries) |
| `legacy_floppy_url` | `--floppy-url` |
| `batocera_gpu_mode` | `--gpu-mode` |
| `custom_bios_url` | `--bios-url` |
| `TAILSCALE_AUTHKEY`, `NGROK_AUTH` secrets | same env names, or `vortax secret set ngrok_authtoken` |

Changed IDs: `bazite` is now `bazzite` (the old name still works as an alias). IDs are case-insensitive.
Pre-built disks (the old `QCOW2_URLS`) are the `qcow2` boot mode of the same system id.

## Filling in the Windows slots

Microsoft media is not bundled. Create `~/.config/vortax/systems.json`:

```json
{"systems": [{"id": "win10", "name": "Windows 10", "family": "windows", "disk_gb": 80,
              "sources": {"iso": "https://your.host/your-win10.iso"}}]}
```

or pass `--source /path/to/win10.iso` for a single run.
