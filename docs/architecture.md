# Architecture

```text
Browser / Glance iframe
        │ HTTPS + short-lived bearer session
        ▼
Python HTTP controller on the Proxmox host
        ├── pvesh GET: guests, storage, active tasks, archive inventory
        ├── mount unit / normal umount: configured UUID only
        └── independent transient systemd queue unit
                       └── worker.py
                              ├── vzdump guest A + Notes A
                              └── vzdump guest B + Notes B
```

## Controller

`app/server.py` serves the static UI and a small fixed API. Authentication uses scrypt password hashes and HMAC-signed 12-hour tokens. Tokens stay in the iframe's session storage (or memory where third-party storage is unavailable). There is no CORS API, cookie-based authorization, arbitrary command endpoint, or browser-side PVE token.

A process lock permits only one controller. A reentrant lock serializes mutations and snapshots within it. Root-only JSON is written atomically with filesystem sync, including state **before** launching a queue. Corrupt authoritative queue state prevents startup rather than allowing a duplicate backup.

## Queue

Each queue has a durable plan, log, and transient systemd service. The worker runs a separate native `vzdump` for each selected guest so Notes can differ. It checks storage and the expected writable USB filesystem before every guest. A native backup error is retained while subsequent guests may run; a missing/wrong drive stops the queue.

The controller watches systemd and consumes the log to update guest progress. A stopped controller does not kill the queue. Rebooting the entire host is different: a missing transient unit becomes uncertain and requires state reconciliation, not an automatic retry.

## USB lifecycle

udev starts a native `.mount` unit on insertion. Manual mount uses that same unit, with before/after checks. Unmount acquires `/var/run/vzdump.lock` using the same `flock` mechanism as Proxmox and checks active tasks. Only normal `umount` is used.

Automatic unmount occurs only after the **whole queue succeeds**, independently of browser state. Failure to unmount is reported separately from backup success.

## Inventory

The controller reads `GET /nodes/{node}/storage/{storage}/content?content=backup` only when the expected UUID/filesystem is mounted. Native completed archive filenames are accepted; temporary `.dat` files and auxiliary files are excluded.

Inventory reads are throttled, with forced reads after queue completion and before unmount. A successful inventory is persisted independently from queue state, so it remains visible after unplugging and service restart. The response explicitly identifies a cached read and its timestamp. Listing errors do not prevent safe eject or erase the previous inventory.

The list reports current guest names when a VMID still exists. Notes and archive filenames remain the historical backup metadata; guest renames do not rewrite old backups.
