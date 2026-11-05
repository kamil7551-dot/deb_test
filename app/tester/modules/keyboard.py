"""Keyboard test: an on-screen map lights up every key pressed. PASS automatically when all main keys were hit.

Keys are identified by evdev codes (GTK hardware keycode - 8), so the result does not depend on the layout.
Optional keys (numpad, right Ctrl, Super, Menu, navigation block) are shown but not required.
Shortcuts are disabled during this test: use the on-screen buttons (mouse/touchpad) to mark a defect or skip.
Option: required_extra: [list of evdev codes] to require more keys for a given model line.
"""

from __future__ import annotations

from tester.core.module import Result

ID = "keyboard"
NAME = "Klawiatura"
KIND = "interactive"
CONTRACT = 1
ORDER = 120

# (label, evdev code, width class, required)
ROWS = [
    [("Esc", 1, "", True)] + [(f"F{i}", 58 + i, "", True) for i in range(1, 11)] + [("F11", 87, "", True),
     ("F12", 88, "", True), ("PrtSc", 99, "", False), ("Ins", 110, "", False), ("Del", 111, "", True)],
    [("`", 41, "", True)] + [(str(i % 10), i + 1, "", True) for i in range(1, 11)] + [("-", 12, "", True),
     ("=", 13, "", True), ("Backspace", 14, "wide", True), ("Home", 102, "", False)],
    [("Tab", 15, "wide", True)] + [(c, 16 + i, "", True) for i, c in enumerate("QWERTYUIOP")] + [
     ("[", 26, "", True), ("]", 27, "", True), ("\\", 43, "", True), ("PgUp", 104, "", False)],
    [("Caps", 58, "wide", True)] + [(c, 30 + i, "", True) for i, c in enumerate("ASDFGHJKL")] + [
     (";", 39, "", True), ("'", 40, "", True), ("Enter", 28, "wide", True), ("PgDn", 109, "", False)],
    [("Shift", 42, "wide", True), ("\\ ISO", 86, "", False)] + [(c, 44 + i, "", True) for i, c in enumerate("ZXCVBNM")]
    + [(",", 51, "", True), (".", 52, "", True), ("/", 53, "", True), ("Shift", 54, "wide", True),
       ("↑", 103, "", True), ("End", 107, "", False)],
    [("Ctrl", 29, "wide", True), ("Win", 125, "", False), ("Alt", 56, "", True), ("Spacja", 57, "space", True),
     ("AltGr", 100, "", True), ("Menu", 127, "", False), ("Ctrl", 97, "", False), ("←", 105, "", True),
     ("↓", 108, "", True), ("→", 106, "", True)],
]
NUMPAD = [("Num", 69), ("/", 98), ("*", 55), ("-", 74), ("7", 71), ("8", 72), ("9", 73), ("+", 78), ("4", 75),
          ("5", 76), ("6", 77), ("1", 79), ("2", 80), ("3", 81), ("0", 82), (",", 83), ("Enter", 96)]


def required_codes(extra: list[int] | None = None) -> set[int]:
    codes = {code for row in ROWS for _, code, _, req in row if req}
    return codes | set(extra or [])


def build(ctx, finish):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    ui = ctx.ui
    ui.capture_keys = True
    required = required_codes(ctx.options.get("required_extra"))
    hit: set[int] = set()
    other: set[int] = set()
    cells: dict[int, list] = {}

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, focusable=True, can_focus=True)
    ui.set_hint("Naciśnij po kolei wszystkie klawisze. Usterkę zaznacz przyciskiem na dole (myszą/touchpadem).")
    for row in ROWS:
        rb = Gtk.Box(spacing=6)
        for text, code, width, req in row:
            lb = Gtk.Label(label=text)
            lb.add_css_class("key")
            if width:
                lb.add_css_class(width)
            if not req:
                lb.add_css_class("optional")
            cells.setdefault(code, []).append(lb)
            rb.append(lb)
        root.append(rb)
    np = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=17)
    for text, code in NUMPAD:
        lb = Gtk.Label(label=text)
        lb.add_css_class("key")
        lb.add_css_class("optional")
        cells.setdefault(code, []).append(lb)
        np.append(lb)
    root.append(Gtk.Label(label="Klawiatura numeryczna (opcjonalnie):", xalign=0))
    root.append(np)
    progress = Gtk.Label(xalign=0)
    progress.add_css_class("big")
    root.append(progress)
    extra_lbl = Gtk.Label(xalign=0, wrap=True)
    extra_lbl.add_css_class("muted")
    root.append(extra_lbl)

    def refresh():
        missing = required - hit
        progress.set_text(f"Wciśnięte: {len(required & hit)} / {len(required)}")
        if other:
            extra_lbl.set_text("Inne klawisze (np. multimedialne): " + ", ".join(str(c) for c in sorted(other)))
        ui.data.update({"pressed": len(required & hit), "required": len(required),
                        "missing_codes": sorted(missing), "extra_codes": sorted(other)})
        if not missing:
            ui.note = ""
            GLib.timeout_add(500, lambda: (finish(Result("pass", {k: v for k, v in ui.data.items()})), False)[1])
        else:
            ui.note = f"Nie wciśnięto {len(missing)} klawiszy"

    keys = Gtk.EventControllerKey()
    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def on_key(_c, _keyval, keycode, _state):
        code = keycode - 8
        if code in cells:
            for lb in cells[code]:
                lb.add_css_class("hit")
            if code not in hit:
                hit.add(code)
                refresh()
        else:
            other.add(code)
            refresh()
        return True

    keys.connect("key-pressed", on_key)
    ui.window.add_controller(keys)
    ui.on_cleanup(lambda: ui.window.remove_controller(keys))
    refresh()
    return root

