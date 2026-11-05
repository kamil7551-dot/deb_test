"""BIOS/firmware: vendor, version, release date, board, asset tag, boot mode (UEFI/legacy), Secure Boot state."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from tester.core.module import Result
from tester.core.util import clean, dmi_records

ID = "bios"
NAME = "BIOS / firmware"
KIND = "auto"
CONTRACT = 1
ORDER = 20

EFI_GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
EFIVARS = Path("/sys/firmware/efi/efivars")


def efi_bool(name: str, efivars: Path = EFIVARS) -> bool | None:
    try:
        raw = (efivars / f"{name}-{EFI_GLOBAL}").read_bytes()
    except OSError:
        return None
    return bool(raw[4]) if len(raw) >= 5 else None


def iso_date(value: str | None) -> str | None:
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return value


def collect(t0: list, t1: list, t2: list, t3: list, efi_present: bool, efivars: Path = EFIVARS) -> dict:
    p0 = t0[0]["props"] if t0 else {}
    p2 = t2[0]["props"] if t2 else {}
    p3 = t3[0]["props"] if t3 else {}
    data = {
        "vendor": clean(p0.get("Vendor")),
        "version": clean(p0.get("Version")),
        "date": iso_date(clean(p0.get("Release Date"))),
        "revision": clean(p0.get("BIOS Revision")),
        "ec_firmware": clean(p0.get("Firmware Revision")),
        "board_manufacturer": clean(p2.get("Manufacturer")),
        "board_model": clean(p2.get("Product Name")),
        "board_serial": clean(p2.get("Serial Number")),
        "chassis_serial": clean(p3.get("Serial Number")),
        "asset_tag": clean(p3.get("Asset Tag")),
        "boot_mode": "uefi" if efi_present else "legacy",
    }
    if efi_present:
        data["secure_boot"] = efi_bool("SecureBoot", efivars)
        data["setup_mode"] = efi_bool("SetupMode", efivars)
    return {k: v for k, v in data.items() if v is not None}


def run(ctx) -> Result:
    data = collect(dmi_records(0), dmi_records(1), dmi_records(2), dmi_records(3),
                   Path("/sys/firmware/efi").exists())
    ctx.machine["boot_mode"] = data.get("boot_mode")
    if not data.get("version"):
        return Result("warn", data, "Brak wersji BIOS w tabelach SMBIOS")
    return Result("pass", data)
