"""Screen test: full-screen colour fields, gradient and grid for dead pixels, banding and backlight bleed.

Space / click / → = next pattern, ← = previous, ↑/↓ = backlight brightness, Esc = stop early.
After the last pattern the operator judges: Enter = OK, Esc = defect.
"""

from __future__ import annotations

from pathlib import Path

from tester.core.util import read_int

ID = "display"
NAME = "Ekran"
KIND = "interactive"
CONTRACT = 1
ORDER = 110

PATTERNS = [
    ("Czerwony", (1, 0, 0)), ("Zielony", (0, 1, 0)), ("Niebieski", (0, 0, 1)), ("Biały", (1, 1, 1)),
    ("Czarny", (0, 0, 0)), ("Szary 50%", (0.5, 0.5, 0.5)), ("Gradient", "gradient"), ("Siatka", "grid"),
]


def available(ctx) -> str | None:
    if ctx.machine.get("chassis") in ("desktop",) and not ctx.machine.get("has_internal_screen"):
        return "Komputer stacjonarny bez wbudowanego ekranu"
    return None


def backlights() -> list[Path]:
    return sorted(Path("/sys/class/backlight").glob("*"))


def set_brightness(delta_pct: int) -> int | None:
    for bl in backlights():
        mx, cur = read_int(bl / "max_brightness"), read_int(bl / "brightness")
        if not mx or cur is None:
            continue
        new = max(int(mx * 0.05), min(mx, cur + mx * delta_pct // 100))
        try:
            (bl / "brightness").write_text(str(new))
        except OSError:
            return None
        return round(new * 100 / mx)
    return None


def build(ctx, finish):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gtk

    ui = ctx.ui
    state = {"i": 0, "seen": 0}

    area = Gtk.DrawingArea(focusable=True, can_focus=True)

    def draw(_a, cr, w, h):
        name, spec = PATTERNS[state["i"]]
        if spec == "gradient":
            for x in range(0, w, 2):
                v = x / max(1, w - 1)
                cr.set_source_rgb(v, v, v)
                cr.rectangle(x, 0, 2, h / 2)
                cr.fill()
                cr.set_source_rgb(v, 0, 0) if x % 6 < 2 else cr.set_source_rgb(0, v, 0) if x % 6 < 4 else cr.set_source_rgb(0, 0, v)
                cr.rectangle(x, h / 2, 2, h / 2)
                cr.fill()
        elif spec == "grid":
            cr.set_source_rgb(0, 0, 0)
            cr.paint()
            cr.set_source_rgb(1, 1, 1)
            cr.set_line_width(1)
            step = 40
            for x in range(0, w + 1, step):
                cr.move_to(x + 0.5, 0)
                cr.line_to(x + 0.5, h)
            for y in range(0, h + 1, step):
                cr.move_to(0, y + 0.5)
                cr.line_to(w, y + 0.5)
            cr.stroke()
            cr.rectangle(0.5, 0.5, w - 1, h - 1)
            cr.stroke()
        else:
            cr.set_source_rgb(*spec)
            cr.paint()

    area.set_draw_func(draw)

    def step(d: int):
        state["i"] += d
        if state["i"] >= len(PATTERNS):
            state["i"] = len(PATTERNS) - 1
            done_patterns()
            return
        state["i"] = max(0, state["i"])
        state["seen"] = max(state["seen"], state["i"] + 1)
        area.queue_draw()

    def done_patterns():
        ui.data["patterns_shown"] = state["seen"]
        ui.restore()
        ui.set_hint("Czy ekran był bez martwych pikseli, przebarwień i plam? Enter = OK, Esc = usterka")
        info.set_text("Test wzorów zakończony. Możesz go powtórzyć klawiszem R.")

    keys = Gtk.EventControllerKey()

    def on_key(_c, keyval, _code, _state):
        n = (Gdk.keyval_name(keyval) or "").lower()
        if n in ("space", "right", "return", "kp_enter", "page_down"):
            step(1)
        elif n in ("left", "backspace", "page_up"):
            step(-1)
        elif n == "up":
            ui.data["brightness_changed"] = set_brightness(20) is not None
        elif n == "down":
            ui.data["brightness_changed"] = set_brightness(-20) is not None
        elif n == "escape":
            done_patterns()
        return True

    keys.connect("key-pressed", on_key)
    area.add_controller(keys)
    click = Gtk.GestureClick(button=0)
    click.connect("pressed", lambda g, *_: step(-1 if g.get_current_button() == 3 else 1))
    area.add_controller(click)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, valign=Gtk.Align.CENTER)
    info = Gtk.Label(label="Za chwilę ekran wypełnią kolory. Spacja / klik = następny wzór, ← poprzedni, "
                           "↑/↓ jasność podświetlenia, Esc = przerwij.", wrap=True)
    info.add_css_class("hint")
    box.append(info)
    again = Gtk.Button(label="Pokaż wzory ponownie")
    again.connect("clicked", lambda *_: start())
    box.append(again)

    def start():
        state["i"] = 0
        state["seen"] = 1
        ui.set_hint("Patrz na ekran: szukaj martwych pikseli, plam i przebarwień")
        ui.fullscreen(area)

    from gi.repository import GLib

    GLib.timeout_add(600, lambda: (start(), False)[1])
    return box

