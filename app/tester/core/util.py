"""Small helpers shared by modules: running commands, reading sysfs, parsing dmidecode."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("tester")

# Serial numbers that BIOS vendors leave as placeholders. Compared after normalisation.
PLACEHOLDER_SERIALS = {
    "",
    "TOBEFILLEDBYOEM",
    "DEFAULTSTRING",
    "0123456789",
    "SYSTEMSERIALNUMBER",
    "NONE",
    "NOTAPPLICABLE",
    "NA",
    "NOTSPECIFIED",
    "CHASSISSERIALNUMBER",
    "INVALID",
    "OEM",
    "XXXXXXXXXX",
}


def normalize_serial(value: str | None) -> str:
    return re.sub(r"[\s\-_.]", "", (value or "")).upper()


def is_placeholder_serial(value: str | None) -> bool:
    norm = normalize_serial(value)
    if norm in PLACEHOLDER_SERIALS:
        return True
    if set(norm) <= {"0"} or set(norm) <= {"F"} or set(norm) <= {"X"}:
        return True
    return len(norm) < 4


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def run(cmd: list[str], timeout: float = 20, check_rc: bool = False) -> str | None:
    """Run a command and return stdout, or None if it is missing, times out or (with check_rc) fails."""
    if not have(cmd[0]):
        log.debug("command not found: %s", cmd[0])
        return None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("command failed: %s: %s", cmd, exc)
        return None
    if check_rc and proc.returncode != 0:
        log.warning("command %s exited %s: %s", cmd, proc.returncode, proc.stderr.strip()[:300])
        return None
    return proc.stdout


def read_text(path: str | Path, default: str | None = None) -> str | None:
    try:
        return Path(path).read_text(errors="replace").strip()
    except OSError:
        return default


def read_int(path: str | Path) -> int | None:
    value = read_text(path)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def clean(value: str | None) -> str | None:
    """Return a DMI/SPD string or None for empty and placeholder-like values."""
    if value is None:
        return None
    value = value.strip()
    if not value or normalize_serial(value) in PLACEHOLDER_SERIALS or value.lower() in {"unknown", "not specified"}:
        return None
    return value


def parse_dmidecode(text: str) -> list[dict]:
    """Parse `dmidecode` output into a list of records: {"type": int, "title": str, "props": {key: value}}.

    Multi-line list values (e.g. "Characteristics:") are stored as lists.
    """
    records: list[dict] = []
    current: dict | None = None
    list_key: str | None = None
    for raw in text.splitlines():
        if raw.startswith("Handle "):
            m = re.search(r"DMI type (\d+)", raw)
            current = {"type": int(m.group(1)) if m else -1, "title": "", "props": {}}
            records.append(current)
            list_key = None
            continue
        if current is None or not raw.strip():
            continue
        if not raw.startswith("\t"):
            continue
        if raw.startswith("\t\t"):
            if list_key is not None:
                current["props"].setdefault(list_key, [])
                if isinstance(current["props"][list_key], list):
                    current["props"][list_key].append(raw.strip())
            continue
        line = raw.strip()
        if not current["title"] and ":" not in line:
            current["title"] = line
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if value:
                current["props"][key] = value
                list_key = None
            else:
                current["props"][key] = []
                list_key = key
        elif not current["title"]:
            current["title"] = line
    return records


def dmi_records(dmi_type: int, text: str | None = None) -> list[dict]:
    if text is None:
        text = run(["dmidecode", "-t", str(dmi_type)]) or ""
    return [r for r in parse_dmidecode(text) if r["type"] == dmi_type]


def parse_size_to_gb(value: str | None) -> float | None:
    """'8 GB' / '8192 MB' / '16 GiB' -> gigabytes."""
    if not value:
        return None
    m = re.match(r"\s*([\d.]+)\s*([KMGT]i?B)", value, re.I)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2).upper().replace("I", "")
    factor = {"KB": 1 / (1024 * 1024), "MB": 1 / 1024, "GB": 1, "TB": 1024}[unit]
    return round(num * factor, 2)


def parse_speed_mts(value: str | None) -> int | None:
    """'3200 MT/s' / '2666 MHz' -> 3200 / 2666."""
    if not value:
        return None
    m = re.search(r"(\d+)\s*(MT/s|MHz)", value)
    return int(m.group(1)) if m else None


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0
