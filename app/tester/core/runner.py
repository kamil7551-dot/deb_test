"""Test session: which modules run, their results, and the background runner for automatic modules."""

from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from tester.core.module import Context, ModuleInfo, Result
from tester.core.profiles import Profile

log = logging.getLogger("tester")

# Modules that other modules may rely on (they fill ctx.machine); run first, sequentially.
FIRST = ("sysinfo",)


@dataclass
class Session:
    profile: Profile
    modules: list[ModuleInfo]                     # in run order
    machine: dict = field(default_factory=dict)
    results: dict[str, dict] = field(default_factory=dict)   # module id -> Result.to_dict() + timing
    serial: str | None = None
    serial_source: str = "dmi"                    # "dmi" | "scanned"
    operator: str | None = None
    started_mono: float = field(default_factory=time.monotonic)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def context(self, m: ModuleInfo, ui=None) -> Context:
        return Context(options=self.profile.options_for(m.id), machine=self.machine,
                       log=logging.getLogger(f"tester.{m.id}"), ui=ui)

    def record(self, module_id: str, result: Result, seconds: float) -> None:
        if module_id == "sysinfo" and self.serial_source == "scanned" and result.data.get("serial_placeholder"):
            result = Result("pass", result.data, "S/N zeskanowany z naklejki (BIOS: pusty lub zastępczy)")
        with self._lock:
            d = result.to_dict()
            d["seconds"] = round(seconds, 1)
            self.results[module_id] = d

    @property
    def auto_modules(self) -> list[ModuleInfo]:
        return [m for m in self.modules if m.kind == "auto"]

    @property
    def interactive_modules(self) -> list[ModuleInfo]:
        return [m for m in self.modules if m.interactive]

    def pending(self) -> list[ModuleInfo]:
        return [m for m in self.modules if m.id not in self.results]


def run_module(session: Session, m: ModuleInfo) -> Result:
    """Run an automatic module (or the run() part of a semi module) with full error isolation."""
    start = time.monotonic()
    ctx = session.context(m)
    try:
        reason = m.mod.available(ctx) if hasattr(m.mod, "available") else None
        if reason:
            result = Result("skip", note=str(reason))
        else:
            result = m.mod.run(ctx)
    except Exception as exc:
        log.error("module %s crashed:\n%s", m.id, traceback.format_exc())
        result = Result("fail", {"error": repr(exc)}, note=f"Błąd modułu: {exc}")
    session.record(m.id, result, time.monotonic() - start)
    return result


def run_auto(session: Session, on_update: Callable[[str, Result], None] | None = None,
             workers: int = 4) -> threading.Thread:
    """Start all automatic modules in a background thread. Returns the thread (join() to wait)."""

    def notify(mid: str, res: Result) -> None:
        if on_update:
            try:
                on_update(mid, res)
            except Exception:
                log.exception("on_update failed")

    def worker() -> None:
        autos = session.auto_modules
        for m in [m for m in autos if m.id in FIRST]:
            notify(m.id, run_module(session, m))
        rest = [m for m in autos if m.id not in FIRST]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_module, session, m): m for m in rest}
            for fut, m in futures.items():
                notify(m.id, fut.result())

    t = threading.Thread(target=worker, name="auto-modules", daemon=True)
    t.start()
    return t


def overall_result(results: dict[str, dict]) -> str:
    """pass: everything passed (warn counts as pass) · fail: any fail · partial: something skipped/not run."""
    statuses = [r["status"] for r in results.values()]
    if any(s == "fail" for s in statuses):
        return "fail"
    if any(s == "skip" for s in statuses):
        return "partial"
    return "pass"
