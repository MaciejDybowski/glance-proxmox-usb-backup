#!/usr/bin/env python3
"""Glance Proxmox USB Backup controller. Python standard library only."""

import argparse
import contextlib
import copy
import fcntl
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

VZDUMP_LOCK = "/var/run/vzdump.lock"

class PanelError(Exception):
    pass


def normalize_notes(notes):
    if not isinstance(notes, str) or len(notes) > 500:
        raise PanelError("Notatka każdej maszyny może mieć maksymalnie 500 znaków.")
    notes = notes.replace("\r\n", "\n").replace("\r", "\n").strip()
    if any(ord(char) < 32 and char not in "\n\t" for char in notes):
        raise PanelError("Notatka zawiera niedozwolone znaki sterujące.")
    for placeholder in re.findall(r"\{\{([^{}]+)\}\}", notes):
        if placeholder not in {"cluster", "guestname", "node", "vmid"}:
            raise PanelError("Notatka obsługuje tylko znaczniki {{cluster}}, {{guestname}}, {{node}} i {{vmid}}.")
    return notes


def backup_arguments(node, storage, vmid, notes):
    vzdump = shutil.which("vzdump")
    if not vzdump:
        raise PanelError("Nie znaleziono vzdump na hoście.")
    notes = normalize_notes(notes)
    template = "{{guestname}} - " + notes.replace("\\", "\\\\").replace("\n", "\\n")
    return [vzdump, str(vmid), "--node", node, "--storage", storage,
            "--mode", "snapshot", "--compress", "zstd", "--prune-backups", "keep-all=1",
            "--remove", "0", "--tmpdir", "/var/tmp",
            *(["--notes-template", template] if notes else [])]


