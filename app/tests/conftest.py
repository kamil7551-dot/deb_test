import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TESTER_PROFILES", str(ROOT.parent / "profiles"))
os.environ.setdefault("TESTER_TRUST_CLOCK", "1")

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()
