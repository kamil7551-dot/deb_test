"""Touchpad test: draw over the whole surface, left and right click, two-finger scroll.

Indicators turn green as each function is detected; the operator confirms with Enter (or marks a defect with Esc).
"""

from __future__ import annotations


ID = "touchpad"
NAME = "Touchpad"
KIND = "interactive"
CONTRACT = 1
ORDER = 130


def available(ctx) -> str | None:
    if ctx.machine.get("chassis") not in (None, "laptop"):
        return "Komputer bez touchpada"
    return None


def build(ctx, finish):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    ui = ctx.ui
    ui.set_hint("Rysuj palcem po całym touchpadzie, kliknij lewym i prawym przyciskiem, przewiń dwoma palcami.")
    strokes: list[list[tuple[float, float]]] = []
    seen = {"left": False, "right": False, "scroll": False, "move": False}

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, vexpand=True)
    ind_box = Gtk.Box(spacing=10)
    inds = {}
    for key, text in (("move", "Ruch kursora"), ("left", "Lewy przycisk"), ("right", "Prawy przycisk"),
                      ("scroll", "Przewijanie")):
        lb = Gtk.Label(label=text)
        lb.add_css_class("indicator")
        inds[key] = lb
        ind_box.append(lb)
    clear = Gtk.Button(label="Wyczyść")
    ind_box.append(clear)
    root.append(ind_box)

    area = Gtk.DrawingArea(vexpand=True, hexpand=True)
    frame = Gtk.Frame()
    frame.set_child(area)
    root.append(frame)

    def mark(key):
        if not seen[key]:
            seen[key] = True
            inds[key].add_css_class("hit")
        ui.data.update({k: v for k, v in seen.items()})
        missing = [k for k, v in seen.items() if not v]
        ui.note = "" if not missing else "Nie wykryto: " + ", ".join(missing)

    def draw(_a, cr, w, h):
        cr.set_source_rgb(0.1, 0.12, 0.15)
        cr.paint()
        cr.set_source_rgb(0.3, 0.8, 0.45)
        cr.set_line_width(4)
        for s in strokes:
            if len(s) < 2:
                continue
            cr.move_to(*s[0])
            for p in s[1:]:
                cr.line_to(*p)
            cr.stroke()

    area.set_draw_func(draw)

    motion = Gtk.EventControllerMotion()

    def on_motion(_c, x, y):
        if not strokes:
            strokes.append([])
        strokes[-1].append((x, y))
        if len(strokes[-1]) > 2000:
            strokes.append([(x, y)])
        mark("move")
        area.queue_draw()

    motion.connect("motion", on_motion)
    motion.connect("leave", lambda *_: strokes.append([]))
    area.add_controller(motion)

    click = Gtk.GestureClick(button=0)

    def on_click(g, *_):
        b = g.get_current_button()
        mark("left" if b == 1 else "right" if b == 3 else "move")
        strokes.append([])

    click.connect("pressed", on_click)
    area.add_controller(click)

    scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
    scroll.connect("scroll", lambda *_: (mark("scroll"), True)[1])
    area.add_controller(scroll)

    def do_clear(*_):
        strokes.clear()
        area.queue_draw()

    clear.connect("clicked", do_clear)
    ui.note = "Nie wykryto: move, left, right, scroll"
    return root

