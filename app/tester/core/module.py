"""Module contract (version 1) and module discovery.

Every file in tester/modules/ is a test module exposing:

    ID = "battery"            # stable snake_case id, used in profiles and reports
    NAME = "Bateria"          # Polish display name (also the check name in the report)
    KIND = "auto"             # "auto" | "interactive" | "semi"
    CONTRACT = 1
    ORDER = 60                # default display order (lower first)

    def run(ctx) -> Result                  # auto modules (and the data part of semi modules)
    def build(ctx, finish) -> Gtk.Widget    # interactive/semi modules; call finish(Result) when done
    def available(ctx) -> str | None       # optional: return a reason string to skip the module

The engine knows nothing module-specific. See INSTRUCTIONS.md for the full contract.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

CONTRACT_VERSION = 1
STATUSES = ("pass", "fail", "warn", "skip")


@dataclass
class Result:
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"invalid status {self.status!r}")

    def to_dict(self) -> dict:
        return {"status": self.status, "data": self.data, "note": self.note}


@dataclass
class Context:
    options: dict[str, Any]
    machine: dict[str, Any]          # shared facts, filled by earlier modules (e.g. chassis type)
    log: logging.Logger
    ui: Any = None                   # UI helper for interactive modules (None in headless mode)


@dataclass
class ModuleInfo:
    id: str
    name: str
    kind: str
    order: int
    mod: ModuleType

    @property
    def interactive(self) -> bool:
        return self.kind in ("interactive", "semi")


def discover() -> dict[str, ModuleInfo]:
    """Import every module in tester.modules that follows the contract."""
    from tester import modules as pkg

    found: dict[str, ModuleInfo] = {}
    log = logging.getLogger("tester")
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"tester.modules.{info.name}")
        except Exception:  # a broken module must not take the whole tester down
            log.exception("cannot import module %s", info.name)
            continue
        if getattr(mod, "CONTRACT", None) != CONTRACT_VERSION:
            log.warning("module %s skipped: CONTRACT != %s", info.name, CONTRACT_VERSION)
            continue
        found[mod.ID] = ModuleInfo(
            id=mod.ID, name=mod.NAME, kind=mod.KIND, order=getattr(mod, "ORDER", 100), mod=mod
        )
    return dict(sorted(found.items(), key=lambda kv: kv[1].order))
