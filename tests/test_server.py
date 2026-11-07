import contextlib
import hashlib
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server
import worker


def result(stdout="", code=0, stderr=""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=code)


def configuration(directory):
    salt = bytes.fromhex("01" * 16)
    return {"node": "proxmox", "uuid": "TEST-0001", "storage": "usb-backup",
            "mountpoint": "/mnt/usb-backup", "state_dir": directory,
            "password_salt": salt.hex(),
            "password_hash": hashlib.scrypt(b"test-password-long", salt=salt, n=16384, r=8, p=1).hex(),
            "session_secret": "02" * 32, "frame_ancestors": ["https://glance.example.test"]}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = configuration(self.temp.name)
        self.controller = server.Controller(self.config)
        self.lock_path = str(Path(self.temp.name) / "vzdump.lock")
        self.lock_patch = patch.object(server, "VZDUMP_LOCK", self.lock_path)
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)
        self.guests = [{"vmid": 100, "name": "homeassistant", "template": False, "type": "qemu"},
                       {"vmid": 106, "name": "budget-app", "template": False, "type": "lxc"}]
        self.controller.machines = Mock(return_value=self.guests)
        self.controller.validate_storage = Mock()
        self.controller.active_backups = Mock(return_value=[])
        self.controller.usb = Mock(return_value={"state": "mounted", "mounted": True, "free": 10 * 1024 ** 3})
        self.controller.backups = Mock()

    def start(self, auto_eject=True, notes=None):
        with patch.object(server.shutil, "which", return_value="/usr/bin/vzdump"), \
                patch.object(server, "command", return_value=result()) as execute:
            job = self.controller.start([106, 100], auto_eject, notes)
        return job, execute

    def system_state(self, sub="running", active="active", status="0", outcome="success"):
        return result(f"LoadState=loaded\nActiveState={active}\nSubState={sub}\nResult={outcome}\nExecMainStatus={status}\n")

    def test_native_backup_has_fixed_target_and_never_prunes(self):
        self.config["filesystem"] = "ext4"
        job, execute = self.start()
        args = execute.call_args.args[0]
        self.assertIn("--property=RemainAfterExit=yes", args)
        self.assertTrue(any(arg.endswith("worker.py") for arg in args))
        plan = json.loads(Path(args[args.index("--plan") + 1]).read_text())
        self.assertEqual(plan["storage"], "usb-backup")
        self.assertEqual(plan["filesystem"], "ext4")
        self.assertNotIn("password_hash", plan)
        self.assertNotIn("session_secret", plan)
        with patch.object(server.shutil, "which", return_value="/usr/bin/vzdump"):
            args = server.backup_arguments(plan["node"], plan["storage"], 100, "")
        self.assertEqual(args[args.index("--storage") + 1], "usb-backup")
        self.assertEqual(args[args.index("--prune-backups") + 1], "keep-all=1")
        self.assertEqual(args[args.index("--remove") + 1], "0")
        self.assertEqual(args[args.index("--tmpdir") + 1], "/var/tmp")
        self.assertIn("/usr/bin/vzdump", args)
        self.assertEqual([g["vmid"] for g in job["guests"]], [100, 106])
        self.assertEqual(json.loads(self.controller.state_file.read_text())["unit"], job["unit"])

    def test_state_is_durable_before_launch(self):
        def launch(*_args, **_kwargs):
            persisted = json.loads(self.controller.state_file.read_text())
            self.assertEqual(persisted["phase"], "starting")
            return result()
        with patch.object(server.shutil, "which", return_value="/usr/bin/vzdump"), \
                patch.object(server, "command", side_effect=launch):
            self.controller.start([100], False)

    def test_notes_use_native_template_and_preserve_multiline_and_backslashes(self):
        notes = "Przed aktualizacją / {{vmid}}\nKatalog C:\\backup"
        job, execute = self.start(notes={"100": notes, "106": "Osobny opis budżetu"})
        plan = json.loads(Path(execute.call_args.args[0][-1]).read_text())
        self.assertEqual(plan["guests"][1]["notes"], "Osobny opis budżetu")
        with patch.object(server.shutil, "which", return_value="/usr/bin/vzdump"):
            args = server.backup_arguments(plan["node"], plan["storage"], 100, plan["guests"][0]["notes"])
        self.assertEqual(args[args.index("--notes-template") + 1],
                         "{{guestname}} - Przed aktualizacją / {{vmid}}\\nKatalog C:\\\\backup")
        self.assertEqual(job["guests"][0]["notes"], notes)
        self.assertEqual(server.Controller(self.config).job["guests"][1]["notes"], "Osobny opis budżetu")

    def test_empty_notes_preserve_native_defaults(self):
        job, _ = self.start(notes={"100": "  \r\n "})
        with patch.object(server.shutil, "which", return_value="/usr/bin/vzdump"):
            self.assertNotIn("--notes-template", server.backup_arguments("proxmox", "usb-backup", 100, ""))
        self.assertTrue(all(guest["notes"] == "" for guest in job["guests"]))

    def test_invalid_notes_rejected_before_start(self):
        for notes in [None, 123, "x" * 501, "bad\x00note", "{{unknown}}"]:
            with self.subTest(notes=notes), self.assertRaises(server.PanelError):
                self.controller.start([100], True, {"100": notes})
        for notes in ["shared text", {"106": "not selected"}]:
            with self.subTest(notes=notes), self.assertRaises(server.PanelError):
                self.controller.start([100], True, notes)
        self.assertIsNone(self.controller.job)

    def test_notes_are_one_argument_not_a_shell_command(self):
        notes = 'Przed zmianą; $(reboot) "test"\r\nDruga linia'
        job, _ = self.start(notes={"100": notes})
        with patch.object(server.shutil, "which", return_value="/usr/bin/vzdump"):
            args = server.backup_arguments("proxmox", "usb-backup", 100, job["guests"][0]["notes"])
        self.assertEqual(args[-2:], ["--notes-template", '{{guestname}} - Przed zmianą; $(reboot) "test"\\nDruga linia'])
        self.assertEqual(job["guests"][0]["notes"], notes.replace("\r\n", "\n"))

    def test_no_backup_when_wrong_or_missing_usb(self):
        for state in ["wrong_device", "absent", "unmounted", "read_only"]:
            self.controller.usb.return_value = {"mounted": False, "state": state}
            with self.subTest(state=state), self.assertRaises(server.PanelError):
                self.controller.start([100], True)
        self.assertIsNone(self.controller.job)

    def test_rejects_remote_unknown_template_or_injected_id(self):
        for ids in [[999], [True], ["100; reboot"], [], [100, 100]]:
            with self.subTest(ids=ids), self.assertRaises(server.PanelError):
                self.controller.start(ids, True)
        self.guests[0]["template"] = True
        with self.assertRaises(server.PanelError):
            self.controller.start([100], True)

    def test_second_job_and_manual_eject_blocked_during_backup(self):
        self.start()
        with self.assertRaises(server.PanelError):
            self.controller.start([100], True)
        with self.assertRaises(server.PanelError):
            self.controller.eject()

    def test_progress_and_restart_recovery_then_auto_eject(self):
        job, _ = self.start()
        path = Path(self.temp.name) / f"{job['id']}.log"
        path.write_text("INFO: Starting Backup of VM 100 (qemu)\nINFO: Finished Backup of VM 100 (00:00:10)\nINFO: Starting Backup of VM 106 (lxc)\n")
        recovered = server.Controller(self.config)
        recovered.eject = Mock()
        with patch.object(server, "command", return_value=self.system_state()):
            recovered.refresh()
        self.assertEqual([g["backup"] for g in recovered.job["guests"]], ["ok", "running"])
        recovered.eject.assert_not_called()
        with path.open("a") as stream:
            stream.write("INFO: Finished Backup of VM 106 (00:00:10)\n")
        with patch.object(server, "command", return_value=self.system_state(sub="exited")):
            recovered.refresh()
        recovered.eject.assert_called_once_with(internal=True)
        self.assertEqual(recovered.job["phase"], "succeeded")
        self.assertEqual(recovered.job["eject"], "safe")
        self.assertEqual([g["backup"] for g in recovered.job["guests"]], ["ok", "ok"])
        # Polling a completed job must not eject a newly inserted pendrive.
        recovered.refresh()
        recovered.eject.assert_called_once()

    def test_failed_backup_never_auto_ejects(self):
        job, _ = self.start()
        (Path(self.temp.name) / f"{job['id']}.log").write_text("ERROR: Backup of VM 100 failed - no space\n")
        self.controller.eject = Mock()
        with patch.object(server, "command", return_value=self.system_state("failed", "failed", "255", "exit-code")):
            self.controller.refresh()
        self.controller.eject.assert_not_called()
        self.assertEqual(self.controller.job["phase"], "failed")
        self.assertEqual(self.controller.job["guests"][0]["backup"], "failed")

    def test_no_eject_when_option_disabled(self):
        self.start(False)
        self.controller.eject = Mock()
        with patch.object(server, "command", return_value=self.system_state(sub="exited")):
            self.controller.refresh()
        self.controller.eject.assert_not_called()
        self.assertEqual(self.controller.job["phase"], "succeeded")

    def test_eject_failure_preserves_successful_backup_result(self):
        self.start()
        self.controller.eject = Mock(side_effect=server.PanelError("target is busy"))
        with patch.object(server, "command", return_value=self.system_state(sub="exited")):
            self.controller.refresh()
        self.assertEqual(self.controller.job["phase"], "succeeded")
        self.assertEqual(self.controller.job["eject"], "failed")
        self.assertIn("target is busy", self.controller.job["error"])

    def test_native_vzdump_lock_prevents_unmount(self):
        with open(self.lock_path, "a") as lock:
            server.fcntl.flock(lock, server.fcntl.LOCK_EX | server.fcntl.LOCK_NB)
            with patch.object(server, "command") as execute, self.assertRaises(server.PanelError):
                self.controller.eject()
            execute.assert_not_called()

    def test_mount_uses_existing_systemd_unit_and_checks_result(self):
        self.controller.usb.side_effect = [
            {"mounted": False, "state": "unmounted"}, {"mounted": True, "state": "mounted"}]
        unit = r"mnt-usb\x2dbackup.mount"
        with patch.object(server, "command", side_effect=[result(unit + "\n"), result()]) as execute:
            self.controller.mount()
        self.assertEqual(execute.call_args_list[0].args[0],
                         ["/usr/bin/systemd-escape", "--path", "--suffix=mount", "/mnt/usb-backup"])
        self.assertEqual(execute.call_args_list[1].args[0], ["/usr/bin/systemctl", "start", "--", unit])

    def test_mount_refuses_missing_wrong_or_readonly_usb(self):
        for state in ["absent", "wrong_device", "read_only"]:
            self.controller.usb.return_value = {"mounted": False, "state": state}
            with self.subTest(state=state), patch.object(server, "command") as execute, self.assertRaises(server.PanelError):
                self.controller.mount()
            execute.assert_not_called()

    def test_mount_refuses_active_backups_and_busy_panel(self):
        self.controller.active_backups.return_value = [{"upid": "other"}]
        with self.assertRaises(server.PanelError):
            self.controller.mount()
        self.controller.active_backups.return_value = []
        self.start()
        with self.assertRaises(server.PanelError):
            self.controller.mount()

    def test_mount_never_reports_success_without_expected_device(self):
        self.controller.usb.return_value = {"mounted": False, "state": "unmounted"}
        with patch.object(server, "command", return_value=result(r"mnt-usb\x2dbackup.mount")), \
                self.assertRaises(server.PanelError):
            self.controller.mount()

    def test_other_active_task_prevents_unmount(self):
        self.controller.active_backups.return_value = [{"upid": "other"}]
        with patch.object(server, "command") as execute, self.assertRaises(server.PanelError):
            self.controller.eject()
        execute.assert_not_called()

    def test_unmount_is_not_forced_and_must_succeed(self):
        self.controller.usb.side_effect = [
            {"mounted": True, "state": "mounted"}, {"mounted": False, "state": "unmounted"}]
        with patch.object(server, "command", return_value=result()) as execute:
            self.controller.eject()
        self.assertEqual(execute.call_args.args[0], ["/usr/bin/umount", "--", "/mnt/usb-backup"])

    def test_uncertain_start_blocks_retry_and_auto_eject(self):
        with patch.object(server.shutil, "which", return_value="/usr/bin/vzdump"), \
                patch.object(server, "command", side_effect=subprocess.TimeoutExpired("systemd-run", 30)), \
                self.assertRaises(server.PanelError):
            self.controller.start([100], True)
        self.assertEqual(self.controller.job["phase"], "uncertain")
        with self.assertRaises(server.PanelError):
            self.controller.start([100], True)
        self.controller.active_backups.return_value = [{"upid": "possibly-started"}]
        with patch.object(server, "command", return_value=result("LoadState=not-found\n")), \
                self.assertRaises(server.PanelError):
            self.controller.acknowledge()
        self.assertEqual(self.controller.job["phase"], "uncertain")

    def test_manual_eject_clears_previous_eject_error_but_keeps_backup_result(self):
        self.start()
        self.controller.job.update(phase="succeeded", eject="failed", error="target is busy")
        self.controller.usb.side_effect = [
            {"mounted": True, "state": "mounted"}, {"mounted": False, "state": "unmounted"}]
        with patch.object(server, "command", return_value=result()):
            self.controller.eject()
        self.assertEqual(self.controller.job["phase"], "succeeded")
        self.assertEqual(self.controller.job["eject"], "safe")
        self.assertIsNone(self.controller.job["error"])

    def test_acknowledge_does_not_unlock_a_running_recovered_unit(self):
        self.start()
        self.controller.job["phase"] = "uncertain"
        with patch.object(server, "command", return_value=self.system_state()):
            self.controller.acknowledge()
        self.assertEqual(self.controller.job["phase"], "running")

    def test_usb_validates_uuid_and_read_write_mode(self):
        real = server.Controller(self.config)
        mount = {"target": "/mnt/usb-backup", "uuid": "OTHER", "fstype": "exfat", "options": "rw", "source": "/dev/sdb1"}
        with patch.object(server, "command", return_value=result(json.dumps({"filesystems": [mount]}))):
            self.assertEqual(real.usb()["state"], "wrong_device")
        mount.update(uuid="TEST-0001", options="ro")
        with patch.object(server, "command", return_value=result(json.dumps({"filesystems": [mount]}))):
            self.assertEqual(real.usb()["state"], "read_only")
        mount["options"] = "rw,nosuid,nodev,noexec"
        stat = SimpleNamespace(f_bavail=10, f_frsize=4096, f_blocks=100)
        with patch.object(server, "command", return_value=result(json.dumps({"filesystems": [mount]}))), \
                patch.object(server.os, "statvfs", return_value=stat):
            self.assertEqual(real.usb()["free"], 40960)

    def test_storage_must_require_exact_mountpoint(self):
        real = server.Controller(self.config)
        real.api = Mock(return_value={"type": "dir", "path": "/mnt/usb-backup", "content": "backup"})
        with self.assertRaises(server.PanelError):
            real.validate_storage()
        real.api.return_value["is_mountpoint"] = 1
        real.validate_storage()

    def test_corrupt_state_does_not_silently_clear_a_job(self):
        self.controller.state_file.write_text("{broken")
        with self.assertRaises(json.JSONDecodeError):
            server.Controller(self.config)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = configuration(self.temp.name)
        self.controller = server.Controller(self.config)
        self.controller.validate_storage = Mock()
        self.controller.api = Mock()
        self.controller.usb = Mock(return_value={"mounted": True, "state": "mounted"})
        self.volume = {"volid": "usb-backup:backup/vzdump-qemu-100-2026_11_07-10_00_00.vma.zst",
                       "ctime": 1000, "vmid": 100, "size": 4096, "notes": "homeassistant - przed aktualizacją\nDruga linia"}

    def test_inventory_filters_temporary_files_and_sorts_newest_first(self):
        newer = {**self.volume, "volid": "usb-backup:backup/vzdump-lxc-106-2026_11_07-11_00_00.tar.zst", "vmid": 106, "ctime": 2000}
        temporary = {**self.volume, "volid": "usb-backup:backup/vzdump-qemu-100-2026_11_07-10_00_00.vma.dat"}
        foreign = {**self.volume, "volid": "other:backup/vzdump-qemu-100-2026_11_07-10_00_00.vma.zst"}
        self.controller.api.return_value = [self.volume, temporary, foreign, newer]
        inventory = self.controller.backups(force=True)
        self.assertEqual([entry["vmid"] for entry in inventory["items"]], [106, 100])
        self.assertEqual(inventory["items"][1]["notes"], "homeassistant - przed aktualizacją")
        self.assertFalse(inventory["cached"])
        self.assertTrue(self.controller.inventory_file.exists())
        self.controller.api.assert_called_once_with("/nodes/proxmox/storage/usb-backup/content", "--content", "backup")

    def test_inventory_is_cached_after_unmount_and_survives_restart(self):
        self.controller.api.return_value = [self.volume]
        self.controller.backups(force=True)
        recovered = server.Controller(self.config)
        recovered.usb = Mock(return_value={"mounted": False, "state": "absent"})
        recovered.api = Mock()
        inventory = recovered.backups(force=True)
        self.assertTrue(inventory["cached"])
        self.assertEqual(len(inventory["items"]), 1)
        recovered.api.assert_not_called()

    def test_wrong_device_is_never_queried(self):
        self.controller.usb.return_value = {"mounted": False, "state": "wrong_device"}
        self.assertEqual(self.controller.backups(force=True)["items"], [])
        self.controller.api.assert_not_called()

    def test_inventory_error_preserves_previous_list_and_reports_stale(self):
        self.controller.api.return_value = [self.volume]
        first = self.controller.backups(force=True)
        self.controller.api.side_effect = server.PanelError("storage unavailable")
        inventory = self.controller.backups(force=True)
        self.assertEqual(inventory["items"], first["items"])
        self.assertEqual(inventory["updated"], first["updated"])
        self.assertTrue(inventory["cached"])
        self.assertIn("storage unavailable", inventory["error"])

    def test_inventory_reads_are_throttled(self):
        self.controller.api.return_value = [self.volume]
        self.controller.backups()
        self.controller.backups()
        self.controller.api.assert_called_once()

    def test_new_inventory_is_saved_before_unmount(self):
        self.controller.api.return_value = [self.volume]
        self.controller.active_backups = Mock(return_value=[])
        self.controller.usb.side_effect = [{"mounted": True, "state": "mounted"}, {"mounted": False, "state": "unmounted"}]
        def unmount(args, **kwargs):
            self.assertEqual(args, ["/usr/bin/umount", "--", "/mnt/usb-backup"])
            self.assertEqual(json.loads(self.controller.inventory_file.read_text())["items"][0]["vmid"], 100)
            return result()
        with patch.object(server, "VZDUMP_LOCK", str(Path(self.temp.name) / "vzdump.lock")), \
                patch.object(server, "command", side_effect=unmount):
            self.controller.eject()

    def test_inventory_failure_does_not_block_safe_eject(self):
        self.controller.api.side_effect = server.PanelError("Cannot list archives")
        self.controller.active_backups = Mock(return_value=[])
        self.controller.usb.side_effect = [{"mounted": True, "state": "mounted"}, {"mounted": False, "state": "unmounted"}]
        with patch.object(server, "VZDUMP_LOCK", str(Path(self.temp.name) / "vzdump.lock")), \
                patch.object(server, "command", return_value=result()) as execute:
            self.controller.eject()
        execute.assert_called_once_with(["/usr/bin/umount", "--", "/mnt/usb-backup"], timeout=120)
        self.assertIn("Cannot list archives", self.controller.inventory_error)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"node": "proxmox", "storage": "usb-backup", "guests": [
            {"vmid": 100, "notes": "Przed zmianą HA"}, {"vmid": 106, "notes": "Przed migracją budżetu"}]}
        self.controller = Mock()
        self.controller.usb.return_value = {"mounted": True, "free": 10 * 1024 ** 3}

    def test_guests_get_independent_native_notes_and_one_queue_result(self):
        with patch.object(worker, "Controller", return_value=self.controller), \
                patch.object(server.shutil, "which", return_value="/usr/bin/vzdump"), \
                patch.object(worker.subprocess, "run", return_value=result()) as execute:
            self.assertEqual(worker.run_plan(self.plan), 0)
        self.assertEqual(execute.call_count, 2)
        first, second = [call.args[0] for call in execute.call_args_list]
        self.assertEqual(first[1], "100")
        self.assertEqual(first[-1], "{{guestname}} - Przed zmianą HA")
        self.assertEqual(second[1], "106")
        self.assertEqual(second[-1], "{{guestname}} - Przed migracją budżetu")

    def test_backup_failure_is_retained_while_remaining_guests_run(self):
        with patch.object(worker, "Controller", return_value=self.controller), \
                patch.object(server.shutil, "which", return_value="/usr/bin/vzdump"), \
                patch.object(worker.subprocess, "run", side_effect=[result(code=1), result()]) as execute:
            self.assertEqual(worker.run_plan(self.plan), 1)
        self.assertEqual(execute.call_count, 2)

    def test_usb_disappearance_stops_queue_before_next_guest(self):
        self.controller.usb.side_effect = [{"mounted": True, "free": 10 * 1024 ** 3}, {"mounted": False}]
        with patch.object(worker, "Controller", return_value=self.controller), \
                patch.object(server.shutil, "which", return_value="/usr/bin/vzdump"), \
                patch.object(worker.subprocess, "run", return_value=result()) as execute:
            self.assertEqual(worker.run_plan(self.plan), 1)
        execute.assert_called_once()


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configuration("unused")
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.config = cls.config
        cls.httpd.auth = server.Auth(cls.config)
        cls.httpd.controller = Mock()
        cls.httpd.controller.snapshot.return_value = {"usb": {"mounted": False}}
        cls.httpd.controller.start.return_value = {"phase": "starting"}
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def request(self, method, path, data=None, token=None, origin=True):
        host = f"127.0.0.1:{self.httpd.server_port}"
        connection = http.client.HTTPConnection(host)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if origin:
            headers["Origin"] = f"http://{host}"
        connection.request(method, path, None if data is None else json.dumps(data), headers)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body, response.headers

    def test_read_requires_login(self):
        status, _, headers = self.request("GET", "/api/status")
        self.assertEqual(status, 401)
        self.assertIn("frame-ancestors", headers["Content-Security-Policy"])

    def test_login_and_authenticated_backup(self):
        status, body, _ = self.request("POST", "/api/login", {"password": "test-password-long"})
        self.assertEqual(status, 200)
        token = json.loads(body)["token"]
        status, _, _ = self.request("POST", "/api/backup", {"vmids": [100], "auto_eject": True, "notes": {"100": "Przed aktualizacją"}}, token)
        self.assertEqual(status, 202)
        self.httpd.controller.start.assert_called_with([100], True, {"100": "Przed aktualizacją"})

    def test_unauthenticated_or_cross_origin_changes_rejected(self):
        self.assertEqual(self.request("POST", "/api/eject", {})[0], 401)
        token = self.httpd.auth.login("test-password-long", "test-client")
        self.assertEqual(self.request("POST", "/api/eject", {}, token, origin=False)[0], 403)

    def test_mount_requires_authentication_and_calls_only_mount(self):
        self.assertEqual(self.request("POST", "/api/mount", {})[0], 401)
        token = self.httpd.auth.login("test-password-long", "mount-client")
        self.assertEqual(self.request("POST", "/api/mount", {}, token, origin=False)[0], 403)
        self.httpd.controller.mount.reset_mock()
        self.assertEqual(self.request("POST", "/api/mount", {}, token)[0], 200)
        self.httpd.controller.mount.assert_called_once_with()

    def test_multiple_guest_notes_can_exceed_old_single_note_body_limit(self):
        token = self.httpd.auth.login("test-password-long", "notes-client")
        notes = {str(vmid): "ą" * 500 for vmid in range(100, 108)}
        status, _, _ = self.request("POST", "/api/backup", {"vmids": list(range(100, 108)), "auto_eject": True, "notes": notes}, token)
        self.assertEqual(status, 202)
        self.httpd.controller.start.assert_called_with(list(range(100, 108)), True, notes)

    def test_static_assets_and_path_traversal(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/panel.js")[0], 200)
        self.assertEqual(self.request("GET", "/../server.py")[0], 404)
        self.assertEqual(self.request("GET", "/demo.js")[0], 404)

    def test_tampered_and_expired_tokens(self):
        auth = server.Auth(self.config)
        token = auth.login("test-password-long", "test")
        self.assertTrue(auth.valid(token))
        self.assertFalse(auth.valid(token[:-1] + ("0" if token[-1] != "0" else "1")))
        with patch.object(server.time, "time", return_value=time.time() + 50000):
            self.assertFalse(auth.valid(token))

    def test_password_attempts_are_rate_limited(self):
        auth = server.Auth(self.config)
        for _ in range(8):
            with self.assertRaises(server.PanelError):
                auth.login("wrong", "test")
        with self.assertRaisesRegex(server.PanelError, "Zbyt wiele"):
            auth.login("test-password-long", "test")


if __name__ == "__main__":
    unittest.main()
