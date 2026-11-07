import ast
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import i18n

spec = importlib.util.spec_from_file_location("language_installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class TranslationTests(unittest.TestCase):
    def test_catalog_keys_and_interpolation_fields_match(self):
        en, pl = i18n.CATALOG["ui"]["en"], i18n.CATALOG["ui"]["pl"]
        self.assertEqual(en.keys(), pl.keys())
        fields = lambda text: set(re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", text))
        for key in en:
            with self.subTest(key=key):
                self.assertEqual(fields(en[key]), fields(pl[key]))
        for entry in i18n.CATALOG["errors"]:
            self.assertEqual(fields(entry["en"]), fields(entry["pl"]))

    def test_html_and_demo_static_keys_exist(self):
        source = (ROOT / "app/static/index.html").read_text() + (ROOT / "scripts/build-demo.py").read_text()
        keys = re.findall(r'data-i18n(?:-aria)?="([a-z_]+)"', source)
        self.assertTrue(keys)
        for key in keys:
            self.assertIn(key, i18n.CATALOG["ui"]["en"])

    def test_known_errors_translate_both_directions(self):
        self.assertEqual(i18n.localize("Nieprawidłowe hasło panelu.", "en"), "Incorrect panel password.")
        self.assertEqual(i18n.localize("Incorrect panel password.", "pl"), "Nieprawidłowe hasło panelu.")
        original = "Backup zakończony, ale nie odmontowano USB: Trwa backup Proxmoxa. Poczekaj na jego zakończenie."
        english = i18n.localize(original, "en")
        self.assertEqual(english, "Backup completed, but USB was not unmounted: A Proxmox backup is running. Wait until it finishes.")
        self.assertEqual(i18n.localize(english, "pl"), original)
        self.assertIn("Storage demo", i18n.localize("Storage demo musi być aktywnym Directory z właściwą ścieżką i is_mountpoint=1.", "en"))

    def test_native_output_notes_names_and_logs_are_not_translated(self):
        text = "Nieprawidłowe hasło panelu."
        data = {"error": text, "job": {"error": text, "guests": [{"notes": text, "name": text}], "logs": [text]}}
        translated = i18n.payload(data, "en")
        self.assertEqual(translated["error"], "Incorrect panel password.")
        self.assertEqual(translated["job"]["error"], "Incorrect panel password.")
        self.assertEqual(translated["job"]["guests"][0], {"notes": text, "name": text})
        self.assertEqual(translated["job"]["logs"], [text])
        self.assertEqual(i18n.localize("mount: native technical error", "pl"), "mount: native technical error")

    def test_proxmox_double_braces_remain_literal(self):
        error = i18n.CATALOG["errors"][2]
        self.assertEqual(i18n.localize(error["pl"], "en"), error["en"])
        self.assertIn("{{guestname}}", i18n.localize(error["pl"], "en"))

    def test_fixed_application_errors_have_english_translations(self):
        messages = set()
        for name in ("server.py", "worker.py"):
            tree = ast.parse((ROOT / "app" / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id == "PanelError" and node.args and isinstance(node.args[0], ast.Constant):
                        messages.add(node.args[0].value)
                    for keyword in node.keywords:
                        if keyword.arg == "error" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                            messages.add(keyword.value.value)
        self.assertGreater(len(messages), 20)
        for message in messages:
            with self.subTest(message=message):
                self.assertNotEqual(i18n.localize(message, "en"), message)

    def test_language_header_and_safe_browser_configuration(self):
        self.assertEqual(i18n.language("en-GB,en;q=0.8"), "en")
        self.assertEqual(i18n.language("pl;q=0.9"), "pl")
        self.assertEqual(i18n.language("fr", "en"), "en")
        script = i18n.browser_script("en").decode()
        config = json.loads(script.removeprefix("window.HOMELAB_LOCALE = ").removesuffix(";\n"))
        self.assertEqual(config["language"], "en")
        self.assertEqual(set(config), {"language", "ui", "errors"})

    def test_install_selects_new_language_and_preserves_legacy_polish(self):
        with patch("builtins.input", return_value="pl"):
            self.assertEqual(installer.select_language(), "pl")
        with patch("builtins.input", return_value=""):
            self.assertEqual(installer.select_language(), "en")
        with patch("builtins.input", side_effect=AssertionError("Upgrades must not prompt")):
            self.assertEqual(installer.select_language(upgrading=True), "pl")
            self.assertEqual(installer.select_language("en", upgrading=True), "en")
        with self.assertRaises(ValueError):
            installer.select_language("fr")


if __name__ == "__main__":
    unittest.main()
