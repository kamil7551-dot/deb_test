import struct

from conftest import fixture

from tester.core.util import is_placeholder_serial, parse_dmidecode, parse_size_to_gb, parse_speed_mts
from tester.modules import battery, memory, storage, sysinfo, tpm


def test_placeholder_serials():
    for s in ["To be filled by O.E.M.", "Default string", "0123456789", "", None, "00000000", "System Serial Number"]:
        assert is_placeholder_serial(s), s
    assert not is_placeholder_serial("5CD1234XYZ")
    assert not is_placeholder_serial("PF2ABCDE")


def test_size_speed():
    assert parse_size_to_gb("8 GB") == 8
    assert parse_size_to_gb("8192 MB") == 8
    assert parse_speed_mts("3200 MT/s") == 3200
    assert parse_speed_mts("2666 MHz") == 2666


def test_memory_smbios_and_spd_merge():
    recs = [r for r in parse_dmidecode(fixture("dmidecode_t17_laptop.txt")) if r["type"] == 17]
    assert len(recs) == 3
    mods = memory.parse_smbios_modules(recs)
    assert [m["slot"] for m in mods] == ["DIMM 0", "DIMM 1"]
    assert mods[0]["part"] == "M471A1K43DB1-CWE"
    assert mods[0]["configured_mts"] == 2666
    spd = memory.parse_decode_dimms(fixture("decode_dimms.txt"))
    assert len(spd) == 1
    assert spd[0]["max_mts"] == 3200 and spd[0]["pc_name"] == "PC4-25600"
    assert spd[0]["manufactured"] == "2020-W37"
    merged = memory.merge(mods, spd)
    assert merged[0]["spd"] == "ok"                 # matched by serial 0x4A1B2C3D
    assert merged[1]["spd"] == "unavailable"
    warns = memory.warnings_for(merged, 15.4)
    assert any("znamionowo 3200 MT/s, pracuje 2666 MT/s" in w for w in warns)
    assert "Moduły różnych producentów lub modeli" in warns


def test_tpm2():
    d = tpm.parse_tpm2_fixed(fixture("tpm2_fixed.txt"))
    assert d["version"] == "2.0"
    assert d["manufacturer"] == "Infineon"
    assert d["firmware"] == "7.61.10.57600"
    assert d["vendor_string"] == "SLB9670"
    v = tpm.parse_tpm2_variable(fixture("tpm2_variable.txt"))
    assert v["owner_auth_set"] is True and v["endorsement_auth_set"] is False


def test_storage_list_and_smart():
    disks = storage.list_disks(fixture("lsblk.json"))
    assert [d["name"] for d in disks] == ["nvme0n1"]     # tester stick and external USB excluded
    nv = storage.parse_smart(fixture("smartctl_nvme.json"))
    assert nv["type"] == "NVMe" and nv["health_pct"] == 97 and nv["power_on_hours"] == 4321
    hdd = storage.parse_smart(fixture("smartctl_hdd_bad.json"))
    assert hdd["type"] == "HDD" and hdd["reallocated_sectors"] == 16
    status, note = storage.evaluate([{"name": "sda", **hdd}], False, 10)
    assert status == "warn" and "realokowane" in note
    assert storage.evaluate([], True, 10)[0] == "fail"


def test_lspci_gpus():
    gpus = sysinfo.parse_lspci_gpus(fixture("lspci_mm.txt"))
    assert [g["name"] for g in gpus] == ["Intel Iris Xe Graphics", "NVIDIA GeForce MX450"]


def test_cpu_info():
    text = "processor\t: 0\nmodel name\t: 11th Gen Intel(R) Core(TM) i5-1145G7 @ 2.60GHz\nphysical id\t: 0\ncore id\t: 0\n\n" \
           "processor\t: 1\nmodel name\t: 11th Gen Intel(R) Core(TM) i5-1145G7 @ 2.60GHz\nphysical id\t: 0\ncore id\t: 0\n"
    info = sysinfo.cpu_info(text)
    assert info["cpu"] == "Intel Core i5-1145G7"
    assert info["cpu_cores"] == 1 and info["cpu_threads"] == 2


def _edid(w_px=1920, h_px=1080, w_mm=309, h_mm=174, name=b"LP140WF9"):
    e = bytearray(128)
    e[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    e[8:10] = struct.pack(">H", (ord("L") - 64) << 10 | (ord("G") - 64) << 5 | (ord("D") - 64))
    d = bytearray(18)
    d[0], d[1] = 0x01, 0x1D
    d[2], d[4] = w_px & 0xFF, (w_px >> 8) << 4
    d[5], d[7] = h_px & 0xFF, (h_px >> 8) << 4
    d[12], d[13], d[14] = w_mm & 0xFF, h_mm & 0xFF, ((w_mm >> 8) << 4) | (h_mm >> 8)
    e[54:72] = d
    e[72:90] = b"\x00\x00\x00\xfe\x00" + name.ljust(13, b"\x0a")
    return bytes(e)


def test_edid():
    info = sysinfo.parse_edid(_edid())
    assert info["width_px"] == 1920 and info["height_px"] == 1080
    assert info["diagonal_in"] == 14.0
    assert info["vendor"] == "LGD" and info["panel"] == "LP140WF9"
    assert sysinfo.format_screen(info) == '14" 1920x1080'


def test_chassis():
    assert sysinfo.chassis_kind("Notebook") == "laptop"
    assert sysinfo.chassis_kind("Mini Tower") == "desktop"
    assert sysinfo.chassis_kind("All In One") == "aio"


def test_battery(tmp_path):
    bat = tmp_path / "BAT0"
    bat.mkdir()
    for k, v in {"type": "Battery", "present": "1", "energy_full": "43000000", "energy_full_design": "50000000",
                 "energy_now": "21500000", "cycle_count": "212", "manufacturer": "SMP", "model_name": "5B10W13930",
                 "capacity": "50", "status": "Discharging"}.items():
        (bat / k).write_text(v)
    bats = battery.batteries(tmp_path)
    assert bats[0]["health_pct"] == 86 and bats[0]["cycles"] == 212
    r = battery.evaluate(bats, {"warn_below_health_pct": 90}, "laptop")
    assert r.status == "warn"
    assert battery.evaluate([], {}, "laptop").status == "fail"
    assert battery.evaluate([], {}, "desktop").status == "skip"
