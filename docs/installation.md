# Installation and operations

## 1. Prepare the existing backup storage

Identify the **filesystem UUID** using `lsblk -f`. Do not use `/dev/sdb1` as a persistent identity; device names may change.

The installer expects an existing, enabled Directory storage that contains backups and requires an actual mountpoint. For a new, deliberately chosen storage:

```bash
mkdir -p /mnt/usb-backup
pvesm add dir usb-backup --path /mnt/usb-backup --content backup --is_mountpoint 1
```

Skip the `pvesm add` command if that storage already exists. Verify its configuration first. The installer does not format a filesystem, move existing backups, or change `/etc/pve/storage.cfg`.

Supported filesystem types are exFAT, ext4, and XFS. FAT32 is not supported because its per-file size limit is unsuitable for VM archives. On a disconnected device, provide `--filesystem` explicitly; on a connected device the installer can detect it.

## 2. Install on the Proxmox host

Upload the public project and run `scripts/install.py` as root, as shown in the main README. Alternatively, copy `config/install.example.json` to a private file, replace its example values, and run:

```bash
python3 scripts/install.py --settings /path/to/your/install-settings.json
```

The installer validates the storage, UUID, mountpoint, origins, and filesystem before writing configuration. It prompts for a separate panel password, creates the service and UUID-specific mounting rule, and starts the service. It does **not** start a backup.

Choose **English / Polski** when prompted for the panel language, or provide `--language en` / `--language pl`. The selection is stored in runtime configuration. Upgrading an older Polish-only installation keeps Polish unless a language is explicitly supplied.

| Installed path | Purpose |
| --- | --- |
| `/opt/glance-backup/` | `server.py`, `worker.py`, static UI, password utility |
| `/etc/glance-backup/config.json` | Runtime configuration, password hash, signing secret; root-only |
| `/var/lib/glance-backup/state.json` | Last queue state |
| `/var/lib/glance-backup/*.plan.json` | Root-only queue plans, including per-machine Notes |
| `/var/lib/glance-backup/*.log` | Queue output |
| `/var/lib/glance-backup/inventory.json` | Last successful USB archive inventory |
| `/etc/systemd/system/glance-backup.service` | HTTPS service |
| `/etc/systemd/system/<escaped-mountpoint>.mount` | UUID-based mount unit |
| `/etc/udev/rules.d/99-usb-backup.rules` | Mount on device insertion |

The installation supports **one configured USB filesystem**. Generated files that differ from an existing version get a first-copy `.before-panel` backup. Existing global `vzdump` hooks continue to apply, once per native guest backup.

## 3. Open the panel and integrate Glance

Open `https://PROXMOX_HOST:8765/` directly once. For a self-signed certificate, a browser cannot show its trust prompt inside an iframe; accept/trust it on the direct endpoint first.

Set the exact Glance origin with `--glance-origin`, including its scheme and non-default port. A root URL is an origin; a URL containing `/proxmox` is not. Add additional origins with repeated flags.

Use the full example in `examples/glance.yml` for automatic resizing, or the small `$include` example for a fixed-height frame. The iframe connects from **the user's browser** to the panel, not from the Glance backend.

## Upgrades

Upload the new project and rerun the installer, or use `bash scripts/deploy.sh` with your private deployment settings. Existing password hashes, signing secrets, and queue state are retained. New origins are added to the allowed list.

The installer refuses an implicit change of UUID, storage ID, mountpoint, or filesystem on an existing installation. Changing storage requires a deliberate migration outside the upgrade path. The original exFAT-only configuration is recognized without editing its runtime JSON.

After upgrading, refresh the browser page to load the new UI. Independent, already-running queue units continue running during a panel service restart.

## Change the password

On the Proxmox host as root:

```bash
python3 /opt/glance-backup/change-password.py
```

This prompts securely, replaces the password hash, rotates the session-signing secret, and restarts only the panel. Existing sessions must sign in again; independent backup queues are not stopped.

## USB workflow

1. Connect the configured USB drive. udev starts its mount unit.
2. Select machines and optionally enter a different note for each.
3. Start the queue. The panel launches native backups sequentially.
4. If the whole queue succeeds and automatic eject was selected, the panel snapshots the inventory and performs normal `umount`.
5. Remove the drive only after the panel confirms that it is unmounted.

Use **Mount USB** to remount a still-connected drive after an earlier eject. The manual eject button also works after backups started in the Proxmox UI, provided no backup is active.

## Failure handling

- A failed backup leaves the USB mounted and displays the log. Remaining guests can still run if the drive remains valid.
- A missing/wrong drive stops the queue before another guest starts. No automatic remount is attempted by the queue.
- Busy filesystems refuse normal unmount; there is no force/lazy unmount.
- Missing queue state or a host reboot leads to an uncertain state. The acknowledge action checks active tasks and the global backup lock before unblocking the panel. It never blindly restarts a backup.
- A failed inventory read preserves the old list with a stale/error label; it does not turn a successful backup into a failure or prevent safe eject.

LXC backups on exFAT use `/var/tmp` on the host for temporary data. Ensure that the host and USB both have enough space. The sum of root-disk allocations displayed by Proxmox is not a prediction of compressed archive sizes. Snapshot mode may fall back to suspend where native `vzdump` implements that behavior.

## Diagnostics

```bash
systemctl status glance-backup --no-pager
journalctl -u glance-backup -n 80 --no-pager
findmnt /mnt/usb-backup
pvesm status --storage usb-backup
```

Adapt the last two commands to your configured mountpoint and storage ID. Check native Proxmox task logs for backup-specific errors.

Old log/plan files are retained for manual housekeeping. Do not remove a plan while its queue is running. A completed task and an archive entry are not a restore test.

## Removal

Wait for queues to finish. Stop/disable `glance-backup.service`, safely unmount the configured device, then remove only the service/mount unit and udev rule installed for this project. Reload systemd and udev afterward. The exact mount-unit name is obtained with:

```bash
systemd-escape --path --suffix=mount /mnt/usb-backup
```

Do not remove the USB contents or Proxmox storage definition as part of removing the panel. Runtime configuration, queue metadata, and logs may be retained for later reinstall; they are independent of the USB archives.
