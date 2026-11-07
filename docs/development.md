# Development and publication

## Backend

Python 3.11+ on Linux or macOS is sufficient for local mocked tests. Windows is not a target because the backend uses `fcntl` and native Linux operations in production.

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check.py
node --check app/static/panel.js
bash -n scripts/deploy.sh
```

Or use `make check`. Tests use temporary directories, mocked commands, and a loopback HTTP server; they do not require root, Proxmox, a USB device, or real credentials. Failure-path tests intentionally simulate failed tasks and unavailable storage.

## Browser scenarios

Node.js 22+ is optional development tooling, not a runtime dependency:

```bash
npm ci
npx playwright install chromium
npm run test:browser
```

The scenario runs in EN and PL and checks authentication, per-guest draft notes, language switching without data loss, locale formatting, payload isolation, backup progress, archive lists/caching, mount controls, session reload, network failure, and mobile overflow against synthetic responses. Screenshots go to ignored `test-results/`.

You can use an existing Chromium-family binary with `BROWSER_EXECUTABLE_PATH=/absolute/path/to/browser`. To regenerate documentation screenshots from the synthetic scenario:

```bash
SCREENSHOT_DIR=docs/images npm run test:browser
```

Never replace documentation screenshots with production names, network details, or private Notes. The checked-in previews use demo data.

The command generates `panel-desktop-en.png`, `panel-mobile-en.png`, `panel-desktop-pl.png` and `panel-mobile-pl.png`. Keep both language versions current. Translation keys and API error templates are checked by `tests/test_i18n.py`; add matching entries for each language in the shared catalog.

## Public export

```bash
python3 scripts/export-public.py
```

The archive is assembled from a public allowlist. Git metadata, ignored `local/` settings, IDE metadata, installed runtime JSON, keys, logs, and queue plans are excluded. Owner names and numeric IDs are stripped from tar headers.

An existing Git repository can still contain private files in old commits even after they were untracked. The export does not rewrite or claim to clean that history. Extract the archive into a new directory and initialize a new repository there when publishing a formerly private working tree.

The export includes documentation, MIT license, examples, tests, and GitHub CI. It does not create a remote, commit, repository, release, or push anything.
