"""Build the report (schema version 1) sent to ProSpectra POST /tests. See INSTRUCTIONS.md § Report."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

from tester import __version__
from tester.core import config
from tester.core.profiles import suggest_grade
from tester.core.runner import Session, overall_result

REPORT_SCHEMA = 1


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def next_run_id(cfg: config.StationConfig, data_dir: Path | None = None) -> str:
    """station slug + UTC date + counter kept on the config partition (or in RAM without one)."""
    data_dir = data_dir or config.DATA_DIR
    counter_file = data_dir / "counter"
    try:
        n = int(counter_file.read_text().strip()) + 1
    except (OSError, ValueError):
        n = 1
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        counter_file.write_text(str(n))
    except OSError:
        pass
    suffix = "" if cfg.persistent else f"-{int(time.time()) % 100000:05d}"   # avoid clashes without a counter
    return f"{cfg.station_slug}-{dt.datetime.now(dt.timezone.utc):%Y%m%d}-{n:06d}{suffix}"


def _data(results: dict, mid: str) -> dict:
    return (results.get(mid) or {}).get("data") or {}


def build_device(results: dict) -> dict:
    sysinfo, bios, mem = _data(results, "sysinfo"), _data(results, "bios"), _data(results, "memory")
    storage, battery, tpm = _data(results, "storage"), _data(results, "battery"), _data(results, "tpm")
    gpu = _data(results, "gpu")
    device: dict = {
        "manufacturer": sysinfo.get("manufacturer"),
        "model": sysinfo.get("model"),
        "cpu": sysinfo.get("cpu"),
        "ram_gb": mem.get("total_gb") or sysinfo.get("ram_gb"),
        "storage": [
            {k: d.get(k) for k in ("type", "size_gb", "health_pct", "model", "serial", "power_on_hours") if d.get(k) is not None}
            for d in storage.get("disks", [])
        ],
        "screen": sysinfo.get("screen"),
        "gpu": ", ".join(g.get("name", "") for g in sysinfo.get("gpus", [])) or None,
    }
    if battery.get("present"):
        device["battery"] = {k: battery.get(k) for k in ("health_pct", "cycles") if battery.get(k) is not None}
    tester = {
        "version": __version__,
        "report_schema": REPORT_SCHEMA,
        "bios": bios or None,
        "memory": mem.get("modules"),
        "memory_warnings": mem.get("warnings") or None,
        "tpm": tpm or None,
        "gpu": gpu or None,
        "system": {k: sysinfo.get(k) for k in ("chassis", "cpu_cores", "cpu_threads", "network", "sku", "uuid") if sysinfo.get(k)},
        "battery": battery or None,
    }
    device["tester"] = {k: v for k, v in tester.items() if v is not None}
    return {k: v for k, v in device.items() if v not in (None, [], {})}


def build_report(session: Session, cfg: config.StationConfig, *, grade: str | None = None,
                 result: str | None = None, summary: str | None = None, run_id: str | None = None) -> dict:
    from tester.core.module import discover  # local import keeps report.py importable without modules

    names = {m.id: m.name for m in session.modules} or {m.id: m.name for m in discover().values()}
    results = session.results
    checks = []
    for mid, res in results.items():
        status = res["status"]
        check = {"name": names.get(mid, mid), "status": "pass" if status == "warn" else status}
        if res.get("note"):
            check["note"] = res["note"]
        checks.append(check)
    suggested, reasons = suggest_grade(session.profile, results)
    report = {
        "run_id": run_id or next_run_id(cfg),
        "serial": session.serial,
        "tested_at": utc_now(),
        "station": cfg.station,
        "result": result or overall_result(results),
        "summary": summary or make_summary(results),
        "device": build_device(results),
        "checks": checks,
    }
    if session.operator:
        report["operator"] = session.operator
    if grade:
        report["grade"] = grade
    t = report["device"].setdefault("tester", {})
    t.update({"profile": session.profile.id, "mode": session.profile.mode, "serial_source": session.serial_source,
              "suggested_grade": suggested, "grade_reasons": reasons or None,
              "modules": {mid: {"status": r["status"], "seconds": r.get("seconds")} for mid, r in results.items()}})
    if not t["grade_reasons"]:
        del t["grade_reasons"]
    # Kept outside the API body: lets the uploader fix tested_at after the clock is synced (same boot only).
    report["_local"] = {"mono": time.monotonic(), "boot_id": boot_id()}
    return report


def make_summary(results: dict) -> str:
    parts = []
    bat = _data(results, "battery")
    if bat.get("health_pct") is not None:
        parts.append(f"Bateria {bat['health_pct']}%")
    failed = [mid for mid, r in results.items() if r["status"] == "fail"]
    if failed:
        parts.append("Błędy: " + ", ".join(failed))
    else:
        parts.append("Brak błędów")
    return ", ".join(parts)


def boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def api_body(report: dict) -> dict:
    """The report without local-only keys, as sent to ProSpectra."""
    return {k: v for k, v in report.items() if not k.startswith("_")}


def dumps(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)
