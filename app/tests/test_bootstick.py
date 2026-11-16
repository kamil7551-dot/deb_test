"""TESTERCFG creation on the boot stick.

sfdisk_iso_dd.json is the partition table of the released itad-tester-0.1.0.iso (as written by dd/Rufus DD).
lsblk_bootstick.json is a laptop (internal NVMe with Windows) with that stick in a USB port.
"""

import json
import shutil
import subprocess

import pytest
from conftest import fixture

from tester import bootstick, setup

LSBLK = json.loads(fixture("lsblk_bootstick.json"))
SFDISK = json.loads(fixture("sfdisk_iso_dd.json"))
STICK = 15502147584


def test_plan_free_after_iso():
    start, free = bootstick.plan_free(SFDISK, STICK)
    assert start == 1232896                      # first 1 MiB boundary after the ISO (602 MiB)
    assert start % bootstick.ALIGN == 0
    assert free == (STICK // 512 - start) * 512
    _, none = bootstick.plan_free(SFDISK, 631242752)   # stick exactly as big as the ISO
    assert none == 0


def test_plan_free_no_primary_slot():
    full = {"partitiontable": {"label": "dos", "sectorsize": 512, "partitions": [
        {"start": 2048 * (i + 1), "size": 2048} for i in range(4)]}}
    assert bootstick.plan_free(full, STICK)[1] == 0


def test_pick_only_the_usb_stick_with_the_image():
    disk, nodes = bootstick.pick_stick(LSBLK, "ITAD-TESTER")
    assert disk["path"] == "/dev/sdb"
    assert not bootstick.has_storage(nodes)
    assert bootstick.pick_stick(LSBLK, "OTHER-ISO") is None


def test_internal_disk_is_never_picked():
    data = json.loads(json.dumps(LSBLK))
    data["blockdevices"][1].update(rm=False, tran="sata")      # image on a non-removable SATA disk
    assert bootstick.pick_stick(data, "ITAD-TESTER") is None


def _fake_run(lsblk, sfdisk):
    def run(cmd, stdin=None, timeout=60):
        if cmd[0] == "lsblk":
            return json.dumps(lsblk)
        if cmd[0] == "sfdisk":
            if sfdisk is None:
                raise bootstick.StickError("no table")
            return json.dumps(sfdisk)
        raise AssertionError(cmd)
    return run


def test_find_partition_mode(monkeypatch):
    monkeypatch.setattr(bootstick, "_run", _fake_run(LSBLK, SFDISK))
    st = bootstick.find()
    assert st.mode == "partition" and st.disk == "/dev/sdb" and st.start == 1232896
    assert "DataTraveler" in st.describe()


def test_find_nothing_when_storage_exists(monkeypatch):
    data = json.loads(json.dumps(LSBLK))
    data["blockdevices"][1]["children"].append({"name": "sdb3", "path": "/dev/sdb3", "type": "part",
                                                "fstype": "vfat", "label": "TESTERCFG"})
    monkeypatch.setattr(bootstick, "_run", _fake_run(data, SFDISK))
    assert bootstick.find() is None


def test_find_folder_mode_rufus_iso(monkeypatch):
    """Rufus ISO mode: one FAT32 partition over the whole stick, labelled with the ISO volume."""
    data = json.loads(json.dumps(LSBLK))
    usb = data["blockdevices"][1]
    usb.update(fstype=None, label=None)
    usb["children"] = [{"name": "sdb1", "path": "/dev/sdb1", "type": "part", "fstype": "vfat",
                        "label": "ITAD-TESTER", "size": STICK - 1048576}]
    table = {"partitiontable": {"label": "dos", "sectorsize": 512,
                                "partitions": [{"node": "/dev/sdb1", "start": 2048, "size": STICK // 512 - 2048}]}}
    monkeypatch.setattr(bootstick, "_run", _fake_run(data, table))
    st = bootstick.find()
    assert st.mode == "folder" and st.folder_dev == "/dev/sdb1" and st.folder_fs == "vfat"


def test_merge_ram_data_keeps_existing(tmp_path):
    ram, part = tmp_path / "ram", tmp_path / "part"
    (ram / "queue").mkdir(parents=True)
    (ram / "queue" / "r1.json").write_text("{}")
    (ram / "counter").write_text("5")
    part.mkdir()
    (part / "config.yaml").write_text("station: A")
    setup._merge_into(ram, part)
    assert (part / "queue" / "r1.json").exists() and (part / "counter").read_text() == "5"
    (ram / "config.yaml").write_text("station: RAM")
    setup._merge_into(ram, part)
    assert (part / "config.yaml").read_text() == "station: A"


@pytest.mark.skipif(not all(shutil.which(t) for t in ("sfdisk", "mkfs.vfat", "mcopy", "mdir")),
                    reason="needs fdisk, dosfstools, mtools")
def test_add_partition_to_image(tmp_path, monkeypatch):
    """ISO-like MBR (partition 1 from sector 64, an EFI partition; sfdisk will not create the real overlap)."""
    img = tmp_path / "stick.img"
    with open(img, "wb") as f:
        f.truncate(128 * 1024 * 1024)
    subprocess.run(["sfdisk", "--force", "--no-reread", "--no-tell-kernel", str(img)], check=True, capture_output=True,
                   input="label: dos\n64,40000,0,*\n40064,6656,ef\n", text=True)
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "config.yaml").write_text("station: CI\n")
    monkeypatch.setattr(bootstick.config, "CFG_TEMPLATE", tpl)
    st = bootstick.probe_file(str(img))
    assert st.start == 47104 and st.free_bytes == (128 * 2048 - 47104) * 512
    node = bootstick.add_partition(st)
    assert node.endswith("3")
    table = json.loads(subprocess.run(["sfdisk", "--json", str(img)], capture_output=True, text=True).stdout)
    parts = table["partitiontable"]["partitions"]
    assert [p["start"] for p in parts] == [64, 40064, 47104] and parts[2]["type"] == "c"
    out = subprocess.run(["mdir", "-b", "-i", f"{img}@@{47104 * 512}", "::/"], capture_output=True, text=True,
                         env={"MTOOLS_SKIP_CHECK": "1", "PATH": "/usr/bin:/bin"}).stdout
    assert "config.yaml" in out and "queue" in out
