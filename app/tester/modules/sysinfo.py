"""System identity: manufacturer, model, serial, chassis, CPU, RAM total, GPUs, network adapters, internal screen."""

from __future__ import annotations

import math
import os
import re
import shlex
from pathlib import Path

from tester.core.module import Result
from tester.core.util import clean, dmi_records, is_placeholder_serial, read_text
from tester.core.util import run as sh

ID = "sysinfo"
NAME = "Informacje o systemie"
KIND = "auto"
CONTRACT = 1
ORDER = 10

LAPTOP_TYPES = {"notebook", "laptop", "portable", "sub notebook", "convertible", "detachable", "tablet", "hand held"}
DESKTOP_TYPES = {"desktop", "tower", "mini tower", "low profile desktop", "space-saving", "pizza box", "mini pc",
                 "lunch box", "main server chassis", "stick pc", "all in one", "all-in-one"}


def chassis_kind(chassis_type: str | None) -> str | None:
    if not chassis_type:
        return None
    t = chassis_type.strip().lower()
    if t in LAPTOP_TYPES:
        return "laptop"
    if t in ("all in one", "all-in-one"):
        return "aio"
    if t in DESKTOP_TYPES:
        return "desktop"
    return None


def cpu_info(cpuinfo: str | None = None) -> dict:
    cpuinfo = cpuinfo if cpuinfo is not None else (read_text("/proc/cpuinfo") or "")
    model = None
    cores: set[tuple[str, str]] = set()
    threads = 0
    phys = core = "0"
    for line in cpuinfo.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "processor":
            threads += 1
        elif key == "model name" and model is None:
            model = value
        elif key == "physical id":
            phys = value
        elif key == "core id":
            core = value
            cores.add((phys, core))
    if model:
        model = re.sub(r"\((R|TM)\)|\s+CPU|\s+@.*$|\d+(th|st|nd|rd) Gen ", "", model, flags=re.I)
        model = re.sub(r"\s+", " ", model).strip()
    return {"cpu": model, "cpu_cores": len(cores) or None, "cpu_threads": threads or os.cpu_count()}


def parse_lspci_gpus(text: str) -> list[dict]:
    gpus = []
    for line in text.splitlines():
        try:
            parts = shlex.split(line)
        except ValueError:
            continue
        if len(parts) < 4:
            continue
        slot, cls, vendor, device = parts[0], parts[1], parts[2], parts[3]
        m = re.search(r"\[(03[0-9a-f]{2})\]", cls)
        if not m:
            continue
        strip_ids = lambda s: re.sub(r"\s*\[[0-9a-f]{4}\]$", "", s).strip()
        vendor_s = strip_ids(vendor).replace(" Corporation", "").replace(", Inc.", "")
        vendor_s = vendor_s.replace("Advanced Micro Devices, Inc. [AMD/ATI]", "AMD").replace("[AMD/ATI]", "AMD")
        dev = strip_ids(device)
        bracket = re.search(r"\[([^\]]+)\]$", dev)          # "TigerLake-LP GT2 [Iris Xe Graphics]"
        name = bracket.group(1) if bracket else dev
        ids = re.findall(r"\[([0-9a-f]{4})\]", vendor + device)
        gpus.append({"slot": slot, "name": f"{vendor_s} {name}".strip(), "pci_id": ":".join(ids[:2]) or None})
    return gpus


def gpu_list() -> list[dict]:
    gpus = parse_lspci_gpus(sh(["lspci", "-mm", "-nn"]) or "")
    for g in gpus:
        drv = Path(f"/sys/bus/pci/devices/0000:{g['slot']}/driver")
        g["driver"] = drv.resolve().name if drv.exists() else None
    return gpus


def network_adapters() -> list[dict]:
    out = []
    for dev in sorted(Path("/sys/class/net").glob("*")):
        if dev.name == "lo" or not (dev / "device").exists():
            continue
        wireless = (dev / "wireless").exists() or (dev / "phy80211").exists()
        drv = dev / "device" / "driver"
        out.append({
            "name": dev.name,
            "type": "wifi" if wireless else "ethernet",
            "mac": read_text(dev / "address"),
            "driver": drv.resolve().name if drv.exists() else None,
        })
    return out


