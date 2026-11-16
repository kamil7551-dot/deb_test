"""Test profiles: YAML files listing which modules run, with options and grade rules.

Built-in profiles ship in /opt/tester/profiles; files with the same name on the config partition
(/var/lib/tester/profiles) override them. Format is documented in INSTRUCTIONS.md.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tester.core import config

log = logging.getLogger("tester")

GRADES = ["A", "B", "C", "D", "scrap"]


@dataclass
class TestEntry:
    id: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Profile:
    id: str
    name: str
    mode: str = "pipeline"
    tests: list[TestEntry] = field(default_factory=list)
    grade_rules: list[dict] = field(default_factory=list)
    upload: str = "auto"
    chassis: list[str] = field(default_factory=list)   # auto-select hint: "laptop", "desktop"
    order: int = 100                                    # position on the start screen

    def options_for(self, module_id: str) -> dict:
        for t in self.tests:
            if t.id == module_id:
                return t.options
        return {}


def parse_profile(data: dict, fallback_id: str = "profile") -> Profile:
    tests: list[TestEntry] = []
    for item in data.get("tests") or []:
        if isinstance(item, str):
            tests.append(TestEntry(item))
        elif isinstance(item, dict) and len(item) == 1:
            (mid, opts), = item.items()
            tests.append(TestEntry(str(mid), dict(opts or {})))
        else:
            raise ValueError(f"invalid test entry: {item!r}")
    return Profile(
        id=str(data.get("profile") or fallback_id),
        name=str(data.get("name") or data.get("profile") or fallback_id),
        mode=str(data.get("mode") or "pipeline"),
        tests=tests,
        grade_rules=list(data.get("grade_rules") or []),
        upload=str(data.get("upload") or "auto"),
        chassis=[str(c) for c in (data.get("chassis") or [])],
        order=int(data.get("order", 100)),
    )


def load_profiles(dirs: list[Path] | None = None) -> dict[str, Profile]:
    if dirs is None:
        dirs = [config.BUILTIN_PROFILES, config.DATA_DIR / "profiles"]
    files: dict[str, Path] = {}
    for d in dirs:
        if d.is_dir():
            for f in sorted(d.glob("*.y*ml")):
                files[f.stem] = f          # later dirs override earlier ones
    profiles: dict[str, Profile] = {}
    for stem, f in files.items():
        try:
            p = parse_profile(yaml.safe_load(f.read_text()) or {}, stem)
            profiles[p.id] = p
        except Exception as exc:
            log.error("invalid profile %s: %s", f, exc)
    return dict(sorted(profiles.items(), key=lambda kv: (kv[1].order, kv[0])))


def pick_default(profiles: dict[str, Profile], chassis: str | None, preferred: str | None = None) -> Profile | None:
    if preferred and preferred in profiles:
        return profiles[preferred]
    if chassis:
        for p in profiles.values():
            if chassis in p.chassis:
                return p
    return next(iter(profiles.values()), None)


_RULE = re.compile(r"^\s*([\w.]+)\s*(<=|>=|==|!=|<|>)\s*(.+?)\s*$")


def _lookup(results: dict[str, dict], path: str) -> Any:
    """'battery.health_pct' -> results['battery']['data']['health_pct']; 'display.status' -> status."""
    module_id, _, rest = path.partition(".")
    res = results.get(module_id)
    if res is None:
        return None
    if rest == "status":
        return res.get("status")
    value: Any = res.get("data", {})
    for part in rest.split(".") if rest else []:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def eval_condition(expr: str, results: dict[str, dict]) -> bool:
    m = _RULE.match(expr)
    if not m:
        raise ValueError(f"invalid rule: {expr!r}")
    path, op, raw = m.groups()
    left = _lookup(results, path)
    if left is None:
        return False
    raw = raw.strip("'\"")
    try:
        right: Any = float(raw)
        left = float(left)
    except (TypeError, ValueError):
        right, left = raw, str(left)
    return {
        "<": lambda a, b: a < b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b,
        ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    }[op](left, right)


def suggest_grade(profile: Profile, results: dict[str, dict]) -> tuple[str, list[str]]:
    """Apply grade_rules and return (worst allowed grade, reasons)."""
    grade_idx = 0
    reasons: list[str] = []
    for rule in profile.grade_rules:
        cond = rule.get("if")
        cap = rule.get("max_grade")
        if not cond or cap not in GRADES:
            continue
        try:
            hit = eval_condition(str(cond), results)
        except ValueError as exc:
            log.error("%s", exc)
            continue
        if hit:
            idx = GRADES.index(cap)
            if idx > grade_idx:
                grade_idx = idx
            reasons.append(rule.get("reason") or f"{cond} → max {cap}")
    return GRADES[grade_idx], reasons
