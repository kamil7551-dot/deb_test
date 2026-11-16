"""Audio test: left/right speaker tone, microphone record + playback (ALSA, no sound server needed).

The first analog (non-HDMI) card is chosen by default; the operator can switch the output/input device.
"""

from __future__ import annotations

import re
import tempfile
import threading
from pathlib import Path

from tester.core.util import run as sh

ID = "audio"
NAME = "Dźwięk i mikrofon"
KIND = "interactive"
CONTRACT = 1
ORDER = 150


def parse_devices(text: str) -> list[dict]:
    """Parse `aplay -l` / `arecord -l`."""
    out = []
    for m in re.finditer(r"^card (\d+): (\S+) \[([^\]]*)\], device (\d+): ([^\[]*)\[([^\]]*)\]", text, re.M):
        card, cid, cname, dev, _dname, dlabel = m.groups()
        out.append({"card": int(card), "device": int(dev), "id": cid, "name": f"{cname} – {dlabel}".strip(),
                    "hdmi": bool(re.search(r"HDMI|DisplayPort|DP", dlabel + cid, re.I))})
    out.sort(key=lambda d: (d["hdmi"], d["card"], d["device"]))
    return out


def unmute(card: int) -> None:
    for ctl in ("Master", "Speaker", "Headphone", "PCM", "Front"):
        sh(["amixer", "-q", "-c", str(card), "sset", ctl, "85%", "unmute"], timeout=5)
    for ctl in ("Capture", "Internal Mic Boost", "Mic Boost"):
        sh(["amixer", "-q", "-c", str(card), "sset", ctl, "70%", "cap"], timeout=5)


def build(ctx, finish):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    ui = ctx.ui
    outs = parse_devices(sh(["aplay", "-l"]) or "")
    ins = parse_devices(sh(["arecord", "-l"]) or "")
    ui.data.update({"outputs": [d["name"] for d in outs], "inputs": [d["name"] for d in ins]})
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    if not outs:
        root.append(Gtk.Label(label="Nie wykryto karty dźwiękowej."))
        ui.note = "Brak karty dźwiękowej"
        return root
    ui.set_hint("Odtwórz dźwięk z lewego i prawego głośnika, potem nagraj i odsłuchaj mikrofon. Enter = OK, Esc = usterka")
    out_drop = Gtk.DropDown.new_from_strings([d["name"] for d in outs])
    in_drop = Gtk.DropDown.new_from_strings([d["name"] for d in ins] or ["(brak mikrofonu)"])
    grid = Gtk.Grid(column_spacing=12, row_spacing=10)
    grid.attach(Gtk.Label(label="Wyjście:", xalign=0), 0, 0, 1, 1)
    grid.attach(out_drop, 1, 0, 1, 1)
    grid.attach(Gtk.Label(label="Wejście:", xalign=0), 0, 1, 1, 1)
    grid.attach(in_drop, 1, 1, 1, 1)
    root.append(grid)
    status = Gtk.Label(xalign=0)
    status.add_css_class("hint")
    btns = Gtk.Box(spacing=12)
    root.append(btns)
    root.append(status)
    busy = threading.Lock()
    done = set()

    def odev():
        d = outs[out_drop.get_selected()]
        return d, f"plughw:{d['card']},{d['device']}"

    def bg(text, fn, key):
        if not busy.acquire(blocking=False):
            return
        status.set_text(text)

        def work():
            try:
                fn()
            finally:
                busy.release()
                GLib.idle_add(lambda: (status.set_text("Gotowe"), done.add(key), ui.data.update(tested=sorted(done)), False)[-1])

        threading.Thread(target=work, daemon=True).start()

    def tone(channel: int):
        d, dev = odev()
        unmute(d["card"])
        sh(["speaker-test", "-D", dev, "-c", "2", "-s", str(channel), "-t", "sine", "-f", "660", "-l", "1"], timeout=15)

    def mic():
        if not ins:
            return
        d = ins[in_drop.get_selected()]
        unmute(d["card"])
        wav = Path(tempfile.gettempdir()) / "itad-mic.wav"
        sh(["arecord", "-q", "-D", f"plughw:{d['card']},{d['device']}", "-d", "4", "-f", "S16_LE", "-r", "44100",
            "-c", "2", str(wav)], timeout=10)
        _, dev = odev()
        sh(["aplay", "-q", "-D", dev, str(wav)], timeout=10)

    for text, fn, key in (("Lewy głośnik", lambda: tone(1), "left"), ("Prawy głośnik", lambda: tone(2), "right"),
                          ("Nagraj 4 s i odtwórz", mic, "mic")):
        b = Gtk.Button(label=text)
        b.connect("clicked", lambda _b, t=text, f=fn, k=key: bg(f"{t}…", f, k))
        btns.append(b)
    return root
