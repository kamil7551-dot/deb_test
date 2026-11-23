"""Device label: a text template filled from the saved report, editable on screen, printed over the network.

The template lives in TESTERCFG/label.yaml (falls back to config.example/TESTERCFG/label.yaml, then DEFAULT).
Each line is text with {placeholders} (see fields()). Line prefixes:
  "# "  big bold line          "? "  drop the line when any placeholder in it is empty
Printer languages (printer.language): zpl (Zebra and most ZPL-compatible printers), tspl (TSC, many cheap
thermal printers), text (plain lines, for printers in line mode or for testing with `nc -l 9100`).
The printer is reached over raw TCP (port 9100 by default). Without printer.host the label is only saved
to DATA_DIR/labels/<run_id>.<ext> so it can be printed later.
"""

from __future__ import annotations

import datetime as dt
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tester.core import config

DEFAULT = {
    "printer": {"host": "", "port": 9100, "language": "zpl", "width_mm": 62, "height_mm": 100, "dpi": 203,
                "copies": 1},
    "auto_print": False,
    "timezone": "Europe/Warsaw",
    "barcode": "{serial}",
    "lines": [
        "# {nr}", "{manufacturer} {model}", "",
        "Testował: {operator}", "Data i godzina testu: {tested_local}", "Miejsce: {station}", "",
        "{serial}", "{cpu}", "{gpu}", "? {ram_gb} GB", "{disks}", "{resolution}",
        "Dotyk: {touchscreen}", "? Bateria_1: {battery_1}%", "? Bateria_2: {battery_2}%", "Bluetooth: {bluetooth}",
        "? Defekty matrycy: {lcd_defects}", "",
        "Grade LCD: {grade_lcd}", "Grade klapa: {grade_lid}", "Grade kadłubek: {grade_body}",
        "Grade palmrest: {grade_palmrest}", "? Uwagi: {notes}", "",
        "# GRADE: {grade}",
    ],
}

PLACEHOLDER = re.compile(r"\{(\w+)\}")
YES_NO = {True: "Tak", False: "Nie", None: ""}
PART_NAMES = {"lcd": "LCD", "lid": "klapa", "body": "kadłubek", "palmrest": "palmrest", "case": "obudowa"}


def template_path() -> Path | None:
    for p in (config.DATA_DIR / "label.yaml", config.CFG_TEMPLATE / "label.yaml"):
        if p.exists():
            return p
    return None


def load_template(path: Path | None = None) -> dict:
    tpl = {**DEFAULT, "printer": dict(DEFAULT["printer"])}
    path = path or template_path()
    if path:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tpl.update({k: v for k, v in data.items() if k != "printer"})
        tpl["printer"].update(data.get("printer") or {})
    return tpl


def _local_time(iso: str | None, tz: str) -> str:
    if not iso:
        return ""
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        try:
            from zoneinfo import ZoneInfo
            t = t.astimezone(ZoneInfo(tz))
        except Exception:      # no tzdata: keep UTC
            pass
        return t.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return iso


def fields(report: dict, tz: str = "Europe/Warsaw") -> dict[str, str]:
    """Flat text values for the template, from a report built by core.report.build_report."""
    dev = report.get("device") or {}
    tester = dev.get("tester") or {}
    bat = tester.get("battery") or {}
    bats = bat.get("batteries") or ([bat] if bat.get("health_pct") is not None else [])
    grades = dev.get("cosmetic_grades") or {}
    notes = dev.get("cosmetic_notes") or {}
    screen = dev.get("screen") or ""
    res = re.search(r"\d{3,5}x\d{3,5}", screen)
    m = re.search(r"-(\d{6})(?:-\d{5})?$", report.get("run_id") or "")
    disks = []
    for d in dev.get("storage") or []:
        disks.append(f"{(d.get('type') or 'DYSK').upper()}: {d.get('size_gb', '?')}")
    out = {
        "nr": str(int(m.group(1))) if m else "",
        "run_id": report.get("run_id") or "",
        "serial": report.get("serial") or "",
        "manufacturer": dev.get("manufacturer") or "",
        "model": dev.get("model") or "",
        "operator": report.get("operator") or "",
        "station": report.get("station") or "",
        "tested_local": _local_time(report.get("tested_at"), tz),
        "tested_at": report.get("tested_at") or "",
        "cpu": dev.get("cpu") or "",
        "gpu": dev.get("gpu") or "",
        "ram_gb": f"{dev['ram_gb']:g}" if isinstance(dev.get("ram_gb"), (int, float)) else str(dev.get("ram_gb") or ""),
        "disks": ", ".join(disks),
        "screen": screen,
        "resolution": res.group(0) if res else "",
        "touchscreen": YES_NO.get(dev.get("touchscreen"), ""),
        "bluetooth": YES_NO.get(dev.get("bluetooth"), ""),
        "camera": YES_NO.get(dev.get("camera"), ""),
        "battery_1": str(bats[0].get("health_pct", "")) if len(bats) > 0 else "",
        "battery_2": str(bats[1].get("health_pct", "")) if len(bats) > 1 else "",
        "lcd_defects": ", ".join(dev.get("lcd_defects") or []),
        "notes": "; ".join(f"{PART_NAMES.get(k, k)}: {v}" for k, v in notes.items()),
        "grade": {"scrap": "ZŁOM"}.get(report.get("grade") or "", report.get("grade") or ""),
        "result": report.get("result") or "",
        "summary": report.get("summary") or "",
    }
    for k, v in grades.items():
        out[f"grade_{k}"] = v
    for k, v in (dev.get("answers") or {}).items():
        out[f"q_{k}"] = YES_NO.get(v, "")
    return out


