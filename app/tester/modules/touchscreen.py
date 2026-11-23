"""Touchscreen test (only when sysinfo found a touchscreen): touch or swipe over every cell of a grid.

Passes by itself when every cell was touched. Esc leaves the grid; then Enter = OK, Esc = defect
(e.g. a dead zone that never reacts).
"""

from __future__ import annotations

from tester.core.module import Result

ID = "touchscreen"
NAME = "Ekran dotykowy"
KIND = "interactive"
CONTRACT = 1
ORDER = 115
COLS, ROWS = 6, 4


def available(ctx) -> str | None:
    if not ctx.machine.get("touchscreen"):
        return "Brak ekranu dotykowego"
    return None


def build(ctx, finish):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, GLib, Gtk

    ui = ctx.ui
    hit: set[tuple[int, int]] = set()
    area = Gtk.DrawingArea(focusable=True, can_focus=True)

    def draw(_a, cr, w, h):
        cw, ch = w / COLS, h / ROWS
        for c in range(COLS):
            for r in range(ROWS):
                cr.set_source_rgb(0.1, 0.6, 0.2) if (c, r) in hit else cr.set_source_rgb(0.15, 0.15, 0.18)
                cr.rectangle(c * cw + 2, r * ch + 2, cw - 4, ch - 4)
                cr.fill()

    area.set_draw_func(draw)

    def touch(x: float, y: float):
        w, h = area.get_width(), area.get_height()
        if w <= 0 or h <= 0:
            return
        cell = (min(COLS - 1, int(x * COLS / w)), min(ROWS - 1, int(y * ROWS / h)))
        if cell not in hit:
            hit.add(cell)
            ui.data["cells_touched"] = len(hit)
            area.queue_draw()
            if len(hit) == COLS * ROWS:
                ui.restore()
                ui.finish(Result("pass", {"cells_touched": len(hit), "cells": COLS * ROWS}))

    drag = Gtk.GestureDrag()
    start = {}

    def begin(_g, x, y):
        start.update(x=x, y=y)
        touch(x, y)

    drag.connect("drag-begin", begin)
    drag.connect("drag-update", lambda _g, dx, dy: touch(start["x"] + dx, start["y"] + dy))
    area.add_controller(drag)

    keys = Gtk.EventControllerKey()

    def on_key(_c, keyval, _code, _state):
        if (Gdk.keyval_name(keyval) or "").lower() == "escape":
            ui.restore()
            ui.data["cells"] = COLS * ROWS
            ui.set_hint(f"Dotknięto {len(hit)} z {COLS * ROWS} pól. Enter = OK, Esc = usterka dotyku")
            return True
        return False

    keys.connect("key-pressed", on_key)
    area.add_controller(keys)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, valign=Gtk.Align.CENTER)
    info = Gtk.Label(label="Dotknij palcem każdego pola (można przeciągać). Esc = przerwij.", wrap=True)
    info.add_css_class("hint")
    box.append(info)
    again = Gtk.Button(label="Pokaż siatkę ponownie")
    again.connect("clicked", lambda *_: ui.fullscreen(area))
    box.append(again)
    GLib.timeout_add(600, lambda: (ui.fullscreen(area), False)[1])
    return box