def parse_edid(edid: bytes) -> dict:
    if len(edid) < 128 or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return {}
    info: dict = {}
    dtd = edid[54:72]
    if dtd[0] or dtd[1]:
        info["width_px"] = dtd[2] | ((dtd[4] & 0xF0) << 4)
        info["height_px"] = dtd[5] | ((dtd[7] & 0xF0) << 4)
        w_mm = dtd[12] | ((dtd[14] & 0xF0) << 4)
        h_mm = dtd[13] | ((dtd[14] & 0x0F) << 8)
    else:
        w_mm = h_mm = 0
    if not (w_mm and h_mm):
        w_mm, h_mm = edid[21] * 10, edid[22] * 10
    if w_mm and h_mm:
        info["diagonal_in"] = round(math.hypot(w_mm, h_mm) / 25.4, 1)
    texts = []
    for off in (72, 90, 108):
        block = edid[off:off + 18]
        if block[0:3] == b"\x00\x00\x00" and block[3] in (0xFC, 0xFE):
            texts.append(block[5:].split(b"\x0a")[0].decode("ascii", "replace").strip())
    mfg = edid[8:10]
    code = (mfg[0] << 8) | mfg[1]
    info["vendor"] = "".join(chr(((code >> s) & 0x1F) + 64) for s in (10, 5, 0))
    if texts:
        info["panel"] = " ".join(t for t in texts if t)
    return info


def internal_screen() -> dict | None:
    for status in sorted(Path("/sys/class/drm").glob("card*-*/status")):
        conn = status.parent
        if not re.search(r"-(eDP|LVDS|DSI)-", conn.name):
            continue
        if (read_text(status) or "") != "connected":
            continue
        try:
            info = parse_edid((conn / "edid").read_bytes())
        except OSError:
            info = {}
        info["connector"] = conn.name.split("-", 1)[1]
        return info
    return None


def format_screen(s: dict | None) -> str | None:
    if not s:
        return None
    parts = []
    if s.get("diagonal_in"):
        parts.append(f'{s["diagonal_in"]:g}"')
    if s.get("width_px"):
        parts.append(f'{s["width_px"]}x{s["height_px"]}')
    return " ".join(parts) or None


def run(ctx) -> Result:
    def dmi(key: str) -> str | None:
        return clean(sh(["dmidecode", "-s", key]))

    t1 = dmi_records(1)
    t3 = dmi_records(3)
    sys_props = t1[0]["props"] if t1 else {}
    chassis_type = t3[0]["props"].get("Type") if t3 else None
    serial_raw = (sh(["dmidecode", "-s", "system-serial-number"]) or "").strip()
    data = {
        "manufacturer": dmi("system-manufacturer"),
        "model": dmi("system-version") if (dmi("system-manufacturer") or "").upper() == "LENOVO" else dmi("system-product-name"),
        "product_name": dmi("system-product-name"),
        "serial": serial_raw or None,
        "serial_placeholder": is_placeholder_serial(serial_raw),
        "uuid": clean(sys_props.get("UUID")),
        "sku": clean(sys_props.get("SKU Number")),
        "family": clean(sys_props.get("Family")),
        "chassis_type": chassis_type,
        "chassis": chassis_kind(chassis_type),
        **cpu_info(),
        "gpus": gpu_list(),
        "network": network_adapters(),
    }
    mem_kb = re.search(r"MemTotal:\s+(\d+)", read_text("/proc/meminfo") or "")
    if mem_kb:
        data["ram_visible_gb"] = round(int(mem_kb.group(1)) / 1024 / 1024, 1)
    screen = internal_screen()
    if screen:
        data["screen_info"] = screen
        data["screen"] = format_screen(screen)
    ctx.machine.update({"chassis": data["chassis"], "serial": data["serial"],
                        "serial_placeholder": data["serial_placeholder"], "manufacturer": data["manufacturer"],
                        "model": data["model"], "has_internal_screen": screen is not None})
    if data["serial_placeholder"]:
        return Result("warn", data, "Numer seryjny w BIOS jest pusty lub zastępczy, zeskanuj go z naklejki")
    return Result("pass", data)
