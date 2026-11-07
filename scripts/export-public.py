#!/usr/bin/env python3
"""Create a source-only public archive, excluding local configuration and Git history."""

import argparse
from pathlib import Path
import tarfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = ("README.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
                ".gitignore", ".gitattributes", ".editorconfig", "Makefile", "package.json", "package-lock.json",
                "config/install.example.json", "config/deploy.env.example", "config/glance.env.example")
PUBLIC_DIRS = ("app", "scripts", "tests", "docs", "examples", "deployment", "demo", ".github")
SKIP_PARTS = {"__pycache__", "node_modules", ".DS_Store"}


def public_paths(root=PROJECT_ROOT):
    paths = [root / name for name in PUBLIC_FILES]
    for name in PUBLIC_DIRS:
        paths.extend(path for path in (root / name).rglob("*") if path.is_file())
    for path in sorted(paths):
        relative = path.relative_to(root)
        if (path.is_symlink() or any(part in SKIP_PARTS for part in relative.parts)
                or path.suffix in {".pyc", ".pyo", ".pem", ".key", ".log"}
                or path.name in {"config.json", "state.json", "inventory.json"}
                or path.name.startswith(".env") or path.name.endswith(".plan.json")):
            continue
        if not path.is_file():
            raise ValueError(f"Required publication file is missing: {relative}")
        yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "dist/glance-proxmox-usb-backup.tar.gz")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, "w:gz") as archive:
        for path in public_paths():
            info = archive.gettarinfo(str(path), arcname="glance-proxmox-usb-backup/" + path.relative_to(PROJECT_ROOT).as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with path.open("rb") as source:
                archive.addfile(info, source)
    print(f"Public source archive: {args.output}")
    print("No local/, IDE metadata, runtime state, credentials or .git history included.")


if __name__ == "__main__":
    main()
