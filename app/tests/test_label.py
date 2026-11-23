"""Label template: placeholders from the report, optional lines, printer encodings, raw TCP send."""

import socket
import threading

from tester import label

REPORT = {
    "run_id": "mg-1-20261123-036705", "serial": "F67XBL3", "tested_at": "2026-11-23T09:10:59Z", "station": "MG-1-1",
    "operator": "test1", "grade": "A", "result": "pass",
    "device": {
        "manufacturer": "Dell Inc.", "model": "Latitude 5520", "cpu": "11th Gen Intel(R) Core(TM) i5-1145G7 @ 2.60GHz",
        "gpu": "Iris Xe Graphics", "ram_gb": 8, "screen": '15.6" 1920x1080',
        "storage": [{"type": "NVMe", "size_gb": 256}], "touchscreen": False, "bluetooth": True,
        "cosmetic_grades": {"lcd": "A", "lid": "A", "body": "A", "palmrest": "A"},
        "tester": {"battery": {"batteries": [{"health_pct": 100}]}},
    },
}


def test_fields_match_the_current_label():
    v = label.fields(REPORT, "Europe/Warsaw")
    assert v["nr"] == "36705"
    assert v["tested_local"] == "2026-11-23 10:10:59"          # UTC -> Warsaw
    assert v["disks"] == "NVME: 256" and v["resolution"] == "1920x1080" and v["ram_gb"] == "8"
    assert v["touchscreen"] == "Nie" and v["bluetooth"] == "Tak" and v["battery_1"] == "100"
    assert v["battery_2"] == "" and v["grade_palmrest"] == "A"


def test_render_drops_optional_lines_and_keeps_big_marker():
    out = label.render(label.DEFAULT["lines"], label.fields(REPORT))
    assert out[0] == "# 36705" and out[-1] == "# GRADE: A"
    assert "Bateria_1: 100%" in out and not any(o.startswith("Bateria_2") for o in out)
    assert not any("Very good" in o or o.startswith("Matryca:") for o in out)   # word descriptions are gone
    assert "Grade kadłubek: A" in out


def test_encodings():
    lines = ["# 36705", "Dell Inc. Latitude 5520", "", "Grade LCD: A"]
    z = label.to_zpl(lines, "F67XBL3", label.Printer(width_mm=62, height_mm=100, dpi=203)).decode()
    assert z.startswith("^XA\n^CI28\n^PW495\n^LL799") and "^FD36705^FS" in z and "^BCN" in z and z.rstrip().endswith("^XZ")
    t = label.to_tspl(lines, "F67XBL3", label.Printer(language="tspl")).decode()
    assert "SIZE 62 mm,100 mm" in t and 'BARCODE' in t and "PRINT 1,1" in t
    assert label.to_text(lines, "", label.Printer(language="text")).decode().startswith("36705\r\n")


def test_send_over_tcp():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    got = []

    def accept():
        c, _ = srv.accept()
        got.append(c.recv(65536))
        c.close()

    th = threading.Thread(target=accept)
    th.start()
    label.send(b"^XA^XZ", label.Printer(host="127.0.0.1", port=srv.getsockname()[1]))
    th.join(5)
    srv.close()
    assert got == [b"^XA^XZ"]


def test_template_file_overrides(tmp_path):
    f = tmp_path / "label.yaml"
    f.write_text("printer: {host: 10.0.0.5, language: tspl}\nlines: ['# {serial}']\n", encoding="utf-8")
    tpl = label.load_template(f)
    p = label.Printer.from_template(tpl)
    assert p.host == "10.0.0.5" and p.language == "tspl" and p.port == 9100
    assert label.render(tpl["lines"], label.fields(REPORT)) == ["# F67XBL3"]
