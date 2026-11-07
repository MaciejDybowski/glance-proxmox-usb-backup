#!/usr/bin/env python3
"""Change the installed panel password and invalidate its sessions."""

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/glance-backup/config.json"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run as root on the Proxmox host.")
    sys.path.insert(0, "/opt/glance-backup")
    from server import atomic_json
    password = getpass.getpass("New panel password (12–256 characters): ")
    if not 12 <= len(password) <= 256 or password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match or are outside the allowed length. Nothing changed.")
    config = json.loads(args.config.read_text())
    salt = secrets.token_bytes(16)
    config.update(password_salt=salt.hex(), session_secret=secrets.token_hex(32),
                  password_hash=hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex())
    atomic_json(args.config, config)
    subprocess.run(["systemctl", "restart", "glance-backup.service"], check=True)
    print("Password changed. Sign in again. Independent backup jobs continue running.")


if __name__ == "__main__":
    main()
