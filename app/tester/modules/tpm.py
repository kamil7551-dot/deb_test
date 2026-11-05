"""TPM: presence, version (1.2/2.0), manufacturer, firmware version, ownership state.

No TPM device usually means it is disabled in BIOS (or the machine has none). Clearing the TPM is
intentionally not done here: it needs a reboot and operator confirmation (future opt-in module).
"""

from __future__ import annotations

import re
from pathlib import Path

from tester.core.module import Result
from tester.core.util import read_text
from tester.core.util import run as sh

ID = "tpm"
NAME = "TPM"
KIND = "auto"
CONTRACT = 1
ORDER = 40

VENDORS = {
    "IFX": "Infineon", "NTC": "Nuvoton", "STM": "STMicroelectronics", "INTC": "Intel (PTT)", "AMD": "AMD (fTPM)",
    "ATML": "Atmel", "BRCM": "Broadcom", "MSFT": "Microsoft (Pluton)", "NSM": "National Semiconductor",
    "QCOM": "Qualcomm", "ROCC": "Fuzhou Rockchip", "SMSN": "Samsung", "SNS": "Sinosun", "TXN": "Texas Instruments",
    "WEC": "Winbond", "IBM": "IBM", "HPE": "HPE", "GOOG": "Google", "LEN": "Lenovo",
}


def _hex(text: str, prop: str) -> int | None:
    m = re.search(rf"{prop}:\s*\n\s*raw:\s*(0x[0-9a-fA-F]+)", text) or re.search(rf"{prop}:\s*(0x[0-9a-fA-F]+)", text)
    return int(m.group(1), 16) if m else None


def _ascii(v: int | None) -> str | None:
    if v is None:
        return None
    s = v.to_bytes(4, "big").decode("ascii", "replace").strip("\x00 ")
    return s or None


def parse_tpm2_fixed(text: str) -> dict:
    data: dict = {}
    fam = re.search(r"TPM2_PT_FAMILY_INDICATOR:\s*\n\s*raw:.*\n\s*value:\s*\"?([\d.]+)", text)
    data["version"] = fam.group(1) if fam else "2.0"
    code = _ascii(_hex(text, "TPM2_PT_MANUFACTURER"))
    if code:
        data["manufacturer_code"] = code
        data["manufacturer"] = VENDORS.get(code, code)
    fw1, fw2 = _hex(text, "TPM2_PT_FIRMWARE_VERSION_1"), _hex(text, "TPM2_PT_FIRMWARE_VERSION_2")
    if fw1 is not None:
        fw = f"{fw1 >> 16}.{fw1 & 0xFFFF}"
        if fw2 is not None:
            fw += f".{fw2 >> 16}.{fw2 & 0xFFFF}"
        data["firmware"] = fw
    vs = [_ascii(_hex(text, f"TPM2_PT_VENDOR_STRING_{i}")) for i in range(1, 5)]
    vendor_string = "".join(v for v in vs if v)
    if vendor_string:
        data["vendor_string"] = vendor_string
    rev = re.search(r"TPM2_PT_REVISION:\s*\n\s*raw:.*\n\s*value:\s*([\d.]+)", text)
    if rev:
        data["spec_revision"] = rev.group(1)
    return data


def parse_tpm2_variable(text: str) -> dict:
    out = {}
    for key, name in (("ownerAuthSet", "owner_auth_set"), ("endorsementAuthSet", "endorsement_auth_set"),
                      ("lockoutAuthSet", "lockout_auth_set"), ("inLockout", "in_lockout")):
        m = re.search(rf"{key}:\s*(\d)", text)
        if m:
            out[name] = m.group(1) == "1"
    m = re.search(r"TPM2_PT_STARTUP_CLEAR:.*?phEnable:\s*(\d)", text, re.S)
    if m:
        out["platform_hierarchy_enabled"] = m.group(1) == "1"
    return out


def parse_tpm12_caps(text: str) -> dict:
    data = {"version": "1.2"}
    m = re.search(r"Manufacturer:\s*0x([0-9a-fA-F]+)", text)
    if m:
        code = _ascii(int(m.group(1), 16))
        if code:
            data["manufacturer_code"] = code
            data["manufacturer"] = VENDORS.get(code, code)
    m = re.search(r"Firmware version:\s*([\d.]+)", text)
    if m:
        data["firmware"] = m.group(1)
    m = re.search(r"TCG version:\s*([\d.]+)", text)
    if m:
        data["version"] = m.group(1)
    return data


def run(ctx) -> Result:
    dev = Path("/sys/class/tpm/tpm0")
    if not dev.exists():
        return Result("warn", {"present": False}, "Brak TPM (wyłączony w BIOS lub brak układu)")
    data: dict = {"present": True}
    major = read_text(dev / "tpm_version_major")
    if major == "2" or (major is None and Path("/dev/tpmrm0").exists()):
        fixed = sh(["tpm2_getcap", "properties-fixed"], timeout=20) or ""
        data.update(parse_tpm2_fixed(fixed))
        data.update(parse_tpm2_variable(sh(["tpm2_getcap", "properties-variable"], timeout=20) or ""))
        if not fixed:
            data["version"] = "2.0"
            data["note"] = "tpm2_getcap niedostępne"
    else:
        data.update(parse_tpm12_caps(read_text(dev / "device" / "caps") or ""))
        for key in ("enabled", "active", "owned"):
            v = read_text(dev / "device" / key)
            if v in ("0", "1"):
                data[key] = v == "1"
    ppi = read_text(dev / "ppi" / "version")
    if ppi:
        data["ppi_version"] = ppi
    note = ""
    if data.get("owner_auth_set") or data.get("owned"):
        note = "TPM ma ustawionego właściciela (do wyczyszczenia przed sprzedażą)"
    return Result("warn" if note else "pass", data, note)
