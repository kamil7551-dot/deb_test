"""Offline queue of reports on the config partition, flushed to ProSpectra when network and clock are OK.

Layout under DATA_DIR: queue/<run_id>.json (waiting) · sent/<run_id>.json (accepted, with the API answer)
· failed/<run_id>.json (rejected with 400/401/403/…, shown to the operator; retrying will not help).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from tester.core import config
from tester.core.report import api_body, boot_id, utc_now
from tester.core.util import run as sh
from tester.upload.prospectra import FINAL_ERRORS, Client

log = logging.getLogger("tester.upload")


def dirs(base: Path | None = None) -> tuple[Path, Path, Path]:
    base = base or config.DATA_DIR
    q, s, f = base / "queue", base / "sent", base / "failed"
    for d in (q, s, f):
        d.mkdir(parents=True, exist_ok=True)
    return q, s, f


def clock_ok() -> bool:
    """True when systemd-timesyncd (or chrony) reports the clock as NTP-synchronised."""
    import os

    if os.environ.get("TESTER_TRUST_CLOCK") == "1":
        return True
    out = (sh(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], timeout=5) or "").strip()
    return out == "yes"


def wait_for_clock(timeout: float = 30) -> bool:
    if clock_ok():
        return True
    sh(["systemctl", "restart", "systemd-timesyncd"], timeout=10)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if clock_ok():
            return True
        time.sleep(1)
    return False


def enqueue(report: dict, base: Path | None = None) -> Path:
    q, _, _ = dirs(base)
    report.setdefault("_local", {})["clock_synced"] = clock_ok()
    path = q / f"{report['run_id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    tmp.replace(path)
    return path


def fix_tested_at(report: dict) -> None:
    """If the clock was wrong when the test ran, recompute tested_at from the monotonic clock (same boot only)."""
    local = report.get("_local") or {}
    if local.get("clock_synced") or local.get("mono") is None or local.get("boot_id") != boot_id():
        return
    elapsed = max(0.0, time.monotonic() - float(local["mono"]))
    when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=elapsed)
    report["tested_at"] = when.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    local["clock_synced"] = True
    local["tested_at_corrected"] = utc_now()


@dataclass
class Outcome:
    run_id: str
    state: str          # "sent" | "failed" | "waiting"
    message: str = ""
    url: str | None = None
    linked: bool | None = None


def send_one(path: Path, client: Client, base: Path | None = None) -> Outcome:
    _, sent_dir, failed_dir = dirs(base)
    report = json.loads(path.read_text())
    run_id = report.get("run_id", path.stem)
    fix_tested_at(report)
    if not (report.get("_local") or {}).get("clock_synced", True):
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        return Outcome(run_id, "waiting", "Nieznana data testu (zegar nie był zsynchronizowany)")
    resp = client.send_test(api_body(report))
    if resp.ok:
        test = (resp.body or {}).get("test") or {}
        report["_local"]["response"] = resp.body
        (sent_dir / path.name).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        path.unlink(missing_ok=True)
        return Outcome(run_id, "sent", "Wysłano" if test.get("linked") else "Wysłano (sztuki jeszcze nie ma na PZ)",
                       test.get("url"), test.get("linked"))
    if resp.status in FINAL_ERRORS:
        report.setdefault("_local", {})["error"] = resp.message()
        (failed_dir / path.name).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        path.unlink(missing_ok=True)
        return Outcome(run_id, "failed", resp.message())
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))   # keep corrected tested_at
    return Outcome(run_id, "waiting", resp.message())


def flush(client: Client, base: Path | None = None, wait_clock: bool = True) -> list[Outcome]:
    q, _, _ = dirs(base)
    files = sorted(q.glob("*.json"))
    if not files:
        return []
    if not client.configured:
        return [Outcome(f.stem, "waiting", "Brak tokenu ProSpectry w konfiguracji") for f in files]
    if wait_clock and not wait_for_clock():
        return [Outcome(f.stem, "waiting", "Zegar niezsynchronizowany (brak sieci?)") for f in files]
    out = []
    for f in files:
        try:
            out.append(send_one(f, client, base))
        except (OSError, ValueError) as exc:
            log.exception("cannot send %s", f)
            out.append(Outcome(f.stem, "waiting", str(exc)))
    return out


def counts(base: Path | None = None) -> dict:
    q, s, f = dirs(base)
    return {"queued": len(list(q.glob("*.json"))), "sent": len(list(s.glob("*.json"))),
            "failed": len(list(f.glob("*.json")))}
