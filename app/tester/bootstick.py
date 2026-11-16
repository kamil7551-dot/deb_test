"""Create the TESTERCFG config storage on the tester's own boot stick.

A stick written with Rufus (DD mode), Etcher or dd holds only the ISO (~600 MB); the rest is unallocated.
We append one FAT32 partition labelled TESTERCFG in that free space, so reports, logs and config survive
reboots on a single stick and can be read on any Windows/Linux PC.
A stick written with Rufus in ISO mode is one big FAT32 partition: there we create a TESTERCFG/ folder.

Safety (AGENTS.md rule 7): only a disk that carries the tester image itself (filesystem label = ISO volume
label, ITAD-TESTER) and is removable or on USB is ever touched; existing partitions are never changed,
and the UI asks the operator to confirm first. Internal disks of the tested machine are never candidates.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from tester.core import config

log = logging.getLogger("tester.bootstick")

LABEL = "TESTERCFG"
MIN_FREE_BYTES = 64 * 1024 * 1024
ALIGN = 2048                    # sectors (1 MiB at 512 B/sector)
FOLDER_FS = {"vfat", "exfat", "ntfs", "ntfs3"}


class StickError(RuntimeError):
    pass


@dataclass
class Stick:
    disk: str                   # /dev/sdb (or an image file in tests/CI)
    model: str
    size_bytes: int
    mode: str                   # "partition" | "folder"
    free_bytes: int = 0
    start: int = 0              # first sector of the new partition (mode "partition")
    pttype: str = "dos"
    folder_dev: str = ""        # partition that gets the TESTERCFG/ folder (mode "folder")
    folder_fs: str = ""

    def describe(self) -> str:
        size = f"{self.size_bytes / 1e9:.1f} GB"
        name = f"{self.model} ({self.disk}, {size})" if self.model else f"{self.disk} ({size})"
        if self.mode == "partition":
            return f"{name}: nowa partycja FAT32 „{LABEL}” w wolnym miejscu {self.free_bytes / 1e9:.1f} GB"
        return f"{name}: folder {LABEL}/ na partycji {self.folder_dev}"


def _run(cmd: list[str], stdin: str | None = None, timeout: float = 60) -> str:
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StickError(f"{cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise StickError(f"{' '.join(cmd)}: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout


def _truthy(v) -> bool:
    return v is True or str(v).strip() in ("1", "true")


def _nodes(dev: dict) -> list[dict]:
    out = [dev]
    for c in dev.get("children") or []:
        out += _nodes(c)
    return out


# ---------- planning (pure, testable on recorded lsblk/sfdisk output) ----------

def plan_free(table: dict, size_bytes: int) -> tuple[int, int]:
    """(first aligned free sector, free bytes) after the last partition, from `sfdisk --json` output."""
    pt = table.get("partitiontable", table)
    ssz = int(pt.get("sectorsize", 512))
    total = size_bytes // ssz
    parts = pt.get("partitions") or []
    used_end = max((int(p["start"]) + int(p["size"]) for p in parts), default=ALIGN)
    if pt.get("label") == "gpt":
        total -= 34             # backup GPT at the very end of the stick (after sgdisk -e)
    start = -(-used_end // ALIGN) * ALIGN
    if pt.get("label") == "dos" and len([p for p in parts if int(p.get("start", 0)) > 0]) >= 4:
        return start, 0         # no free primary slot
    return start, max(0, (total - start) * ssz)


def pick_stick(lsblk: dict, iso_label: str | None = None) -> tuple[dict, list[dict]] | None:
    """The removable/USB disk that carries the tester image, from `lsblk -J -b` output."""
    iso_label = iso_label or config.ISO_LABEL
    for disk in lsblk.get("blockdevices") or []:
        if disk.get("type") != "disk":
            continue
        nodes = _nodes(disk)
        if not any((n.get("label") or "") == iso_label for n in nodes):
            continue
        if not (_truthy(disk.get("rm")) or (disk.get("tran") or "") == "usb"):
            log.warning("tester image found on %s, but it is not removable/USB: not touching it", disk.get("path"))
            continue
        return disk, nodes
    return None


def has_storage(nodes: list[dict]) -> bool:
    return any((n.get("label") or "").upper() == LABEL for n in nodes)


# ---------- probing the live system ----------

LSBLK = ["lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,FSTYPE,LABEL,RM,TRAN,SIZE,MODEL,PTTYPE"]


def probe_file(path: str) -> Stick:
    """Plan for an image file (CI / tests): always partition mode."""
    size = os.path.getsize(path)
    table = json.loads(_run(["sfdisk", "--json", path]))
    start, free = plan_free(table, size)
    pttype = table["partitiontable"].get("label", "dos")
    return Stick(path, "image", size, "partition", free, start, pttype)


def find() -> Stick | None:
    """Return a plan for the boot stick, or None (no stick, storage already present, or no room)."""
    try:
        lsblk = json.loads(_run(LSBLK))
    except (StickError, ValueError) as exc:
        log.warning("lsblk: %s", exc)
        return None
    picked = pick_stick(lsblk)
    if not picked:
        return None
    disk, nodes = picked
    if has_storage(nodes):
        return None
    path, size = disk.get("path") or f"/dev/{disk['name']}", int(disk.get("size") or 0)
    model = (disk.get("model") or "").strip()
    try:
        table = json.loads(_run(["sfdisk", "--json", path]))
        start, free = plan_free(table, size)
        pttype = table["partitiontable"].get("label", "dos")
    except (StickError, ValueError, KeyError) as exc:
        log.info("no usable partition table on %s: %s", path, exc)
        start, free, pttype = 0, 0, ""
    if free >= MIN_FREE_BYTES and pttype in ("dos", "gpt"):
        return Stick(path, model, size, "partition", free, start, pttype)
    # Rufus ISO mode: the image lives on a writable FAT/exFAT/NTFS partition
    for n in nodes:
        if n.get("fstype") in FOLDER_FS and (n.get("label") or "") == config.ISO_LABEL:
            return Stick(path, model, size, "folder", folder_dev=n.get("path") or f"/dev/{n['name']}",
                         folder_fs=n["fstype"])
    return None


# ---------- creating ----------

def _wait_node(node: str) -> None:
    for _ in range(20):
        if shutil.which("udevadm"):
            subprocess.run(["udevadm", "settle", "--timeout=5"], capture_output=True)
        if os.path.exists(node):
            return
        time.sleep(0.5)
    raise StickError(f"the kernel does not see the new partition {node}")


def _fill(where: Path, template: Path) -> None:
    if template.is_dir():
        for item in template.iterdir():
            dst = where / item.name
            if dst.exists():
                continue
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copyfile(item, dst)
    for d in ("queue", "sent", "failed", "logs", "profiles", "wifi"):
        (where / d).mkdir(exist_ok=True)


def add_partition(stick: Stick) -> str:
    """Append the TESTERCFG partition, format it FAT32, copy the template. Returns the partition node."""
    if stick.mode != "partition" or stick.free_bytes < MIN_FREE_BYTES:
        raise StickError("not enough free space on the stick")
    is_file = os.path.isfile(stick.disk)
    table = json.loads(_run(["sfdisk", "--json", stick.disk]))
    before = {p["node"] for p in table["partitiontable"].get("partitions") or []}
    if stick.pttype == "gpt":
        _run(["sgdisk", "-e", stick.disk])
        _run(["sgdisk", "-n", f"0:{stick.start}:0", "-t", "0:0700", "-c", f"0:{LABEL}", stick.disk])
    else:
        _run(["sfdisk", "--append", "--no-reread", "--force", "--no-tell-kernel", stick.disk],
             stdin=f"{stick.start},,c\n")
    table = json.loads(_run(["sfdisk", "--json", stick.disk]))
    new = [p for p in table["partitiontable"]["partitions"] if p["node"] not in before]
    if len(new) != 1:
        raise StickError("partition table did not change as expected")
    part = new[0]
    template = config.CFG_TEMPLATE
    if is_file:                 # CI: format inside the image file, copy with mtools
        offset_bytes = int(part["start"]) * 512
        _run(["mkfs.vfat", "-F", "32", "-n", LABEL, f"--offset={part['start']}", stick.disk,
              str(int(part["size"]) // 2)])
        tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"testercfg-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        _fill(tmp, template)
        env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
        subprocess.run(["mcopy", "-s", "-i", f"{stick.disk}@@{offset_bytes}", *[str(p) for p in tmp.iterdir()],
                        "::/"], check=True, env=env, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
        return part["node"]
    nr = part["node"][len(stick.disk):].lstrip("p")
    try:
        _run(["partx", "-a", "--nr", nr, stick.disk])
    except StickError as exc:       # already known to the kernel
        log.info("partx: %s", exc)
    node = part["node"]
    _wait_node(node)
    _run(["mkfs.vfat", "-F", "32", "-n", LABEL, node], timeout=180)
    from tester import setup
    if not setup.mount(node, "vfat", setup.PROBE, ro=False):
        raise StickError(f"cannot mount {node}")
    try:
        _fill(setup.PROBE, template)
    finally:
        _run(["umount", str(setup.PROBE)])
    return node


def add_folder(stick: Stick) -> str:
    from tester import setup
    if not setup.mount(stick.folder_dev, stick.folder_fs, setup.PROBE, ro=False):
        raise StickError(f"cannot mount {stick.folder_dev} read-write")
    try:
        where = setup.PROBE / LABEL
        where.mkdir(exist_ok=True)
        _fill(where, config.CFG_TEMPLATE)
    finally:
        _run(["umount", str(setup.PROBE)])
    return stick.folder_dev


def create(stick: Stick) -> dict:
    """Create the storage and switch the running tester to it. Returns the new state (see setup.attach)."""
    from tester import setup
    log.info("creating %s storage: %s", LABEL, stick.describe())
    if stick.mode == "partition":
        node = add_partition(stick)
        if os.path.isfile(stick.disk):
            return {"persistent": False, "device": node}
        state = setup.attach(node, "vfat", "")
    else:
        dev = add_folder(stick)
        state = setup.attach(dev, stick.folder_fs, LABEL)
    if not state.get("persistent"):
        raise StickError("storage created but could not be mounted; reboot the tester")
    reopen_file_logs()
    return state


def reopen_file_logs() -> None:
    """Point open log files at the new storage (the old ones are hidden under the bind mount)."""
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.FileHandler):
            with h.lock:
                if h.stream:
                    h.stream.close()
                h.stream = None
