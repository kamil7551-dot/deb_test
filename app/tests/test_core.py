import json

from tester.core import config
from tester.core.module import Result, discover
from tester.core.profiles import load_profiles, parse_profile, suggest_grade
from tester.core.report import api_body, build_report
from tester.core.runner import Session, overall_result
from tester.upload import queue
from tester.upload.prospectra import Client, Response


def test_discover_contract():
    mods = discover()
    for mid in ("sysinfo", "bios", "memory", "tpm", "storage", "battery", "network"):
        assert mid in mods and mods[mid].kind == "auto"
    for m in mods.values():
        assert m.mod.CONTRACT == 1 and m.name
        assert hasattr(m.mod, "run") or hasattr(m.mod, "build")


def test_profiles_reference_existing_modules():
    mods = discover()
    profiles = load_profiles()
    assert {"laptop_full", "desktop", "quick"} <= set(profiles)
    for p in profiles.values():
        for t in p.tests:
            assert t.id in mods, f"{p.id}: unknown module {t.id}"


def test_grade_rules():
    p = parse_profile({"profile": "x", "tests": ["battery"], "grade_rules": [
        {"if": "battery.health_pct < 70", "max_grade": "B"},
        {"if": "display.status == fail", "max_grade": "C"}]})
    assert suggest_grade(p, {"battery": {"status": "pass", "data": {"health_pct": 65}}})[0] == "B"
    g, reasons = suggest_grade(p, {"battery": {"status": "pass", "data": {"health_pct": 90}},
                                   "display": {"status": "fail", "data": {}}})
    assert g == "C" and len(reasons) == 1
    assert suggest_grade(p, {})[0] == "A"


def test_overall_result():
    assert overall_result({"a": {"status": "pass"}, "b": {"status": "warn"}}) == "pass"
    assert overall_result({"a": {"status": "pass"}, "b": {"status": "skip"}}) == "partial"
    assert overall_result({"a": {"status": "fail"}, "b": {"status": "skip"}}) == "fail"


def _session():
    mods = discover()
    p = parse_profile({"profile": "t", "tests": ["sysinfo", "battery"]})
    s = Session(profile=p, modules=[mods["sysinfo"], mods["battery"]])
    s.record("sysinfo", Result("pass", {"manufacturer": "HP", "model": "EliteBook 840 G8", "cpu": "Intel Core i5-1145G7",
                                        "ram_gb": 16, "serial": "5CD1234XYZ"}), 0.1)
    s.record("battery", Result("warn", {"present": True, "health_pct": 87, "cycles": 212}, "Kondycja 87%"), 0.1)
    s.serial = "5CD1234XYZ"
    return s


def test_report_matches_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    cfg = config.StationConfig(station="Stanowisko 1")
    rep = build_report(_session(), cfg, grade="A")
    body = api_body(rep)
    for key in ("run_id", "serial", "tested_at", "result"):
        assert body[key]
    assert body["result"] == "pass"
    assert body["tested_at"].endswith("Z")
    assert body["run_id"].startswith("stanowisko-1-")
    assert body["device"]["battery"] == {"health_pct": 87, "cycles": 212}
    assert {"name": "Bateria", "status": "pass", "note": "Kondycja 87%"} in body["checks"]
    assert "_local" not in body
    assert len(json.dumps(body).encode()) < 256 * 1024


class FakeClient(Client):
    def __init__(self, responses):
        super().__init__("https://example.invalid/api/ext/v1", "psk_test", sleep=lambda s: None)
        self.responses = list(responses)
        self.sent = []

    def _request(self, method, path, body=None, content_type="application/json"):
        self.sent.append((method, path, json.loads(body) if body else None))
        return self.responses.pop(0)


def test_queue_send_retry_and_idempotency(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    rep = build_report(_session(), config.StationConfig(station="S1"))
    queue.enqueue(rep, tmp_path)
    ok = Response(201, {"test": {"id": "t_1", "linked": False, "url": "https://x/#/stock/p-1"}})
    client = FakeClient([Response(0, None, "timeout"), Response(503, None), ok])
    out = queue.flush(client, tmp_path)
    assert out[0].state == "sent" and out[0].linked is False
    assert len({s[2]["run_id"] for s in client.sent}) == 1      # same run_id on every retry
    assert queue.counts(tmp_path) == {"queued": 0, "sent": 1, "failed": 0}


def test_queue_final_error_goes_to_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    queue.enqueue(build_report(_session(), config.StationConfig()), tmp_path)
    client = FakeClient([Response(400, {"error": "Zły numer seryjny", "code": "bad_request"})])
    out = queue.flush(client, tmp_path)
    assert out[0].state == "failed" and "Zły numer" in out[0].message
    assert queue.counts(tmp_path)["failed"] == 1


def test_queue_waits_without_token(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    queue.enqueue(build_report(_session(), config.StationConfig()), tmp_path)
    out = queue.flush(Client("https://x", None), tmp_path)
    assert out[0].state == "waiting"
    assert queue.counts(tmp_path)["queued"] == 1
