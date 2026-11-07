#!/usr/bin/env python3
"""Build a static, fictional-data GitHub Pages demo; no runtime configuration is read."""

import argparse
from pathlib import Path
import shutil

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/MaciejDybowski/glance-proxmox-usb-backup"


def build(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("panel.js", "panel.css"):
        shutil.copy2(PROJECT_ROOT / "app/static" / name, destination / name)
    for name in ("demo.js", "demo.css"):
        shutil.copy2(PROJECT_ROOT / "demo" / name, destination / name)
    source = (PROJECT_ROOT / "app/static/index.html").read_text(encoding="utf-8")
    source = source.replace("<title>Backup na USB · Homelab</title>", "<title>Live demo · Glance Proxmox USB Backup</title>")
    source = source.replace('<link rel="stylesheet" href="panel.css">', '''<meta name="robots" content="index,follow">
  <link rel="icon" href="data:,">
  <meta name="description" content="Interactive Glance Proxmox USB Backup demo using fictional data. No Proxmox connection or credentials required.">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
  <link rel="stylesheet" href="panel.css">
  <link rel="stylesheet" href="demo.css">''')
    source = source.replace('<script src="panel.js" defer></script>', '<script src="demo.js" defer></script>\n  <script src="panel.js" defer></script>')
    source = source.replace("  <main>", f'''  <header class="demo-banner">
    <div>
      <div class="demo-kicker">INTERACTIVE DEMO / FICTIONAL DATA</div>
      <h2>Glance Proxmox USB Backup</h2>
      <p>Try selecting machines, adding individual notes, running a simulated backup, and mounting or ejecting the demo drive. Everything stays in browser memory — no Proxmox connection or credentials.</p>
    </div>
    <a class="demo-link" href="{REPOSITORY}" target="_blank" rel="noopener noreferrer">View on GitHub ↗</a>
  </header>
  <main class="demo-app">''')
    source = source.replace("  </main>", '  </main>\n  <p class="demo-footer">Static GitHub Pages demo · Reload or use “Resetuj demo” to restore the sample data. Interface currently in Polish.</p>')
    (destination / "index.html").write_text(source, encoding="utf-8")
    (destination / ".nojekyll").write_text("", encoding="utf-8")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "dist/demo")
    args = parser.parse_args()
    destination = build(args.output)
    print(f"Static demo built: {destination}")
    print("Contains only static UI assets and a fictional in-memory simulator. No runtime data or secrets.")


if __name__ == "__main__":
    main()
