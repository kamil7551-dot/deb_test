"""Station configuration and well-known paths.

On the live system the config partition (label TESTERCFG, or a TESTERCFG/ folder on a Ventoy/other stick)
is mounted at /var/lib/tester by tester.setup at boot. Every path can be overridden with an environment
variable so the app can run on a developer machine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DATA_DIR = Path(os.environ.get("TESTER_DATA_DIR", "/var/lib/tester"))
BUILTIN_PROFILES = Path(os.environ.get("TESTER_PROFILES", "/opt/tester/profiles"))
ENV_FILE = Path(os.environ.get("TESTER_ENV_FILE", "/etc/tester/prospectra.env"))
STATE_FILE = Path(os.environ.get("TESTER_STATE_FILE", "/run/tester/state.yaml"))

DEFAULT_BASE_URL = "https://prospectra-ai.app/api/ext/v1"


@dataclass
class StationConfig:
    station: str = "Stanowisko"
    operator_prompt: bool = True
    base_url: str = DEFAULT_BASE_URL
    token: str | None = None
    default_profile: str | None = None
    language: str = "pl"
    persistent: bool = False           # True when DATA_DIR is on the USB config partition
    extra: dict = field(default_factory=dict)

    @property
    def station_slug(self) -> str:
        slug = "".join(ch if ch.isalnum() else "-" for ch in self.station.lower()).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        return slug or "station"


def _read_env_token(path: Path) -> str | None:
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith("PROSPECTRA_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    except OSError:
        pass
    return os.environ.get("PROSPECTRA_TOKEN") or None


def load_config() -> StationConfig:
    raw: dict = {}
    cfg_file = DATA_DIR / "config.yaml"
    if cfg_file.exists():
        raw = yaml.safe_load(cfg_file.read_text()) or {}
    pros = raw.get("prospectra") or {}
    state = {}
    if STATE_FILE.exists():
        state = yaml.safe_load(STATE_FILE.read_text()) or {}
    return StationConfig(
        station=str(raw.get("station") or "Stanowisko"),
        operator_prompt=bool(raw.get("operator_prompt", True)),
        base_url=str(pros.get("base_url") or DEFAULT_BASE_URL).rstrip("/"),
        token=_read_env_token(ENV_FILE),
        default_profile=raw.get("default_profile"),
        language=str(raw.get("language") or "pl"),
        persistent=bool(state.get("persistent", False)),
        extra=raw,
    )
