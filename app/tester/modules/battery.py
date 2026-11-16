"""Battery: design vs full capacity (health %), cycle count, manufacturer/model, charging state.

Options: fail_below_health_pct (int, default 0 = never fail), warn_below_health_pct (int, default 70),
required (bool; default: required on laptops).
"""

from __future__ import annotations

from pathlib import Path

from tester.core.module import Result
from tester.core.util import read_int, read_text

ID = "battery"
NAME = "Bateria"
KIND = "auto"
CONTRACT = 1
ORDER = 60

PS = Path("/sys/class/power_supply")


def read_battery(dev: Path) -> dict:
    full = read_int(dev / "energy_full") or read_int(dev / "charge_full")
    design = read_int(dev / "energy_full_design") or read_int(dev / "charge_full_design")
    now = read_int(dev / "energy_now") or read_int(dev / "charge_now")
    unit = "Wh" if (dev / "energy_full").exists() else "Ah"
    data = {
        "name": dev.name,
        "present": read_text(dev / "present", "1") == "1",
        "manufacturer": read_text(dev / "manufacturer") or None,
        "model": read_text(dev / "model_name") or None,
        "serial": read_text(dev / "serial_number") or None,
        "technology": read_text(dev / "technology") or None,
        "status": read_text(dev / "status") or None,
        "cycles": read_int(dev / "cycle_count") or None,
        "full": round(full / 1e6, 2) if full else None,
        "design": round(design / 1e6, 2) if design else None,
        "unit": unit,
        "charge_pct": read_int(dev / "capacity"),
    }
    if full and design:
        data["health_pct"] = min(100, round(full * 100 / design))
    if now and full:
        data["charge_pct"] = data["charge_pct"] or round(now * 100 / full)
    return {k: v for k, v in data.items() if v is not None}


def batteries(root: Path = PS) -> list[dict]:
    out = []
    for dev in sorted(root.glob("*")):
        if read_text(dev / "type") == "Battery" and read_text(dev / "scope", "System") != "Device":
            out.append(read_battery(dev))
    return out


def evaluate(bats: list[dict], opts: dict, chassis: str | None) -> Result:
    required = opts.get("required", chassis == "laptop")
    if not bats:
        if required:
            return Result("fail", {"present": False}, "Brak baterii")
        return Result("skip", {"present": False}, "Brak baterii (komputer stacjonarny)")
    main = bats[0]
    data = dict(main)
    data["present"] = True
    if len(bats) > 1:
        data["batteries"] = bats
        hs = [b["health_pct"] for b in bats if "health_pct" in b]
        if hs:
            data["health_pct"] = min(hs)
    health = data.get("health_pct")
    fail_below = int(opts.get("fail_below_health_pct", 0))
    warn_below = int(opts.get("warn_below_health_pct", 70))
    if health is None:
        return Result("warn", data, "Nie można odczytać pojemności baterii")
    if health < fail_below:
        return Result("fail", data, f"Kondycja baterii {health}% (próg {fail_below}%)")
    if health < warn_below:
        return Result("warn", data, f"Kondycja baterii {health}%")
    return Result("pass", data, f"Kondycja baterii {health}%")


def run(ctx) -> Result:
    return evaluate(batteries(), ctx.options, ctx.machine.get("chassis"))
