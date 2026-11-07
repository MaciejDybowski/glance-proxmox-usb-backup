#!/usr/bin/env python3
"""Build a static, fictional-data GitHub Pages demo; no runtime configuration is read."""

import argparse
import hashlib
from pathlib import Path
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/MaciejDybowski/glance-proxmox-usb-backup"
sys.path.insert(0, str(PROJECT_ROOT / "app"))
from i18n import browser_script


def build(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("panel.js", "panel.css", "i18n.js"):
        shutil.copy2(PROJECT_ROOT / "app/static" / name, destination / name)
    for name in ("demo.js", "demo.css"):
        shutil.copy2(PROJECT_ROOT / "demo" / name, destination / name)
    source = (PROJECT_ROOT / "app/static/index.html").read_text(encoding="utf-8")
    (destination / "messages.js").write_bytes(browser_script("en"))
    source = source.replace('data-i18n="title"', 'data-i18n="demo_title"')
    source = source.replace('<link rel="stylesheet" href="panel.css">', '''<meta name="robots" content="index,follow">
  <link rel="icon" href="data:,">
  <meta name="description" content="Interactive Glance Proxmox USB Backup demo using fictional data. No Proxmox connection or credentials required.">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
  <link rel="stylesheet" href="panel.css">
  <link rel="stylesheet" href="demo.css">''')
    source = source.replace('<script src="panel.js" defer></script>', '<script src="demo.js" defer></script>\n  <script src="panel.js" defer></script>')
    source = source.replace("  <main>", f'''  <header class="demo-banner">
    <div>
      <div class="demo-kicker" data-i18n="demo_kicker">INTERACTIVE DEMO / FICTIONAL DATA</div>
      <h2>Glance Proxmox USB Backup</h2>
      <p data-i18n="demo_description">Try selecting machines, adding individual notes, running a simulated backup, and mounting or ejecting the demo drive. Everything stays in browser memory — no Proxmox connection or credentials.</p>
    </div>
    <a class="demo-link" href="{REPOSITORY}" target="_blank" rel="noopener noreferrer" data-i18n="demo_github">View on GitHub ↗</a>
  </header>
  <main class="demo-app">''')
    source = source.replace("  </main>", '  </main>\n  <p class="demo-footer" data-i18n="demo_footer">Static GitHub Pages demo · Reload or reset to restore sample data.</p>')
    # Keep translations and UI in sync when a CDN/browser still has older assets.
    for name in ("messages.js", "i18n.js", "panel.js", "panel.css", "demo.js", "demo.css"):
        digest = hashlib.sha256((destination / name).read_bytes()).hexdigest()[:12]
        source = source.replace(f'"{name}"', f'"{name}?v={digest}"')
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
