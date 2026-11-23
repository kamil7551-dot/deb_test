"""Disk format/wipe planning and SMART self-test parsing (no real disk is touched)."""

import json

from conftest import fixture

from tester.modules import storage, wipe

LSBLK = {"blockdevices": [
    {"name": "nvme0n1", "type": "disk", "tran": "nvme", "size": 256060514304, "model": "PM991", "serial": "S1",
     "rota": False, "label": None, "mountpoint": None,
     "children": [{"name": "nvme0n1p1", "type": "part", "label": "SYSTEM", "mountpoint": None},
                  {"name": "nvme0n1p3", "type": "part", "label": "Windows", "mountpoint": None}]},
    {"name": "sda", "type": "disk", "tran": "sata", "size": 500107862016, "model": "Samsung SSD 860", "serial": "S2",
     "rota": False, "label": None, "mountpoint": None,
     "children": [{"name": "sda1", "type": "part", "label": "DATA", "mountpoint": "/mnt/x"}]},
    {"name": "sdb", "type": "disk", "tran": "usb", "size": 15502147584, "model": "DataTraveler", "serial": "U1",
     "rota": False, "label": "ITAD-TESTER", "mountpoint": None,
     "children": [{"name": "sdb3", "type": "part", "label": "TESTERCFG", "mountpoint": "/mnt/testercfg"}]},
]}


def test_targets_skip_the_stick_and_flag_mounted():
    t = {d["name"]: d for d in wipe.targets(json.dumps(LSBLK))}
    assert set(t) == {"nvme0n1", "sda"}                     # the tester stick is never offered
    assert t["nvme0n1"]["partitions"] == ["nvme0n1p1", "nvme0n1p3"] and t["nvme0n1"]["kind"] == "nvme"
    assert t["sda"]["mounted"] == ["/mnt/x"]
    try:
        wipe.wipe_disk(t["sda"], "quick", "ntfs", "DANE", lambda f: None)
        raise AssertionError("mounted disk must be refused")
    except wipe.WipeError as exc:
        assert "zamontowana" in str(exc)


def test_quick_plan_merges_into_one_partition():
    d = wipe.targets(json.dumps(LSBLK))[0]
    plan = wipe.plan_quick(d, "ntfs", "DANE")
    argv = [c for c, _ in plan]
    assert argv[0] == ["wipefs", "-a", "-f", "/dev/nvme0n1p1"]
    assert ["wipefs", "-a", "-f", "/dev/nvme0n1"] in argv
    sfd = next((c, i) for c, i in plan if c[0] == "sfdisk")
    assert sfd[1] == f"label: gpt\n,,{wipe.GPT_BASIC_DATA}\n"
    assert argv[-1] == ["mkfs.ntfs", "-Q", "-F", "-L", "DANE", "/dev/nvme0n1p1"]
    assert wipe.part_node("sda") == "/dev/sda1"
    assert wipe.plan_quick(d, "none")[-1][0][0] == "udevadm"


def test_secure_method_choice():
    hd = fixture("hdparm_security.txt")
    s = wipe.ata_security(hd)
    assert s == {"supported": True, "enabled": False, "frozen": False, "enhanced": True, "estimate_min": 2}
    sata = {"kind": "ssd", "transport": "sata"}
    assert wipe.secure_method(sata, hd) == "ata-secure-erase-enhanced"
    assert wipe.secure_method(sata, hd.replace("not\tfrozen", "\tfrozen")) == "overwrite-zeros"
    assert wipe.secure_method({"kind": "nvme"}) == "nvme-format"
    assert wipe.secure_method({"kind": "hdd", "transport": "usb"}, hd) == "overwrite-zeros"


def test_selftest_parsing():
    ata_running = {"ata_smart_data": {"self_test": {"status": {"value": 249, "remaining_percent": 90}}}}
    assert storage.parse_selftest(json.dumps(ata_running)) == {"self_test": "running", "remaining_pct": 90}
    ata_done = {"ata_smart_data": {"self_test": {"status": {"value": 0, "passed": True}}},
                "ata_smart_self_test_log": {"standard": {"table": [
                    {"type": {"string": "Short offline"}, "status": {"value": 0, "string": "Completed without error", "passed": True}}]}}}
    assert storage.parse_selftest(json.dumps(ata_done))["self_test"] == "passed"
    ata_bad = json.loads(json.dumps(ata_done))
    ata_bad["ata_smart_self_test_log"]["standard"]["table"][0]["status"] = {"value": 121, "string": "Completed: read failure", "passed": False}
    assert storage.parse_selftest(json.dumps(ata_bad)) == {"self_test": "failed", "self_test_result": "Completed: read failure"}
    nv_run = {"nvme_self_test_log": {"current_self_test_operation": {"value": 1}, "current_self_test_completion_percent": 30}}
    assert storage.parse_selftest(json.dumps(nv_run)) == {"self_test": "running", "remaining_pct": 70}
    nv_ok = {"nvme_self_test_log": {"current_self_test_operation": {"value": 0},
                                    "table": [{"self_test_result": {"value": 0, "string": "Completed without error"}}]}}
    assert storage.parse_selftest(json.dumps(nv_ok))["self_test"] == "passed"
    status, note = storage.evaluate([{"name": "sda", "self_test": "failed", "self_test_result": "read failure"}], True, 10)
    assert status == "fail" and "autotest" in note
