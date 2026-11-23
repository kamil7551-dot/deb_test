"""Visual grading (cosmetics), LCD defects, touchscreen/Bluetooth detection and their place in the report."""

from tester.core import config
from tester.core.profiles import Profile, suggest_grade
from tester.core.report import build_device, build_report
from tester.core.runner import Session
from tester.modules import cosmetics, sysinfo

OPTS = {"questions": [{"id": "kb_backlight", "text": "Podświetlenie?"}]}


def test_cosmetics_needs_every_part_and_answer():
    data = {"grades": {"lcd": "A", "lid": "B", "body": "A"}, "answers": {}}
    msg = cosmetics.missing(OPTS, data)
    assert "Palmrest" in msg and "Podświetlenie?" in msg
    data["grades"]["palmrest"] = "C"
    data["answers"]["kb_backlight"] = False          # "Nie" is an answer too
    assert cosmetics.missing(OPTS, data) is None
    assert [p["id"] for p in cosmetics.parts({})] == ["lcd", "lid", "body", "palmrest"]


def test_overall_grade_never_better_than_worst_part():
    results = {"cosmetics": {"status": "pass", "data": {"grades": {"lcd": "A", "lid": "C", "body": "B", "palmrest": "C"}}}}
    grade, reasons = suggest_grade(Profile("p", "p"), results)
    assert grade == "C"
    assert reasons == ["Najgorszy element: klapa, palmrest (C)"]
    results["battery"] = {"status": "warn", "data": {"health_pct": 40}}
    prof = Profile("p", "p", grade_rules=[{"if": "battery.health_pct < 50", "max_grade": "D", "reason": "Bateria"}])
    assert suggest_grade(prof, results)[0] == "D"


def test_touchscreen_and_bluetooth_detection(tmp_path):
    udev = tmp_path / "udev"
    udev.mkdir()
    (udev / "c13:64").write_text("E:ID_INPUT=1\nE:ID_INPUT_KEYBOARD=1\n")
    assert not sysinfo.has_touchscreen(udev)
    (udev / "c13:70").write_text("E:ID_INPUT=1\nE:ID_INPUT_TOUCHSCREEN=1\n")
    assert sysinfo.has_touchscreen(udev)

    cls = tmp_path / "class"
    (cls / "rfkill" / "rfkill0").mkdir(parents=True)
    (cls / "rfkill" / "rfkill0" / "type").write_text("wlan\n")
    assert not sysinfo.has_bluetooth(cls)
    (cls / "rfkill" / "rfkill1").mkdir()
    (cls / "rfkill" / "rfkill1" / "type").write_text("bluetooth\n")
    assert sysinfo.has_bluetooth(cls)


def test_report_carries_grades_defects_and_flags():
    results = {
        "sysinfo": {"status": "pass", "data": {"manufacturer": "Dell Inc.", "touchscreen": False, "bluetooth": True}},
        "display": {"status": "warn", "data": {"defects": ["Zarysowania"]}},
        "camera": {"status": "pass", "data": {}},
        "cosmetics": {"status": "pass", "data": {"grades": {"lcd": "A", "lid": "A", "body": "B", "palmrest": "A"},
                                                 "notes": {"body": "rysa na spodzie"},
                                                 "answers": {"kb_backlight": True}}},
    }
    dev = build_device(results)
    assert dev["touchscreen"] is False and dev["bluetooth"] is True and dev["camera"] is True
    assert dev["cosmetic_grades"]["body"] == "B" and dev["cosmetic_notes"] == {"body": "rysa na spodzie"}
    assert dev["lcd_defects"] == ["Zarysowania"] and dev["answers"] == {"kb_backlight": True}
    s = Session(profile=Profile("p", "p"), modules=[])
    s.serial, s.results = "F67XBL3", results
    rep = build_report(s, config.StationConfig(station="MG"), grade="B", run_id="mg-1")
    assert rep["device"]["tester"]["suggested_grade"] == "B"
