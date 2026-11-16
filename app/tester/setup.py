"""Boot-time setup (run as root by itad-tester-setup.service before the UI starts).

1. Find the config storage: a partition labelled TESTERCFG, or a TESTERCFG/ folder on any vfat/exfat/ext4
   partition (e.g. the Ventoy data partition or a second USB stick).
2. Bind it to /var/lib/tester so the queue, counter, profiles and config survive reboots.
3. Install the ProSpectra token (/etc/tester/prospectra.env, 0600) and Wi-Fi profiles (NetworkManager).

Without config storage the tester still works; reports then live in RAM until sent.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

import yaml

from tester.core import config
from tester.core.util import run as sh

log = logging.getLogger("tester.setup")

LABEL = "TESTERCFG"
MNT = Path("/mnt/testercfg")
PROBE = Path("/mnt/testercfg-probe")
FS_OK = {"vfat", "exfat", "ext4", "ext3", "ext2", "ntfs", "ntfs3"}


def candidates() -> list[tuple[str, str, str]]:
    out = sh(["lsblk", "-rpno", "NAME,TYPE,FSTYPE,LABEL"]) or ""
    parts = []
    for line in out.splitlines():
        f = line.split(" ")
        if len(f) >= 3 and f[1] in ("part", "disk") and f[2] in FS_OK:
            label = f[3].replace("\\x20", " ") if len(f) > 3 else ""
            parts.append((f[0], f[2], label))
    return parts


def mount(dev: str, fstype: str, where: Path, ro: bool) -> bool:
    where.mkdir(parents=True, exist_ok=True)
    opts = ["ro" if ro else "rw", "noatime"]
    if fstype in ("vfat", "exfat"):
        opts += ["uid=0", "gid=0", "umask=077"]
    if fstype in ("vfat",):
        opts += ["utf8"]
    return sh(["mount", "-t", "ntfs3" if fstype == "ntfs" else fstype, "-o", ",".join(opts), dev, str(where)],
              timeout=20, check_rc=True) is not None


def find_storage() -> tuple[str, str, str] | None:
    """Return (device, fstype, subdir) of the config storage."""
    parts = candidates()
    for dev, fs, label in parts:
        if label.upper() == LABEL:
            return dev, fs, ""
    for dev, fs, _ in parts:
        if not mount(dev, fs, PROBE, ro=True):
            continue
        try:
            found = (PROBE / LABEL).is_dir()
        finally:
            sh(["umount", str(PROBE)], timeout=20)
        if found:
            return dev, fs, LABEL
    return None


def install_secrets(data: Path) -> None:
    env_src = data / "prospectra.env"
    if env_src.exists():
        config.ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(env_src, config.ENV_FILE)
        os.chmod(config.ENV_FILE, 0o600)
        log.info("ProSpectra token installed")
    nm_dir = Path("/etc/NetworkManager/system-connections")
    wifi = sorted((data / "wifi").glob("*.nmconnection")) if (data / "wifi").is_dir() else []
    if wifi:
        nm_dir.mkdir(parents=True, exist_ok=True)
        for f in wifi:
            dst = nm_dir / f.name
            shutil.copyfile(f, dst)
            os.chmod(dst, 0o600)
        sh(["nmcli", "connection", "reload"], timeout=15)
        log.info("installed %d Wi-Fi profile(s)", len(wifi))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    state = {"persistent": False}
    found = find_storage()
    if found:
        dev, fs, sub = found
        if mount(dev, fs, MNT, ro=False):
            src = MNT / sub if sub else MNT
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            if sh(["mount", "--bind", str(src), str(config.DATA_DIR)], timeout=10, check_rc=True) is not None:
                state = {"persistent": True, "device": dev, "fstype": fs, "subdir": sub}
                log.info("config storage: %s (%s) %s", dev, fs, sub or "")
    else:
        log.warning("no %s storage found; reports will be kept in RAM only", LABEL)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    for d in ("queue", "sent", "failed", "profiles", "logs"):
        (config.DATA_DIR / d).mkdir(exist_ok=True)
    install_secrets(config.DATA_DIR)
    config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.STATE_FILE.write_text(yaml.safe_dump(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
