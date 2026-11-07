import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load_script("panel_install", "install.py")
exporter = load_script("panel_export", "export-public.py")
demo_builder = load_script("panel_demo", "build-demo.py")


class DemoBuildTests(unittest.TestCase):
    def test_demo_build_contains_only_static_assets_and_blocks_api_networking(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = demo_builder.build(Path(temporary) / "site")
            self.assertEqual({path.name for path in directory.iterdir()},
                             {"index.html", "panel.js", "panel.css", "demo.js", "demo.css", "i18n.js", "messages.js", ".nojekyll"})
            page = (directory / "index.html").read_text()
            self.assertIn("FICTIONAL DATA", page)
            self.assertIn("connect-src 'none'", page)
            self.assertLess(page.index('src="demo.js?'), page.index('src="panel.js?'))
            self.assertLess(page.index('src="messages.js?'), page.index('src="i18n.js?'))
            self.assertLess(page.index('src="i18n.js?'), page.index('src="demo.js?'))
            for asset in directory.iterdir():
                content = asset.read_text()
                self.assertNotIn("password_hash", content)
                self.assertNotIn("session_secret", content)
                self.assertIsNone(re.search(r"\b192[.]168[.]\d+[.]\d+\b", content))


class InstallerSettingsTests(unittest.TestCase):
    def settings(self):
        return {"uuid": "DEMO-0001", "storage": "backups", "mountpoint": "/mnt/backup-usb",
                "filesystem": "ext4", "drive_name": "Demo USB", "listen": "0.0.0.0",
                "port": 8765, "frame_ancestors": ["https://glance.example.test"]}

    def test_supported_filesystems_and_custom_mountpoint(self):
        for filesystem in ("exfat", "ext4", "xfs"):
            settings = {**self.settings(), "filesystem": filesystem}
            installer.validate_settings(settings)
            unit = installer.render_mount(settings)
            self.assertIn(f"Type={filesystem}", unit)
            self.assertIn("What=/dev/disk/by-uuid/DEMO-0001", unit)
            self.assertIn("Where=/mnt/backup-usb", unit)
            self.assertIn('ENV{ID_FS_UUID}=="DEMO-0001"', installer.render_udev(settings, "example.mount"))

    def test_rejects_invalid_or_example_settings_before_systemd_rendering(self):
        invalid = [("uuid", "REPLACE-WITH-FILESYSTEM-UUID"), ("uuid", 'bad"\nRUN+="x'),
                   ("storage", "../other"), ("mountpoint", "/"), ("mountpoint", "/mnt/../etc"),
                   ("mountpoint", "/mnt/two words"), ("filesystem", "vfat"), ("port", True),
                   ("port", 70000), ("listen", "::1"), ("frame_ancestors", ["https://example.test/path"]),
                   ("frame_ancestors", ["https://example.test; script-src *"])]
        for key, value in invalid:
            with self.subTest(key=key, value=value), self.assertRaises((ValueError, TypeError)):
                installer.validate_settings({**self.settings(), key: value})

    def test_storage_must_match_configured_mountpoint(self):
        config = {"type": "dir", "path": "/mnt/backup-usb", "content": "backup", "is_mountpoint": 1}
        installer.validate_storage(config, "backups", "/mnt/backup-usb")
        for changes in ({"path": "/elsewhere"}, {"is_mountpoint": 0}, {"disable": 1}, {"content": "iso"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                installer.validate_storage({**config, **changes}, "backups", "/mnt/backup-usb")


class PublicationTests(unittest.TestCase):
    def test_public_manifest_excludes_local_files_and_runtime_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for filename in exporter.PUBLIC_FILES:
                (root / filename).parent.mkdir(parents=True, exist_ok=True)
                (root / filename).write_text("example")
            for dirname in exporter.PUBLIC_DIRS:
                (root / dirname).mkdir(exist_ok=True)
            (root / "local").mkdir()
            (root / "local/private.yml").write_text("private")
            (root / "config/private-settings.json").write_text("private")
            (root / "app/config.json").write_text("private")
            (root / "app/key.pem").write_text("private")
            (root / "app/debug.log").write_text("private")
            (root / "app/server.py").write_text("public")
            paths = {str(path.relative_to(root)) for path in exporter.public_paths(root)}
            self.assertIn("app/server.py", paths)
            self.assertNotIn("config/private-settings.json", paths)
            self.assertFalse(any("local" in path or path.endswith(("config.json", ".pem", ".log")) for path in paths))

    def test_public_source_has_no_personal_network_or_home_paths(self):
        for path in exporter.public_paths():
            if path.suffix in {".png", ".jpg"}:
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIsNone(re.search(r"\b192[.]168[.]\d+[.]\d+\b", text))
                self.assertIsNone(re.search(r"/Users/[A-Za-z0-9_-]+/", text))
                self.assertIsNone(re.search(r"[.]ts[.]net/", text))

    def test_install_settings_example_has_no_runtime_credentials(self):
        config = json.loads((ROOT / "config/install.example.json").read_text())
        self.assertTrue(config.keys() <= installer.SETTING_KEYS)
        self.assertNotIn("password_hash", config)
        self.assertNotIn("session_secret", config)


class DeploymentScriptTests(unittest.TestCase):
    def test_deploy_uploads_only_public_code_and_quotes_remote_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            log = directory / "commands.jsonl"
            stub = '#!' + sys.executable + '\nimport json,os,sys\nwith open(os.environ["COMMAND_LOG"],"a") as f: f.write(json.dumps(sys.argv)+"\\n")\n'
            for name in ("ssh", "scp", "rsync"):
                binary = directory / name
                binary.write_text(stub)
                binary.chmod(0o700)
            settings = directory / "settings.env"
            settings.write_text('USB_UUID=DEMO-0001\nPVE_STORAGE=backups\nUSB_FILESYSTEM=ext4\nUSB_DRIVE_NAME="Owner\'s USB"\n')
            environment = {**os.environ, "DEPLOY_SETTINGS": str(settings), "COMMAND_LOG": str(log),
                           "PATH": str(directory) + os.pathsep + os.environ["PATH"]}
            subprocess.run(["bash", str(ROOT / "scripts/deploy.sh"), "proxmox.example.test"], check=True,
                           capture_output=True, text=True, env=environment)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([Path(cmd[0]).name for cmd in commands], ["rsync", "ssh"])
            self.assertIn("--exclude=*", commands[0])
            try:
                remote = shlex.split(commands[1][-1])
            except ValueError as error:
                self.fail(f"Invalid remote quoting: {commands[1][-1]!r}: {error}")
            self.assertEqual(remote[:2], ["python3", "/root/glance-backup-upload/scripts/install.py"])
            self.assertEqual(remote[remote.index("--drive-name") + 1], "Owner's USB")
            self.assertNotIn("scp", [Path(cmd[0]).name for cmd in commands])
            # Upgrades without any explicit installer flags must also work on Bash 3.2.
            settings.write_text("")
            log.write_text("")
            subprocess.run(["bash", str(ROOT / "scripts/deploy.sh"), "proxmox.example.test"], check=True,
                           capture_output=True, text=True, env=environment)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(shlex.split(commands[1][-1]), ["python3", "/root/glance-backup-upload/scripts/install.py"])


if __name__ == "__main__":
    unittest.main()
