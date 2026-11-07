# Security

The service is intended for a trusted LAN/VPN and runs as root on the Proxmox host because it mounts filesystems and invokes local PVE tools. It exposes fixed operations, not a general shell.

Use HTTPS, a dedicated panel password, and explicit embedding origins. Keep `/etc/glance-backup/config.json` and `/var/lib/glance-backup` private. Public example settings do not contain a real password hash or session key.

To report a vulnerability, prefer the repository's private vulnerability-reporting channel if enabled. Do not publish working credentials, root keys, or unredacted runtime configuration in issues. If no private channel exists, open an issue requesting a private contact without exploit details or secrets.

Password changes rotate the signing secret and invalidate panel sessions. A service restart alone does not rotate credentials or stop independent backup jobs.
