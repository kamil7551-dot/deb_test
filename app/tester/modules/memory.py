"""RAM modules: SMBIOS type 17 (always) + SPD EEPROM decode (when the bus allows), rated vs configured speed.

SPD is read with `decode-dimms` (i2c-tools) through the kernel drivers ee1004 (DDR4) and spd5118 (DDR5);
i2c-i801 / i2c-piix4 register them automatically on recent kernels. Soldered memory has no SPD, and some
laptops lock SMBus; then `spd` is "unavailable" for that module and SMBIOS data is used alone.
"""

from __future__ import annotations

import re

from tester.core.module import Result
from tester.core.util import clean, dmi_records, parse_size_to_gb, parse_speed_mts, read_text
from tester.core.util import run as sh

ID = "memory"
NAME = "Pamięć RAM"
KIND = "auto"
CONTRACT = 1
ORDER = 30

SPD_KMODS = ["i2c-dev", "i2c-i801", "i2c-piix4", "ee1004", "spd5118", "at24"]


def parse_smbios_modules(records: list[dict]) -> list[dict]:
    mods = []
    for r in records:
        p = r["props"]
        size = p.get("Size", "")
        if not size or "No Module" in size or size.startswith("0") or size == "Unknown":
            continue
        configured = p.get("Configured Memory Speed") or p.get("Configured Clock Speed")
        form = clean(p.get("Form Factor"))
        mods.append({
            "slot": clean(p.get("Locator")),
            "bank": clean(p.get("Bank Locator")),
            "size_gb": parse_size_to_gb(size),
            "type": clean(p.get("Type")),
            "form_factor": form,
            "soldered": (form or "").lower() == "row of chips",
            "manufacturer": clean(p.get("Manufacturer")),
            "part": clean(p.get("Part Number")),
            "serial": clean(p.get("Serial Number")),
            "rank": clean(p.get("Rank")),
            "rated_mts": parse_speed_mts(p.get("Speed")),
            "configured_mts": parse_speed_mts(configured),
            "voltage": clean(p.get("Configured Voltage")),
        })
    return mods


def parse_decode_dimms(text: str) -> list[dict]:
    """Split decode-dimms output per EEPROM and pick the useful fields."""
    out = []
    for block in re.split(r"^Decoding EEPROM:\s*", text, flags=re.M)[1:]:
        lines = block.splitlines()
        fields: dict[str, str] = {"_source": lines[0].strip()}
        for line in lines[1:]:
            m = re.match(r"^(\S.*?)\s{2,}(\S.*)$", line)
            if m:
                fields.setdefault(m.group(1).strip(), m.group(2).strip())
        speed = fields.get("Maximum module speed", "")
        spd = {
            "source": fields["_source"],
            "type": fields.get("Fundamental Memory type"),
            "module_type": fields.get("Module Type"),
            "size_gb": parse_size_to_gb(fields.get("Size")),
            "max_mts": parse_speed_mts(speed),
            "pc_name": (re.search(r"\((PC\d?-\d+)\)", speed) or [None, None])[1],
            "manufacturer": clean(fields.get("Module Manufacturer")),
            "dram_manufacturer": clean(fields.get("DRAM Manufacturer")),
            "part": clean(fields.get("Part Number")),
            "serial": clean(fields.get("Assembly Serial Number")),
            "manufactured": clean(fields.get("Manufacturing Date")),
            "ranks": clean(fields.get("Ranks")),
            "timings": clean(fields.get("AA-RCD-RP-RAS (cycles)")),
            "cas_latencies": clean(fields.get("Supported CAS Latencies")),
            "voltage": clean(fields.get("Module Nominal Voltage") or fields.get("Module Nominal Voltage, VDD")),
            "crc_ok": "OK" in fields.get("EEPROM CRC of bytes 0-125", fields.get("EEPROM CRC of bytes 0-116", "OK")),
        }
        out.append({k: v for k, v in spd.items() if v not in (None, "")})
    return out


def _norm_serial(s: str | None) -> str:
    s = (s or "").upper().replace("0X", "")
    return s.lstrip("0")


def merge(smbios: list[dict], spd: list[dict]) -> list[dict]:
    """Attach SPD records to SMBIOS modules: by serial, then part number, then position."""
    unused = list(spd)
    for m in smbios:
        match = None
        for s in unused:
            if m.get("serial") and _norm_serial(m["serial"]) == _norm_serial(s.get("serial")):
                match = s
                break
        if match is None:
            for s in unused:
                if m.get("part") and s.get("part") and m["part"].strip() == s["part"].strip():
                    match = s
                    break
        if match is None and unused and not m.get("soldered") and len(spd) == len([x for x in smbios if not x.get("soldered")]):
            match = unused[0]
        if match is not None:
            unused.remove(match)
            m["spd"] = "ok"
            m["spd_data"] = match
            if match.get("max_mts"):
                m["rated_mts"] = match["max_mts"]
            for key in ("manufacturer", "part", "serial"):
                if not m.get(key) and match.get(key):
                    m[key] = match[key]
        else:
            m["spd"] = "soldered" if m.get("soldered") else "unavailable"
    return smbios


def warnings_for(mods: list[dict], visible_gb: float | None, warn_speed: bool = True) -> list[str]:
    warns = []
    if warn_speed:
        for m in mods:
            if m.get("rated_mts") and m.get("configured_mts") and m["configured_mts"] < m["rated_mts"]:
                warns.append(f"{m.get('slot') or 'moduł'}: znamionowo {m['rated_mts']} MT/s, pracuje {m['configured_mts']} MT/s")
    socketed = [m for m in mods if not m.get("soldered")]
    if len(socketed) > 1:
        if len({m.get("size_gb") for m in socketed}) > 1:
            warns.append("Moduły o różnej pojemności")
        if len({(m.get("manufacturer") or "").lower() for m in socketed}) > 1 or len({m.get("part") for m in socketed}) > 1:
            warns.append("Moduły różnych producentów lub modeli")
    total = sum(m.get("size_gb") or 0 for m in mods)
    if visible_gb and total and visible_gb < total * 0.75:
        warns.append(f"System widzi tylko {visible_gb} GB z {total:g} GB zainstalowanych")
    return warns


def read_spd() -> list[dict]:
    for kmod in SPD_KMODS:
        sh(["modprobe", "-q", kmod], timeout=10)
    text = sh(["decode-dimms"], timeout=60) or ""
    return parse_decode_dimms(text)


def run(ctx) -> Result:
    records = dmi_records(17)
    mods = parse_smbios_modules(records)
    spd = read_spd()
    mods = merge(mods, spd)
    mem_kb = re.search(r"MemTotal:\s+(\d+)", read_text("/proc/meminfo") or "")
    visible = round(int(mem_kb.group(1)) / 1024 / 1024, 1) if mem_kb else None
    warns = warnings_for(mods, visible, bool(ctx.options.get("warn_if_speed_below_rated", True)))
    total = round(sum(m.get("size_gb") or 0 for m in mods), 1)
    data = {
        "total_gb": total or None,
        "visible_gb": visible,
        "slots_used": len(mods),
        "slots_total": len(records) or None,
        "type": next((m["type"] for m in mods if m.get("type")), None),
        "spd_read": sum(1 for m in mods if m.get("spd") == "ok"),
        "modules": mods,
        "warnings": warns,
    }
    if not mods:
        return Result("warn", data, "Brak danych o modułach w SMBIOS")
    note = "; ".join(warns)
    return Result("warn" if warns else "pass", data, note)
