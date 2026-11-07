# Contributing

Thanks for helping make USB backup operations easier to use from Glance.

1. Keep changes focused and retain the dependency-free Python runtime.
2. Run `make check`; run the optional browser scenario for UI changes.
3. Add meaningful tests for queue state, USB lifecycle, authentication, or publication behavior you change.
4. Use only synthetic guest names, UUIDs, credentials, and archive metadata in tests/screenshots.
5. Describe the Proxmox version and filesystem for hardware-related findings; mocked CI cannot establish hardware compatibility.

Do not add force/lazy unmount, automatic deletion of old backups, or browser-side root/PVE tokens as incidental changes. Open a design discussion for changes to those behaviors.

Bug reports are most useful with reproduction steps, expected/actual behavior, version information, and redacted logs. Never attach runtime `config.json`, session tokens, private keys, or actual financial/personal backup contents.

The UI supports English and Polish through `app/static/translations.json`. Keep translation keys and interpolation fields aligned in both catalogs. Do not translate guest names, user Notes or native tool output. For UI changes, run the browser scenarios in both languages and refresh both sets of synthetic screenshots.
