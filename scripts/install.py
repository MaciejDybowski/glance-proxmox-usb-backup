#!/usr/bin/env python3
"""Install or upgrade on the Proxmox host, as root. No Python dependencies."""

import argparse
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "app"))
from server import atomic_json

DESTINATION = Path("/opt/glance-backup")
CONFIG_FILE = Path("/etc/glance-backup/config.json")
STATE_DIR = Path("/var/lib/glance-backup")
FILESYSTEMS = {"exfat", "ext4", "xfs"}
SETTING_KEYS = {"uuid", "storage", "mountpoint", "filesystem", "drive_name", "listen", "port",
                "glance_origins", "tls_cert", "tls_key"}


def run(*args):
    try:
        return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Command failed: {' '.join(args)}\n{error.stderr or error.stdout}") from error


def validate_settings(settings):
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}", settings["uuid"])
            or settings["uuid"].startswith("REPLACE")):
        raise ValueError("Use the filesystem UUID from lsblk -f (not a device name).")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", settings["storage"]):
        raise ValueError("Invalid Proxmox storage ID.")
    mountpoint = settings["mountpoint"]
    if (not re.fullmatch(r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", mountpoint)
            or any(part in {".", ".."} for part in mountpoint.split("/"))):
        raise ValueError("Mountpoint must be an absolute path without whitespace or dot segments, other than /.")
    if settings["filesystem"] not in FILESYSTEMS:
        raise ValueError("Supported filesystems: exfat, ext4, xfs. The installer never formats devices.")
    if not isinstance(settings["drive_name"], str) or not 1 <= len(settings["drive_name"]) <= 100:
        raise ValueError("Drive display name must contain 1–100 characters.")
    if ipaddress.ip_address(settings["listen"]).version != 4:
        raise ValueError("The built-in server currently requires an IPv4 listen address.")
    if type(settings["port"]) is not int or not 1 <= settings["port"] <= 65535:
        raise ValueError("Port must be an integer between 1 and 65535.")
    for origin in settings["frame_ancestors"]:
        if not re.fullmatch(r"https?://(?:\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+)(?::(?:[0-9]{1,5}|\*))?", origin):
            raise ValueError("Glance origins must be http(s)://host[:port], without a path.")


def validate_storage(storage, storage_id, mountpoint):
    if (storage.get("type") != "dir" or storage.get("path") != mountpoint
            or str(storage.get("is_mountpoint")) != "1"
            or "backup" not in storage.get("content", "").split(",") or storage.get("disable")):
        raise ValueError(f"Storage {storage_id} must be an enabled Directory at {mountpoint}, with content backup and is_mountpoint=1.")


def render_mount(config):
    return f"""[Unit]
Description=Glance Proxmox USB backup storage

[Mount]
What=/dev/disk/by-uuid/{config['uuid']}
Where={config['mountpoint']}
Type={config['filesystem']}
Options=nosuid,nodev,noexec
TimeoutSec=30
"""


def render_udev(config, mount_unit):
    return ('ACTION=="add", SUBSYSTEM=="block", '
            f'ENV{{ID_FS_UUID}}=="{config["uuid"]}", TAG+="systemd", '
            f'ENV{{SYSTEMD_WANTS}}+="{mount_unit}"\n')


def write_preserving(path, content):
    if path.exists() and path.read_text() != content:
        previous = path.with_name(path.name + ".before-panel")
        if not previous.exists():
            shutil.copy2(path, previous)
    path.write_text(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, help="Non-secret installation JSON; see config/install.example.json")
    parser.add_argument("--uuid")
    parser.add_argument("--storage")
    parser.add_argument("--mountpoint")
    parser.add_argument("--filesystem", choices=sorted(FILESYSTEMS))
    parser.add_argument("--drive-name")
    parser.add_argument("--glance-origin", action="append", default=[])
    parser.add_argument("--listen")
    parser.add_argument("--port", type=int)
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    args = parser.parse_args()
    if os.geteuid() != 0 or not Path("/etc/pve/storage.cfg").exists():
        raise SystemExit("Run this installer as root on the Proxmox host, not inside the Glance LXC.")
    for binary in ("pvesh", "vzdump", "systemd-run", "findmnt", "umount", "udevadm", "blkid", "systemd-escape"):
        if not shutil.which(binary):
            raise SystemExit(f"Required command not found: {binary}")

    supplied = json.loads(args.settings.read_text()) if args.settings else {}
    if not isinstance(supplied, dict) or supplied.keys() - SETTING_KEYS:
        raise SystemExit("Unknown installation settings. Do not use the secret runtime config as an installation example.")
    for key in SETTING_KEYS - {"glance_origins"}:
        value = getattr(args, key, None)
        if value is not None:
            supplied[key] = value
    origins = supplied.pop("glance_origins", [])
    if not isinstance(origins, list) or any(not isinstance(origin, str) for origin in origins):
        raise SystemExit("glance_origins must be an array of origins.")

    existing = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else None
    config = dict(existing or {})
    config.update(supplied)
    config.setdefault("storage", "usb-backup")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", config["storage"]):
        raise SystemExit("Invalid storage ID.")
    storage = json.loads(run("pvesh", "get", f"/storage/{config['storage']}", "--output-format", "json"))
    config.setdefault("mountpoint", storage.get("path", ""))
    if "uuid" not in config:
        config["uuid"] = input("USB filesystem UUID (lsblk -f): ").strip()
    # Validate UUID before using it as a device path or udev match.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}", config["uuid"]):
        raise SystemExit("Invalid filesystem UUID.")
    device = Path("/dev/disk/by-uuid") / config["uuid"]
    if "filesystem" not in config:
        if existing:
            config["filesystem"] = "exfat"  # Compatibility with the original installer.
        elif device.exists():
            config["filesystem"] = run("blkid", "-s", "TYPE", "-o", "value", str(device))
        else:
            raise SystemExit("Connect the USB device or provide --filesystem (exfat, ext4, xfs).")
    config.setdefault("drive_name", "USB backup")
    config.setdefault("node", socket.gethostname().split(".")[0])
    config.setdefault("listen", "0.0.0.0")
    config.setdefault("port", 8765)
    config["state_dir"] = str(STATE_DIR)
    config["frame_ancestors"] = sorted(set(config.get("frame_ancestors", []) + origins + args.glance_origin))
    tls_prefix = "/etc/pve/local/pveproxy-ssl" if Path("/etc/pve/local/pveproxy-ssl.pem").exists() else "/etc/pve/local/pve-ssl"
    config.setdefault("tls_cert", tls_prefix + ".pem")
    config.setdefault("tls_key", tls_prefix + ".key")
    try:
        validate_settings(config)
        validate_storage(storage, config["storage"], config["mountpoint"])
        if existing:
            for key in ("uuid", "storage", "mountpoint", "filesystem"):
                if config[key] != existing.get(key, "exfat" if key == "filesystem" else None):
                    raise ValueError(f"Changing {key} on an existing installation requires a planned storage migration.")
    except (ValueError, TypeError) as error:
        raise SystemExit(str(error)) from error
    if not Path(config["tls_cert"]).is_file() or not Path(config["tls_key"]).is_file():
        raise SystemExit("TLS certificate/key not found. Provide --tls-cert and --tls-key or use the existing Proxmox certificate.")

    if not existing:
        password = getpass.getpass("Panel password (12–256 characters): ")
        if not 12 <= len(password) <= 256 or password != getpass.getpass("Repeat panel password: "):
            raise SystemExit("Password validation failed. No configuration was installed.")
        salt = secrets.token_bytes(16)
        config.update(password_salt=salt.hex(), session_secret=secrets.token_hex(32),
                      password_hash=hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex())
    for directory in (DESTINATION, CONFIG_FILE.parent, STATE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.parent.chmod(0o700)
    STATE_DIR.chmod(0o700)
    atomic_json(CONFIG_FILE, config)
    for name in ("server.py", "worker.py"):
        shutil.copy2(PROJECT_ROOT / "app" / name, DESTINATION / name)
    shutil.copy2(PROJECT_ROOT / "scripts/change-password.py", DESTINATION / "change-password.py")
    shutil.copytree(PROJECT_ROOT / "app/static", DESTINATION / "static", dirs_exist_ok=True)
    Path(config["mountpoint"]).mkdir(parents=True, exist_ok=True)
    mount_unit = run("systemd-escape", "--path", "--suffix=mount", config["mountpoint"])
    write_preserving(Path("/etc/systemd/system") / mount_unit, render_mount(config))
    write_preserving(Path("/etc/udev/rules.d/99-usb-backup.rules"), render_udev(config, mount_unit))
    write_preserving(Path("/etc/systemd/system/glance-backup.service"),
                     (PROJECT_ROOT / "deployment/glance-backup.service").read_text())
    run("systemctl", "daemon-reload")
    run("udevadm", "control", "--reload-rules")
    if device.exists():
        run("systemctl", "start", mount_unit)
    run("systemctl", "enable", "glance-backup.service")
    run("systemctl", "restart", "glance-backup.service")
    time.sleep(1)
    run("systemctl", "is-active", "glance-backup.service")
    print(f"Installed. Open https://PROXMOX_HOST:{config['port']}/ and accept/trust its certificate if necessary.")
    print("Existing passwords and sessions are preserved during upgrades. No backup was started.")


if __name__ == "__main__":
    main()