def render(lines: list[str], values: dict[str, str]) -> list[str]:
    """Fill placeholders. Keeps the "# " prefix (big line) for the printer; drops "? " lines with an empty value."""
    out = []
    for raw in lines:
        line = str(raw)
        optional = line.startswith("? ")
        if optional:
            line = line[2:]
            if any(not values.get(k) for k in PLACEHOLDER.findall(line)):
                continue
        out.append(PLACEHOLDER.sub(lambda mt: values.get(mt.group(1), ""), line).rstrip())
    return out


@dataclass
class Printer:
    host: str = ""
    port: int = 9100
    language: str = "zpl"
    width_mm: float = 62
    height_mm: float = 100
    dpi: int = 203
    copies: int = 1
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_template(cls, tpl: dict) -> "Printer":
        p = dict(tpl.get("printer") or {})
        known = {k: p.pop(k) for k in list(p) if k in cls.__dataclass_fields__ and k != "extra"}
        return cls(**known, extra=p)

    @property
    def ext(self) -> str:
        return {"zpl": "zpl", "tspl": "tspl"}.get(self.language, "txt")


def _zpl_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace("^", " ").replace("~", " ")


def to_zpl(lines: list[str], barcode: str, p: Printer) -> bytes:
    dots = p.dpi / 25.4
    w, h = int(p.width_mm * dots), int(p.height_mm * dots)
    small, big = max(18, int(h / 42)), max(26, int(h / 28))
    out = ["^XA", "^CI28", f"^PW{w}", f"^LL{h}", "^LH0,0"]
    y = int(3 * dots)
    for line in lines:
        bold = line.startswith("# ")
        text = line[2:] if bold else line
        size = big if bold else small
        if text:
            out.append(f"^FO0,{y}^A0N,{size},{size}^FB{w},1,0,C^FD{_zpl_text(text)}^FS")
        y += int(size * (1.15 if text else 0.6))
    if barcode:
        bh = int(10 * dots)
        out.append(f"^BY2,3,{bh}^FO{int(4 * dots)},{min(y + 6, h - bh - 4)}^BCN,{bh},N,N,N^FD{_zpl_text(barcode)}^FS")
    out += [f"^PQ{max(1, int(p.copies))}", "^XZ"]
    return ("\n".join(out) + "\n").encode("utf-8")


def to_tspl(lines: list[str], barcode: str, p: Printer) -> bytes:
    dots = p.dpi / 25.4
    out = [f"SIZE {p.width_mm} mm,{p.height_mm} mm", "GAP 3 mm,0", "CODEPAGE UTF-8", "DIRECTION 1", "CLS"]
    y = int(3 * dots)
    for line in lines:
        bold = line.startswith("# ")
        text = (line[2:] if bold else line).replace('"', "'")
        scale = 2 if bold else 1
        if text:
            out.append(f'TEXT {int(3 * dots)},{y},"3",0,{scale},{scale},"{text}"')
        y += int((26 if text else 12) * scale)
    if barcode:
        out.append(f'BARCODE {int(4 * dots)},{y + 6},"128",{int(10 * dots)},0,0,2,4,"{barcode}"')
    out.append(f"PRINT 1,{max(1, int(p.copies))}")
    return ("\r\n".join(out) + "\r\n").encode("utf-8")


def to_text(lines: list[str], barcode: str, p: Printer) -> bytes:
    body = [ln[2:].upper() if ln.startswith("# ") else ln for ln in lines]
    if barcode:
        body += ["", f"*{barcode}*"]
    return ("\r\n".join(body) + "\r\n\f").encode("utf-8")


def encode(lines: list[str], barcode: str, p: Printer) -> bytes:
    return {"zpl": to_zpl, "tspl": to_tspl}.get(p.language, to_text)(lines, barcode, p)


def save(run_id: str, payload: bytes, p: Printer, data_dir: Path | None = None) -> Path:
    d = (data_dir or config.DATA_DIR) / "labels"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{run_id or 'label'}.{p.ext}"
    path.write_bytes(payload)
    return path


def send(payload: bytes, p: Printer, timeout: float = 8) -> None:
    """Raw TCP to the printer (JetDirect / port 9100). Raises OSError with the reason on failure."""
    if not p.host:
        raise OSError("brak adresu drukarki (printer.host w label.yaml)")
    with socket.create_connection((p.host, int(p.port)), timeout=timeout) as s:
        s.sendall(payload)
