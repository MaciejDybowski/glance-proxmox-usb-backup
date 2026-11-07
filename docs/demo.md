# Static live demo

The live demo is intended for GitHub Pages:

[English](https://maciejdybowski.github.io/glance-proxmox-usb-backup/?lang=en) · [Polski](https://maciejdybowski.github.io/glance-proxmox-usb-backup/?lang=pl)

It reuses the real panel's HTML/CSS/JavaScript and injects a separate, **fictional in-memory transport**. The installed production service does not serve the simulator and continues to use its authenticated API.

## Behavior

- No sign-in, password, PVE token, runtime config, or real storage required.
- Six fictional machines, an example drive UUID, and sample archives.
- Interactive per-machine Notes, a timed simulated queue, newly created example archives, mount/eject, and reset.
- No Proxmox calls, filesystem operations, browser storage persistence, telemetry, or external assets.
- A visible demo banner and log markers distinguish simulation from real operations.
- EN/PL selector, localized labels/errors and dates/numbers; default English with a shareable `?lang=en|pl` override.
- A `connect-src 'none'` Content Security Policy prevents network API calls from the demo.

All notes entered into the demo exist only in the current browser tab's memory. Reload or use **Resetuj demo** to restore the sample data.

## Local preview

```bash
python3 scripts/build-demo.py
python3 -m http.server 8080 --directory dist/demo
```

Open `http://localhost:8080/`. The generated site consists of exactly eight static files, including the shared translation catalog wrapped as JavaScript. The build reads only source UI/catalog files and the simulator; it does not read `local/`, installed runtime configuration, archive inventories, or logs.

## GitHub Pages

In the GitHub repository, choose **Settings → Pages → Build and deployment → Source → GitHub Actions**. The `Deploy live demo` workflow builds and deploys `dist/demo` on pushes to `main`, and can also be run manually.

The workflow requests only repository-content read access plus Pages/OIDC permissions in its deployment job. No custom token, root password, or Proxmox secret belongs in GitHub Actions secrets.

For a fork, update the repository link in `scripts/build-demo.py` and the live-demo URL in the README. The generated asset paths are relative, so the site works beneath a repository Pages path.

## Tests

```bash
npm run test:demo
```

The browser scenario runs in both EN and PL beneath a repository-like subpath and verifies language changes without losing draft Notes, simulated backups, cached archive inventory, reset, mobile layout, absence of API requests, and absence of stored credentials.
