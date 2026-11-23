"""Disk format / data wipe. DESTRUCTIVE: only in profiles that list it, and only after on-screen confirmation.

Two modes, chosen on screen (default from option `mode`):
  quick   remove every partition and filesystem signature, merge the disk into ONE partition (new GPT) and format
          it (option `fs`: ntfs | exfat | none, default ntfs; `label`, default "DANE"). Seconds. Data is recoverable
          with forensic tools.
  secure  erase the data first, then do the quick steps:
          NVMe -> `nvme format --ses=1` (user data erase by the controller, seconds to minutes);
          SATA with ATA Security Erase supported and not frozen -> `hdparm --security-erase(-enhanced)`;
          otherwise (frozen, USB bridge, unsupported) -> overwrite the whole disk with zeros (HDD: hours).
          Afterwards 64 random 1 MiB samples are read back; all zero = verified.
The tester's own stick and any USB disk are never offered (storage.list_disks); a disk with a mounted partition is
refused. Results go to data["disks"]: method, seconds, verified, partitions before/after.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import time

from tester.core.module import Result
from tester.core.util import run as sh
from tester.modules.storage import list_disks

ID = "wipe"
NAME = "Format dysku"
KIND = "interactive"
CONTRACT = 1
ORDER = 200
WAIT_FOR_AUTO = True      # start only after the automatic tests (SMART self-test) have finished

GPT_BASIC_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"   # Windows "basic data" partition (NTFS / exFAT)
CHUNK = 8 * 1024 * 1024


class WipeError(RuntimeError):
    pass


def _run(cmd: list[str], stdin: str | None = None, timeout: float = 600) -> str:
    try:
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WipeError(f"{cmd[0]}: {exc}") from exc
    if p.returncode != 0:
        raise WipeError(f"{' '.join(cmd)}: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


# ---------- discovery ----------

LSBLK = ["lsblk", "-J", "-b", "-o", "NAME,TYPE,TRAN,SIZE,MODEL,SERIAL,ROTA,LABEL,MOUNTPOINT"]


def targets(lsblk_json: str) -> list[dict]:
    """Disks that may be wiped: internal, not the tester stick; with their partitions and mount state."""
    disks = list_disks(lsblk_json)
    tree = {d["name"]: d for d in json.loads(lsblk_json or "{}").get("blockdevices", [])}
    for d in disks:
        parts = tree.get(d["name"], {}).get("children") or []
        d["partitions"] = [p["name"] for p in parts if p.get("type") == "part"]
        d["mounted"] = [p.get("mountpoint") for p in [tree.get(d["name"], {})] + parts if p.get("mountpoint")]
        d["kind"] = "nvme" if d["name"].startswith("nvme") else ("hdd" if d.get("rotational") else "ssd")
    return disks


def part_node(disk: str, n: int = 1) -> str:
    return f"/dev/{disk}{'p' if disk[-1].isdigit() else ''}{n}"


# ---------- plans (pure, unit-tested) ----------

def plan_quick(disk: dict, fs: str = "ntfs", label: str = "DANE") -> list[tuple[list[str], str | None]]:
    """Commands (argv, stdin) that leave the disk with one empty partition."""
    dev = f"/dev/{disk['name']}"
    cmds: list[tuple[list[str], str | None]] = [(["wipefs", "-a", "-f", f"/dev/{p}"], None) for p in disk.get("partitions", [])]
    cmds += [
        (["wipefs", "-a", "-f", dev], None),
        (["sfdisk", "--wipe", "always", "--wipe-partitions", "always", dev], f"label: gpt\n,,{GPT_BASIC_DATA}\n"),
        (["partx", "-u", dev], None),
        (["udevadm", "settle", "--timeout=10"], None),
    ]
    part = part_node(disk["name"])
    if fs == "ntfs":
        cmds.append((["mkfs.ntfs", "-Q", "-F", "-L", label, part], None))
    elif fs == "exfat":
        cmds.append((["mkfs.exfat", "-L", label, part], None))
    return cmds


def ata_security(hdparm_i: str) -> dict:
    """Security section of `hdparm -I`: supported, frozen, enhanced erase, time estimate."""
    sec = hdparm_i.split("Security:", 1)[1] if "Security:" in hdparm_i else ""
    sec = sec.split("Logical Unit WWN", 1)[0]

    def flag(word: str) -> bool | None:
        m = re.search(rf"^\s*(not)?\s*{word}\s*$", sec, re.M)
        return None if not m else not m.group(1)

    est = re.search(r"(\d+)min for SECURITY ERASE UNIT", sec)
    return {
        "supported": flag("supported") is True,
        "enabled": flag("enabled") is True,
        "frozen": flag("frozen") is not False,      # unknown counts as frozen
        "enhanced": bool(re.search(r"^\s*supported: enhanced erase", sec, re.M)),
        "estimate_min": int(est.group(1)) if est else None,
    }


def secure_method(disk: dict, hdparm_i: str = "") -> str:
    if disk["kind"] == "nvme":
        return "nvme-format"
    if disk.get("transport") in ("sata", "ata") and hdparm_i:
        s = ata_security(hdparm_i)
        if s["supported"] and not s["frozen"] and not s["enabled"]:
            return "ata-secure-erase-enhanced" if s["enhanced"] else "ata-secure-erase"
    return "overwrite-zeros"


# ---------- doing it ----------

def overwrite(dev: str, size: int, progress) -> None:
    zero = bytes(CHUNK)
    done = 0
    fd = os.open(dev, os.O_WRONLY)
    try:
        while done < size:
            n = os.write(fd, zero[: min(CHUNK, size - done)])
            done += n
            if done % (CHUNK * 16) < CHUNK:
                progress(done / size)
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_zero(dev: str, size: int, samples: int = 64) -> bool:
    """Read back the first and last MiB plus random 1 MiB samples; True when all bytes are zero."""
    mib = 1024 * 1024
    offsets = [0, max(0, size - mib)] + [random.randrange(0, max(1, size - mib)) // 4096 * 4096 for _ in range(samples)]
    fd = os.open(dev, os.O_RDONLY)
    try:
        for off in offsets:
            os.lseek(fd, off, os.SEEK_SET)
            if os.read(fd, mib).strip(b"\0"):
                return False
    finally:
        os.close(fd)
    return True


def secure_erase(disk: dict, method: str, progress) -> None:
    dev = f"/dev/{disk['name']}"
    if method == "nvme-format":
        _run(["nvme", "format", dev, "--ses=1", "--force"], timeout=3600)
    elif method.startswith("ata-secure-erase"):
        pw = "itad"
        _run(["hdparm", "--user-master", "u", "--security-set-pass", pw, dev], timeout=60)
        flag = "--security-erase-enhanced" if method.endswith("enhanced") else "--security-erase"
        try:
            _run(["hdparm", "--user-master", "u", flag, pw, dev], timeout=6 * 3600)
        except WipeError:
            sh(["hdparm", "--user-master", "u", "--security-disable", pw, dev], timeout=60)
            raise
    else:
        overwrite(dev, int(disk["size_bytes"]), progress)


def _wait_node(node: str, seconds: float = 15) -> None:
    end = time.monotonic() + seconds
    while not os.path.exists(node):
        if time.monotonic() > end:
            raise WipeError(f"brak urządzenia {node} po utworzeniu partycji")
        time.sleep(0.5)


def wipe_disk(disk: dict, mode: str, fs: str, label: str, progress) -> dict:
    if disk.get("mounted"):
        raise WipeError(f"{disk['name']}: partycja zamontowana ({', '.join(disk['mounted'])}), nie formatuję")
    out = {"name": disk["name"], "model": disk.get("model"), "serial": disk.get("serial"), "size_gb": disk.get("size_gb"),
           "mode": mode, "partitions_before": len(disk.get("partitions", []))}
    t0 = time.monotonic()
    if mode == "secure":
        hd = ""
        if disk["kind"] != "nvme":
            hd = sh(["hdparm", "-I", f"/dev/{disk['name']}"], timeout=30) or ""
        method = secure_method(disk, hd)
        out["method"] = method
        try:
            secure_erase(disk, method, progress)
        except WipeError:
            if method == "overwrite-zeros":
                raise
            out["method"] = f"{method} failed -> overwrite-zeros"
            overwrite(f"/dev/{disk['name']}", int(disk["size_bytes"]), progress)
        out["verified_zero"] = verify_zero(f"/dev/{disk['name']}", int(disk["size_bytes"]))
    else:
        out["method"] = "quick"
    for cmd, stdin in plan_quick(disk, fs, label):
        if cmd[0] in ("partx", "udevadm"):
            sh(cmd, timeout=30)
            if cmd[0] == "partx":
                sh(["partx", "-a", cmd[-1]], timeout=30)       # node missing after -u on some kernels
        elif cmd[0] == "wipefs" and cmd[-1] != f"/dev/{disk['name']}" and not os.path.exists(cmd[-1]):
            continue                                          # partition node not present: the disk wipe covers it
        else:
            if cmd[0].startswith("mkfs"):
                _wait_node(cmd[-1])
            _run(cmd, stdin, timeout=600)
    out.update(filesystem=fs, partitions_after=1, seconds=round(time.monotonic() - t0))
    return out


# ---------- UI ----------

def build(ctx, finish):
    import threading

    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    ui = ctx.ui
    opts = ctx.options
    fs = opts.get("fs", "ntfs")
    label = str(opts.get("label", "DANE"))[:11]
    raw = sh(LSBLK) or "{}"
    disks = targets(raw)
    sizes = {d["name"]: int(d.get("size") or 0) for d in json.loads(raw).get("blockdevices", [])}
    for d in disks:
        d["size_bytes"] = sizes.get(d["name"]) or d["size_gb"] * 10**9

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    if not disks:
        ui.set_hint("Brak dysku wewnętrznego do sformatowania. S = pomiń")
        root.append(Gtk.Label(label="Nie znaleziono dysku wewnętrznego.", xalign=0))
        ui.data["_block_pass"] = "Brak dysku: S = pomiń"
        return root

    ui.data["_block_pass"] = "Najpierw sformatuj dysk albo pomiń test (S)"
    ui.set_hint("Wybierz dyski i tryb, potem „Formatuj”. Pendrive testera nie jest pokazywany.")
    checks = {}
    for d in disks:
        txt = (f"/dev/{d['name']}  {d.get('model') or ''}  {d['size_gb']} GB  {d['kind'].upper()}"
               f"\nS/N {d.get('serial') or '?'}  partycji: {len(d['partitions'])}")
        cb = Gtk.CheckButton(label=txt, active=not d["mounted"], sensitive=not d["mounted"])
        checks[d["name"]] = cb
        root.append(cb)

    mode_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    quick = Gtk.ToggleButton(label="Szybki: jedna partycja + format (sekundy)")
    secure = Gtk.ToggleButton(label="Bezpieczny: kasowanie danych + format (minuty–godziny)")
    secure.set_group(quick)
    (secure if opts.get("mode") == "secure" else quick).set_active(True)
    mode_box.append(quick)
    mode_box.append(secure)
    root.append(mode_box)

    status = Gtk.Label(label=f"System plików po formacie: {fs.upper() if fs != 'none' else 'brak'}, etykieta {label}",
                       xalign=0, wrap=True)
    root.append(status)
    bar = Gtk.ProgressBar(show_text=True, visible=False)
    root.append(bar)
    actions = Gtk.Box(spacing=12)
    go = Gtk.Button(label="Formatuj…")
    go.add_css_class("destructive-action")
    confirm = Gtk.Button(label="Tak, usuń dane", visible=False, sensitive=False)
    confirm.add_css_class("destructive-action")
    cancel = Gtk.Button(label="Anuluj", visible=False)
    actions.append(go)
    actions.append(cancel)
    actions.append(confirm)
    root.append(actions)

    def chosen():
        return [d for d in disks if checks[d["name"]].get_active()]

    def ask(_b):
        sel = chosen()
        if not sel:
            status.set_text("Zaznacz co najmniej jeden dysk.")
            return
        mode = "bezpieczny" if secure.get_active() else "szybki"
        status.set_text("UWAGA: wszystkie dane na " + ", ".join(f"/dev/{d['name']} ({d.get('model') or ''})" for d in sel)
                        + f" zostaną trwale usunięte (tryb {mode}). Przycisk aktywny za 3 s.")
        go.set_visible(False)
        cancel.set_visible(True)
        confirm.set_visible(True)
        confirm.set_sensitive(False)
        GLib.timeout_add_seconds(3, lambda: (confirm.set_sensitive(True), False)[1])

    def back(_b):
        go.set_visible(True)
        cancel.set_visible(False)
        confirm.set_visible(False)
        status.set_text("Anulowano. Nic nie zostało zmienione.")

    def run_it(_b):
        sel = chosen()
        mode = "secure" if secure.get_active() else "quick"
        for w in [confirm, cancel, quick, secure, *checks.values()]:
            w.set_sensitive(False)
        bar.set_visible(True)
        ui.data["_block_pass"] = "Trwa formatowanie, poczekaj"

        def prog(name):
            return lambda frac: GLib.idle_add(lambda: (bar.set_fraction(frac), bar.set_text(f"{name}: {frac:.0%}"), False)[-1])

        def work():
            done, errors = [], []
            for d in sel:
                GLib.idle_add(lambda d=d: (status.set_text(f"Formatowanie /dev/{d['name']}…"), False)[1])
                try:
                    done.append(wipe_disk(d, mode, fs, label, prog(d["name"])))
                except Exception as exc:     # report every failure, keep going with the next disk
                    errors.append(f"{d['name']}: {exc}")
            GLib.idle_add(lambda: (finished(done, errors), False)[1])

        threading.Thread(target=work, daemon=True).start()

    def finished(done, errors):
        bar.set_fraction(1.0)
        data = {"disks": done, "mode": "secure" if secure.get_active() else "quick"}
        if errors:
            finish(Result("fail", data, "; ".join(errors)))
        else:
            unverified = [d["name"] for d in done if d.get("verified_zero") is False]
            note = f"Niezweryfikowane zerami (wzór producenta): {', '.join(unverified)}" if unverified else ""
            finish(Result("warn" if unverified else "pass", data, note))

    go.connect("clicked", ask)
    cancel.connect("clicked", back)
    confirm.connect("clicked", run_it)
    return root
