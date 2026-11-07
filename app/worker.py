#!/usr/bin/env python3
"""Durable sequential backup queue with independent native Notes per guest."""

import argparse
import json
import os
from pathlib import Path
import subprocess

from server import Controller, PanelError, backup_arguments


def run_plan(plan):
    controller = Controller(plan)
    failed = False
    for guest in plan["guests"]:
        vmid = guest["vmid"]
        try:
            controller.validate_storage()
            usb = controller.usb()
            if not usb["mounted"]:
                raise PanelError("Właściwy pendrive nie jest zamontowany w trybie zapisu.")
            if usb["free"] < 1024 ** 3:
                raise PanelError("Na USB pozostało mniej niż 1 GiB.")
        except (PanelError, OSError, ValueError, subprocess.TimeoutExpired) as error:
            print(f"ERROR: Backup of VM {vmid} failed - {error}", flush=True)
            return 1  # Never continue onto an absent or different device.
        try:
            # Native vzdump writes directly to the unit log and the Proxmox task history.
            result = subprocess.run(backup_arguments(plan["node"], plan["storage"], vmid, guest.get("notes", "")),
                                    check=False, env={**os.environ, "LC_ALL": "C"})
            if result.returncode:
                print(f"ERROR: Backup of VM {vmid} failed - vzdump exit {result.returncode}", flush=True)
                failed = True
        except (PanelError, OSError) as error:
            print(f"ERROR: Backup of VM {vmid} failed - {error}", flush=True)
            failed = True
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    args = parser.parse_args()
    raise SystemExit(run_plan(json.loads(Path(args.plan).read_text())))


if __name__ == "__main__":
    main()
