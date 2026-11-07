#!/usr/bin/env python3
"""Syntax validation without generating bytecode or touching a Proxmox host."""

import ast
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for directory in ("app", "scripts", "tests"):
    for path in (root / directory).rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print("Python syntax: OK")
