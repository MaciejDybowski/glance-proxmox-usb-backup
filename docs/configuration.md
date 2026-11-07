# Configuration

## Installation settings

`config/install.example.json` contains **non-secret inputs**, not a ready-to-run runtime config. The installer generates the password salt/hash and session secret on the host.

| Input / installer flag | Meaning |
| --- | --- |
| `uuid` / `--uuid` | Exact filesystem UUID from `lsblk -f` |
| `storage` / `--storage` | Existing Proxmox Directory storage; default `usb-backup` |
| `mountpoint` / `--mountpoint` | Must match that storage's path; detected from storage if omitted |
| `filesystem` / `--filesystem` | `exfat`, `ext4`, or `xfs`; detected on a connected drive if omitted |
| `drive_name` / `--drive-name` | Display label only; default `USB backup` |
| `glance_origins` / repeat `--glance-origin` | Allowed embedding origins, without paths |
| `listen` / `--listen` | IPv4 bind address; default `0.0.0.0` |
| `port` / `--port` | HTTPS port; default `8765` |
| `tls_cert`, `tls_key` / matching flags | Optional certificate paths; default to the existing Proxmox certificate |

An empty origin list permits only same-origin embedding. Explicitly configure the origin of your Glance instance. Wildcard ports such as `https://glance.example.test:*` are accepted, but a precise port is usually sufficient. Changing the certificate requires restarting the service.

## Runtime configuration

The installer writes `/etc/glance-backup/config.json` as root-only JSON. It also records the local Proxmox node and `/var/lib/glance-backup` state directory. Do not commit this file or copy it into public examples: it contains authentication material.

The display label is supplied to the browser through the authenticated API; there is no hardcoded drive name in the public UI. Older installations without a filesystem setting retain exFAT behavior.

## Notes

Each selected machine exposes its own optional note field, up to 500 characters. Notes may be multiline and Unicode. A non-empty note is passed as:

```text
{{guestname}} - your note for this particular machine
```

Native placeholders `{{guestname}}`, `{{vmid}}`, `{{node}}`, and `{{cluster}}` may also appear in the text. Newlines and literal backslashes are encoded for Proxmox's template syntax. Empty notes retain that machine's native `vzdump` defaults. The archive list displays the **first line**, matching the storage-content API, while the full note is stored in Proxmox.

## Glance integration

Set `PROXMOX_BACKUP_URL`, for example:

```dotenv
PROXMOX_BACKUP_URL=https://proxmox.example.test:8765/
```

Glance environment changes require restarting Glance. YAML edits normally auto-reload. No panel password or Proxmox token belongs in this example.

HTML widgets in Glance render `source` directly: they do not automatically apply the standard widget wrapper or `css-class`. The examples therefore provide explicit `.widget` markup. The full example listens for height messages and verifies both the iframe window and origin before resizing it.

For reverse proxies, serve the panel at the origin root and preserve Host/Origin semantics. Use HTTPS on both sides as appropriate. The app uses origin-root API paths; arbitrary subpath hosting is not implemented.

## Workstation deployment helper

Copy `config/deploy.env.example` to `local/deploy.env`. This is a **trusted shell file**, sourced locally. It is excluded from Git and public exports.

The optional `GLANCE_SSH_TARGET`, `GLANCE_CONFIG_FILE`, and `GLANCE_CONFIG_PATH` enable uploading your own private dashboard after the panel installation succeeds. Otherwise the helper deploys only the panel. The helper neither fetches repositories nor commits/pushes changes.
