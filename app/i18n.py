"""Shared EN/PL catalog for the browser and human-readable API responses."""

import json
from pathlib import Path
import re

CATALOG = json.loads((Path(__file__).parent / "static/translations.json").read_text(encoding="utf-8"))


def language(value, default="pl"):
    candidate = str(value or "").split(",", 1)[0].split(";", 1)[0].split("-", 1)[0].lower()
    return candidate if candidate in CATALOG["ui"] else default


def localize(text, target, depth=0):
    if not isinstance(text, str) or depth > 3:
        return text
    target = language(target)
    for entry in CATALOG["errors"]:
        for source in ("pl", "en"):
            template = entry[source]
            # Double-brace Proxmox placeholders are literal, not our interpolation fields.
            tokens = list(re.finditer(r"(?<!\{)\{([a-z_]+)\}(?!\})", template))
            pattern, position = "", 0
            for token in tokens:
                pattern += re.escape(template[position:token.start()]) + f"(?P<{token[1]}>.+)"
                position = token.end()
            pattern += re.escape(template[position:])
            match = re.fullmatch(pattern, text, re.DOTALL)
            if match:
                result = entry[target]
                for key, value in match.groupdict().items():
                    result = result.replace("{" + key + "}", localize(value, target, depth + 1))
                return result
    return text  # Preserve native tool output and all unknown/user-authored text.


def payload(data, target):
    if isinstance(data, list):
        return [payload(item, target) for item in data]
    if isinstance(data, dict):
        return {key: localize(value, target) if key in {"error", "message", "monitor_error"}
                else payload(value, target) if isinstance(value, (dict, list)) else value
                for key, value in data.items()}
    return data


def browser_script(default="pl"):
    config = {"language": language(default), **CATALOG}
    return ("window.HOMELAB_LOCALE = " + json.dumps(config, ensure_ascii=True) + ";\n").encode()