def command(args, timeout=30, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
    if check and result.returncode:
        raise PanelError((result.stderr or result.stdout or "Błąd polecenia")[-1500:].strip())
    return result


def atomic_json(path, data):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Controller:
    BUSY = {"starting", "running", "unmounting", "uncertain"}

    def __init__(self, config):
        self.config = config
        self.node = config["node"]
        self.storage = config["storage"]
        self.mountpoint = config["mountpoint"]
        self.uuid = config["uuid"]
        self.directory = Path(config["state_dir"])
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_file = self.directory / "state.json"
        # Corrupt state must stop startup, never silently permit a duplicate backup.
        self.job = json.loads(self.state_file.read_text()) if self.state_file.exists() else None
        self.lock = threading.RLock()
        self.monitor_error = None
        self.inventory_file = self.directory / "inventory.json"
        self.inventory = {"items": [], "updated": None}
        self.inventory_error = None
        self.inventory_attempt = float("-inf")
        if self.inventory_file.exists():
            try:
                saved = json.loads(self.inventory_file.read_text())
                if not isinstance(saved, dict) or not isinstance(saved.get("items"), list):
                    raise ValueError("Invalid inventory")
                self.inventory = saved
            except (ValueError, OSError):
                logging.warning("Cannot load cached USB inventory; it will be refreshed when mounted")

    def api(self, path, *args):
        output = command(["/usr/bin/pvesh", "get", path, *args, "--output-format", "json"])
        return json.loads(output.stdout)

    def persist(self):
        atomic_json(self.state_file, self.job)

    def machines(self):
        resources = self.api("/cluster/resources", "--type", "vm")
        return sorted([
            {"vmid": int(vm["vmid"]), "name": vm.get("name", str(vm["vmid"])),
             "type": vm["type"], "status": vm.get("status", "unknown"),
             "disk": vm.get("maxdisk", 0), "template": bool(vm.get("template", 0))}
            for vm in resources if vm.get("node") == self.node
            and vm.get("type") in {"qemu", "lxc"}
        ], key=lambda vm: vm["vmid"])

    def usb(self):
        result = command(["/usr/bin/findmnt", "--json", "--mountpoint", self.mountpoint,
                          "--output", "TARGET,SOURCE,FSTYPE,UUID,OPTIONS"], check=False)
        if result.returncode == 1:
            present = Path(f"/dev/disk/by-uuid/{self.uuid}").exists()
            return {"state": "unmounted" if present else "absent", "mounted": False}
        if result.returncode:
            raise PanelError("Nie można sprawdzić punktu montowania USB.")
        entries = json.loads(result.stdout)["filesystems"]
        if len(entries) != 1:
            return {"state": "wrong_device", "mounted": False}
        mount = entries[0]
        if (mount["target"] != self.mountpoint
                or (mount.get("uuid") or "").lower() != self.uuid.lower()
                or mount["fstype"] != self.config.get("filesystem", "exfat")):
            return {"state": "wrong_device", "mounted": False}
        if "rw" not in mount.get("options", "").split(","):
            return {"state": "read_only", "mounted": False}
        stat = os.statvfs(self.mountpoint)
        return {"state": "mounted", "mounted": True, "free": stat.f_bavail * stat.f_frsize,
                "total": stat.f_blocks * stat.f_frsize, "source": mount["source"]}

    def validate_storage(self):
        config = self.api(f"/storage/{self.storage}")
        if (config.get("type") != "dir" or config.get("path") != self.mountpoint
                or str(config.get("is_mountpoint")) != "1"
                or "backup" not in config.get("content", "").split(",")
                or config.get("disable")):
            raise PanelError(f"Storage {self.storage} musi być aktywnym Directory z właściwą ścieżką i is_mountpoint=1.")

    def active_backups(self):
        return self.api(f"/nodes/{self.node}/tasks", "--source", "active",
                        "--typefilter", "vzdump", "--limit", "1000")

    def backups(self, usb=None, force=False):
        with self.lock:
            usb = self.usb() if usb is None else usb
            if usb["mounted"] and (force or time.monotonic() - self.inventory_attempt >= 15):
                self.inventory_attempt = time.monotonic()
                try:
                    self.validate_storage()
                    volumes = self.api(f"/nodes/{self.node}/storage/{self.storage}/content", "--content", "backup")
                    items = []
                    for volume in volumes:
                        volid = volume.get("volid", "")
                        if not volid.startswith(f"{self.storage}:backup/"):
                            continue
                        filename = volid.rsplit("/", 1)[-1]
                        match = re.fullmatch(r"vzdump-(qemu|lxc)-(\d+)-.+\.(?:vma|tar)(?:\.(?:zst|gz|lzo))?", filename)
                        if not match:
                            continue  # Exclude temporary .dat files and auxiliary logs/notes.
                        items.append({"volid": volid, "filename": filename,
                                      "vmid": int(volume.get("vmid", match[2])), "type": match[1],
                                      "created": int(volume.get("ctime", 0)),
                                      "size": int(volume["size"]) if volume.get("size") is not None else None,
                                      "notes": str(volume.get("notes") or "").split("\n", 1)[0],
                                      "protected": bool(volume.get("protected", False))})
                    items.sort(key=lambda item: (item["created"], item["filename"]), reverse=True)
                    inventory = {"items": items, "updated": time.time()}
                    atomic_json(self.inventory_file, inventory)
                    self.inventory = inventory
                    self.inventory_error = None
                except (PanelError, subprocess.TimeoutExpired, OSError, ValueError, TypeError) as error:
                    # Inventory failure must not disable backup controls or prevent safe eject.
                    self.inventory_error = f"Nie można odczytać listy kopii: {error}"
                    logging.warning("Cannot refresh USB inventory: %s", error)
            return {**copy.deepcopy(self.inventory), "cached": not usb["mounted"] or bool(self.inventory_error),
                    "error": self.inventory_error}

    @contextlib.contextmanager
    def backup_lock(self):
        # Same flock used by PVE::VZDump::getlock; closes the check/unmount race.
        with open(VZDUMP_LOCK, "a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PanelError("Trwa backup Proxmoxa. Poczekaj na jego zakończenie.") from None
            yield

    def mount(self):
        with self.lock:
            if self.job and self.job["phase"] in self.BUSY:
                raise PanelError("Zadanie nie jest zakończone. Nie można zmieniać montowania USB.")
            self.validate_storage()
            with self.backup_lock():
                if self.active_backups():
                    raise PanelError("Na hoście trwa lub oczekuje inne zadanie backupu.")
                usb = self.usb()
                if usb["mounted"]:
                    return
                if usb["state"] != "unmounted":
                    raise PanelError("Podłącz właściwy pendrive. Nie można zamontować brakującego, innego lub uszkodzonego nośnika.")
                unit = command(["/usr/bin/systemd-escape", "--path", "--suffix=mount", self.mountpoint]).stdout.strip()
                command(["/usr/bin/systemctl", "start", "--", unit], timeout=120)
                if not self.usb()["mounted"]:
                    raise PanelError("Nie potwierdzono montowania właściwego pendrive’a w trybie zapisu.")

    def eject(self, internal=False):
        with self.lock:
            if not internal and self.job and self.job["phase"] in self.BUSY:
                raise PanelError("Zadanie nie jest zakończone. Nie można odmontować USB.")
            with self.backup_lock():
                if self.active_backups():
                    raise PanelError("Na hoście trwa lub oczekuje inne zadanie backupu.")
                usb = self.usb()
                if usb["state"] != "unmounted":
                    if not usb["mounted"]:
                        raise PanelError("Właściwy pendrive nie jest zamontowany.")
                    # Capture newly completed copies BEFORE automatic/manual unmount.
                    self.backups(usb=usb, force=True)
                    command(["/usr/bin/umount", "--", self.mountpoint], timeout=120)
                    # No lazy/force unmount. Success means the filesystem was flushed.
                    if self.usb()["state"] not in {"unmounted", "absent"}:
                        raise PanelError("USB nadal jest zamontowane.")
                if not internal and self.job and self.job["phase"] == "succeeded":
                    self.job.update(eject="safe", error=None)
                    self.persist()

    def start(self, vmids, auto_eject, notes=None):
        if (not isinstance(vmids, list) or not 1 <= len(vmids) <= 100
                or any(type(vmid) is not int or vmid < 100 for vmid in vmids)
                or len(set(vmids)) != len(vmids) or type(auto_eject) is not bool):
            raise PanelError("Wybierz poprawne ID maszyn i opcję odmontowania.")
        notes = {} if notes is None else notes
        if not isinstance(notes, dict) or any(key not in {str(vmid) for vmid in vmids} for key in notes):
            raise PanelError("Przekaż osobne notatki tylko dla wybranych ID maszyn.")
        notes = {str(vmid): normalize_notes(notes.get(str(vmid), "")) for vmid in vmids}
        with self.lock:
            if self.job and self.job["phase"] in self.BUSY:
                raise PanelError("Poprzednie zadanie jest w toku albo wymaga sprawdzenia.")
            available = {vm["vmid"]: vm for vm in self.machines()}
            if any(vmid not in available or available[vmid]["template"] for vmid in vmids):
                raise PanelError("Wybrana maszyna nie istnieje na tym hoście lub jest szablonem.")
            self.validate_storage()
            with self.backup_lock():
                if self.active_backups():
                    raise PanelError("Na hoście trwa już inne zadanie backupu.")
                usb = self.usb()
                if not usb["mounted"]:
                    raise PanelError("Podłącz właściwy pendrive i poczekaj na zamontowanie.")
                if usb["free"] < 1024 ** 3:
                    raise PanelError("Na USB pozostało mniej niż 1 GiB. Zwolnij miejsce.")
                vzdump = shutil.which("vzdump")
                if not vzdump:
                    raise PanelError("Nie znaleziono vzdump na hoście.")
                job_id = secrets.token_hex(12)
                self.job = {"id": job_id, "unit": f"glance-usb-job-{job_id}.service",
                            "phase": "starting", "started": time.time(), "auto_eject": auto_eject,
                            "guests": [{**available[vmid], "backup": "pending", "notes": notes[str(vmid)]} for vmid in sorted(vmids)],
                            "logs": [], "offset": 0, "eject": None, "error": None}
                self.persist()  # Save BEFORE requesting a durable systemd job.
                log_path = self.directory / f"{job_id}.log"
                log_path.touch(mode=0o600)
                plan_path = self.directory / f"{job_id}.plan.json"
                atomic_json(plan_path, {"node": self.node, "storage": self.storage, "uuid": self.uuid,
                                       "filesystem": self.config.get("filesystem", "exfat"),
                                       "mountpoint": self.mountpoint, "state_dir": str(self.directory),
                                       "guests": self.job["guests"]})
            try:
                # pvesh create /vzdump is synchronous in CLI mode. A separate
                # systemd unit runs native vzdump independently of this HTTP server.
                command(["/usr/bin/systemd-run", "--unit", self.job["unit"],
                         "--property=Type=exec", "--property=RemainAfterExit=yes",
                         f"--property=StandardOutput=append:{log_path}",
                         f"--property=StandardError=append:{log_path}", "--",
                         "/usr/bin/python3", str(Path(__file__).with_name("worker.py")),
                         "--plan", str(plan_path)], timeout=30)
            except (PanelError, subprocess.TimeoutExpired, OSError) as error:
                self.job.update(phase="uncertain", error=f"Niepotwierdzony start zadania: {error}")
                self.persist()
                raise PanelError("Nie potwierdzono startu. Użyj „Sprawdź zadanie”; nie uruchamiaj drugiej kopii.") from error
            return copy.deepcopy(self.job)

    def read_logs(self):
        path = self.directory / f"{self.job['id']}.log"
        if not path.exists():
            return
        with path.open("rb") as stream:
            stream.seek(self.job["offset"])
            for _ in range(4000):
                position = stream.tell()
                raw = stream.readline(16384)
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    stream.seek(position)
                    break
                line = raw.decode("utf-8", errors="replace").rstrip()
                self.job["logs"] = (self.job["logs"] + [line])[-80:]
                for pattern, status in [(r"Starting Backup of VM (\d+)", "running"),
                                        (r"Finished Backup of VM (\d+)", "ok"),
                                        (r"Backup of VM (\d+) failed", "failed")]:
                    match = re.search(pattern, line)
                    if match:
                        for guest in self.job["guests"]:
                            if guest["vmid"] == int(match[1]):
                                guest["backup"] = status
                self.job["offset"] = stream.tell()

    def refresh(self):
        with self.lock:
            if not self.job or self.job["phase"] not in self.BUSY:
                return
            if self.job["phase"] == "unmounting":
                self.finish_eject()
                return
            output = command(["/usr/bin/systemctl", "show", self.job["unit"],
                              "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus"], check=False)
            props = dict(line.split("=", 1) for line in output.stdout.splitlines() if "=" in line)
            if output.returncode or props.get("LoadState") != "loaded":
                if self.job["phase"] == "starting" and time.time() - self.job["started"] < 30:
                    return
                self.job.update(phase="uncertain", error="Brak jednostki zadania. Sprawdź historię Proxmoxa i odblokuj panel.")
                self.persist()
                return
            self.read_logs()
            if props.get("SubState") in {"running", "start", "start-pre", "start-post"} or props.get("ActiveState") == "activating":
                self.job.update(phase="running", error=None)
            elif props.get("SubState") == "exited" and props.get("Result") == "success" and props.get("ExecMainStatus") == "0":
                self.job.update(phase="succeeded", finished=time.time(), error=None)
                for guest in self.job["guests"]:
                    if guest["backup"] != "ok":
                        # Do not invent per-guest success if logs are incomplete.
                        guest["backup"] = "unknown"
                if self.job["auto_eject"]:
                    self.job["phase"] = "unmounting"
                    self.persist()
                    self.finish_eject()
                else:
                    self.backups(force=True)
            elif props.get("ActiveState") in {"failed", "inactive"}:
                self.job.update(phase="failed", finished=time.time(),
                                error="Backup zakończył się błędem. USB pozostaje zamontowane; sprawdź log.")
                for guest in self.job["guests"]:
                    if guest["backup"] == "pending":
                        guest["backup"] = "skipped"
                self.backups(force=True)
            else:
                self.job.update(phase="uncertain", error="Nieznany stan zadania systemd. Sprawdź historię Proxmoxa.")
            self.persist()

    def finish_eject(self):
        try:
            self.eject(internal=True)
            self.job["eject"] = "safe"
        except (PanelError, subprocess.TimeoutExpired, OSError) as error:
            self.job["eject"] = "failed"
            self.job["error"] = f"Backup zakończony, ale nie odmontowano USB: {error}"
        self.job["phase"] = "succeeded"
        self.persist()

    def acknowledge(self):
        with self.lock:
            self.refresh()
            if not self.job or self.job["phase"] != "uncertain":
                return
            with self.backup_lock():
                if self.active_backups():
                    raise PanelError("Zadanie backupu nadal działa. Nie można odblokować panelu.")
                self.job.update(phase="failed", error="Niepotwierdzone zadanie zamknięto. Sprawdź backupy w Proxmoxie.")
                self.persist()

    def snapshot(self):
        with self.lock:
            usb = self.usb()
            return {"node": self.node, "storage": self.storage, "uuid": self.uuid,
                    "drive_name": self.config.get("drive_name", "USB backup"),
                    "usb": usb, "machines": self.machines(), "job": copy.deepcopy(self.job),
                    "backups": self.backups(usb=usb),
                    "monitor_error": self.monitor_error}


class Auth:
    def __init__(self, config):
        self.salt = bytes.fromhex(config["password_salt"])
        self.password_hash = config["password_hash"]
        self.secret = bytes.fromhex(config["session_secret"])
        self.attempts = {}
        self.lock = threading.Lock()

    def login(self, password, address):
        with self.lock:
            now = time.time()
            self.attempts = {ip: value for ip, value in self.attempts.items() if value[1] > now - 300}
            count, _ = self.attempts.get(address, (0, now))
            if count >= 8:
                raise PanelError("Zbyt wiele prób logowania. Spróbuj ponownie za 5 minut.")
            self.attempts[address] = (count + 1, now)
            digest = hashlib.scrypt(password.encode(), salt=self.salt, n=16384, r=8, p=1).hex()
            if not hmac.compare_digest(digest, self.password_hash):
                raise PanelError("Nieprawidłowe hasło panelu.")
            self.attempts.pop(address, None)
        payload = f"{int(now + 43200)}.{secrets.token_hex(16)}"
        return payload + "." + hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()

    def valid(self, token):
        try:
            expiry, nonce, signature = token.split(".")
            payload = f"{expiry}.{nonce}"
            return int(expiry) > time.time() and hmac.compare_digest(
                signature, hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest())
        except (ValueError, TypeError):
            return False


class Handler(BaseHTTPRequestHandler):
    server_version = "HomelabBackup/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def respond(self, status, data, content_type="application/json; charset=utf-8"):
        body = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, (dict, list)) else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        ancestors = " ".join(self.server.config["frame_ancestors"])
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                         "style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
                         f"form-action 'self'; frame-ancestors 'self' {ancestors}")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        return self.server.auth.valid(self.headers.get("Authorization", "").removeprefix("Bearer "))

    def do_GET(self):
        try:
            if self.path == "/api/status":
                if not self.authorized():
                    return self.respond(401, {"error": "Zaloguj się do panelu."})
                return self.respond(200, self.server.controller.snapshot())
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/panel.css": ("panel.css", "text/css; charset=utf-8"),
                      "/panel.js": ("panel.js", "text/javascript; charset=utf-8")}
            if self.path not in assets:
                return self.respond(404, {"error": "Nie znaleziono."})
            name, content_type = assets[self.path]
            self.respond(200, (Path(__file__).parent / "static" / name).read_bytes(), content_type)
        except (PanelError, subprocess.TimeoutExpired, OSError, ValueError) as error:
            logging.exception("GET failed")
            self.respond(503, {"error": str(error)})

    def do_POST(self):
        try:
            origin = self.headers.get("Origin")
            if not origin or urlsplit(origin).netloc != self.headers.get("Host"):
                return self.respond(403, {"error": "Niedozwolone źródło żądania."})
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 256 * 1024 or self.headers.get_content_type() != "application/json":
                return self.respond(400, {"error": "Niepoprawny format lub zbyt duże żądanie JSON."})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise PanelError("Niepoprawne żądanie.")
            if self.path == "/api/login":
                password = data.get("password", "")
                if not isinstance(password, str) or len(password) > 256:
                    raise PanelError("Niepoprawne hasło.")
                return self.respond(200, {"token": self.server.auth.login(password, self.client_address[0])})
            if not self.authorized():
                return self.respond(401, {"error": "Sesja wygasła. Zaloguj się ponownie."})
            controller = self.server.controller
            if self.path == "/api/backup":
                return self.respond(202, {"job": controller.start(data.get("vmids"), data.get("auto_eject"), data.get("notes"))})
            if self.path == "/api/eject":
                controller.eject()
                return self.respond(200, {"message": "Można bezpiecznie wyjąć pendrive."})
            if self.path == "/api/mount":
                controller.mount()
                return self.respond(200, {"message": "Pendrive zamontowany. Możesz uruchomić backup."})
            if self.path == "/api/acknowledge":
                controller.acknowledge()
                return self.respond(200, {"message": "Sprawdzono stan zadania."})
            self.respond(404, {"error": "Nie znaleziono operacji."})
        except (PanelError, ValueError) as error:
            self.respond(409, {"error": str(error)})
        except (subprocess.TimeoutExpired, OSError) as error:
            logging.exception("POST failed")
            self.respond(503, {"error": str(error)})

    def log_message(self, message, *args):
        logging.info("%s %s", self.client_address[0], message % args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/glance-backup/config.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = json.loads(Path(args.config).read_text())
    # Enforce a single controller, including across separate manual invocations.
    instance = open(Path(config["state_dir"]) / "controller.lock", "a")
    fcntl.flock(instance, fcntl.LOCK_EX | fcntl.LOCK_NB)
    controller = Controller(config)

    def monitor():
        while True:
            try:
                controller.refresh()
                controller.monitor_error = None
            except Exception:
                controller.monitor_error = "Nie można potwierdzić stanu zadania. USB nie zostanie automatycznie odmontowane do czasu odzyskania połączenia z systemem."
                logging.exception("Cannot refresh backup; keeping the last state and USB mounted")
            time.sleep(3)

    threading.Thread(target=monitor, daemon=True).start()
    server = ThreadingHTTPServer((config["listen"], config["port"]), Handler)
    server.config, server.auth, server.controller = config, Auth(config), controller
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(config["tls_cert"], config["tls_key"])
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
