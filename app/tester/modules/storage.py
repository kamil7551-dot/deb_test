"""Internal disks: model, serial, capacity, type (NVMe/SSD/HDD), SMART health, wear, power-on hours.

The tester's own USB stick and other USB disks are excluded (option include_usb: true to keep USB disks).
Options: required (bool, fail when no disk), min_health_pct (int, default 10).
"""

from __future__ import annotations

import json

from tester.core.module import Result
from tester.core.util import run as sh

ID = "storage"
NAME = "Dyski"
KIND = "auto"
CONTRACT = 1
ORDER = 50

OWN_LABELS = {"TESTERCFG", "ITAD-TESTER", "VENTOY", "VTOYEFI"}
SKIP_PREFIX = ("loop", "sr", "zram", "ram", "fd", "mmcblk0boot", "mmcblk1boot")


def _labels(node: dict) -> set[str]:
    out = {str(node.get("label") or "").upper()}
    for ch in node.get("children") or []:
        out |= _labels(ch)
    return out


def list_disks(lsblk_json: str, include_usb: bool = False) -> list[dict]:
    try:
        devs = json.loads(lsblk_json).get("blockdevices", [])
    except (ValueError, AttributeError):
        return []
    disks = []
    for d in devs:
        if d.get("type") != "disk" or str(d.get("name", "")).startswith(SKIP_PREFIX):
            continue
        if _labels(d) & OWN_LABELS:
            continue
        if d.get("tran") == "usb" and not include_usb:
            continue
        if int(d.get("size") or 0) < 1_000_000_000:   # skip tiny/virtual devices
            continue
        disks.append({
            "name": d["name"],
            "model": (d.get("model") or "").strip() or None,
            "serial": (d.get("serial") or "").strip() or None,
            "size_gb": round(int(d["size"]) / 1e9),
            "transport": d.get("tran"),
            "rotational": d.get("rota") in (True, 1, "1"),
        })
    return disks


def _attr(table: list, attr_id: int) -> dict | None:
    for a in table:
        if a.get("id") == attr_id:
            return a
    return None


def parse_smart(js: str) -> dict:
    try:
        s = json.loads(js)
    except ValueError:
        return {"smart": "unavailable"}
    out: dict = {}
    proto = (s.get("device") or {}).get("protocol")
    if s.get("model_name"):
        out["model"] = s["model_name"]
    if s.get("serial_number"):
        out["serial"] = s["serial_number"]
    if s.get("firmware_version"):
        out["firmware"] = s["firmware_version"]
    if proto == "NVMe":
        out["type"] = "NVMe"
    elif s.get("rotation_rate") == 0:
        out["type"] = "SSD"
    elif isinstance(s.get("rotation_rate"), int):
        out["type"] = "HDD"
        out["rpm"] = s["rotation_rate"]
    if "smart_status" in s:
        out["smart_passed"] = bool(s["smart_status"].get("passed"))
    hours = (s.get("power_on_time") or {}).get("hours")
    if hours is not None:
        out["power_on_hours"] = hours
    nv = s.get("nvme_smart_health_information_log")
    if nv:
        if nv.get("percentage_used") is not None:
            out["health_pct"] = max(0, 100 - int(nv["percentage_used"]))
        out["media_errors"] = nv.get("media_errors")
        out["critical_warning"] = nv.get("critical_warning")
        out["data_written_tb"] = round(nv.get("data_units_written", 0) * 512000 / 1e12, 1)
    table = ((s.get("ata_smart_attributes") or {}).get("table")) or []
    if table:
        for aid in (231, 233, 169, 177, 202):        # vendor-specific SSD life attributes (normalised value)
            a = _attr(table, aid)
            if a and 0 < int(a.get("value", 0)) <= 100:
                out["health_pct"] = int(a["value"])
                break
        for aid, key in ((5, "reallocated_sectors"), (197, "pending_sectors"), (198, "uncorrectable_sectors")):
            a = _attr(table, aid)
            if a is not None:
                out[key] = int((a.get("raw") or {}).get("value", 0))
    if "smart_passed" not in out and not nv and not table:
        out["smart"] = "unavailable"
    return out


def evaluate(disks: list[dict], required: bool, min_health: int) -> tuple[str, str]:
    if not disks:
        return ("fail" if required else "warn"), "Brak dysku wewnętrznego"
    problems, warns = [], []
    for d in disks:
        label = d.get("model") or d["name"]
        if d.get("smart_passed") is False:
            problems.append(f"{label}: SMART FAILED")
        if d.get("critical_warning"):
            problems.append(f"{label}: NVMe critical warning {d['critical_warning']}")
        if d.get("health_pct") is not None and d["health_pct"] < min_health:
            problems.append(f"{label}: zużycie, zostało {d['health_pct']}%")
        for key, txt in (("reallocated_sectors", "realokowane"), ("pending_sectors", "oczekujące"),
                         ("uncorrectable_sectors", "nienaprawialne")):
            if d.get(key):
                warns.append(f"{label}: {d[key]} sektorów {txt}")
        if d.get("media_errors"):
            warns.append(f"{label}: {d['media_errors']} błędów nośnika")
        if d.get("smart") == "unavailable":
            warns.append(f"{label}: SMART niedostępny")
    if problems:
        return "fail", "; ".join(problems + warns)
    if warns:
        return "warn", "; ".join(warns)
    return "pass", ""


def run(ctx) -> Result:
    opts = ctx.options
    lsblk = sh(["lsblk", "-J", "-b", "-o", "NAME,TYPE,TRAN,SIZE,MODEL,SERIAL,ROTA,LABEL"]) or "{}"
    disks = list_disks(lsblk, bool(opts.get("include_usb", False)))
    for d in disks:
        d.update(parse_smart(sh(["smartctl", "-j", "-a", f"/dev/{d['name']}"], timeout=60) or ""))
        if "type" not in d:
            d["type"] = "HDD" if d.get("rotational") else "SSD"
    status, note = evaluate(disks, bool(opts.get("required", False)), int(opts.get("min_health_pct", 10)))
    if status == "warn" and opts.get("smart_required") and any(d.get("smart") == "unavailable" for d in disks):
        status = "fail"
    return Result(status, {"disks": disks, "count": len(disks)}, note)
