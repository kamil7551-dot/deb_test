"""Visual grading by the operator: a grade A-D for each part (LCD, lid, body, palmrest) with an optional note,
plus yes/no questions from the profile (e.g. keyboard backlight, fingerprint reader).

Profile options (all optional):
  parts:     [{id: lcd, name: "Matryca (LCD)"}, ...]   default: lcd, lid, body, palmrest
  questions: [{id: backlight, text: "Podświetlenie klawiatury działa?"}, ...]   default: none
PASS needs a grade for every part and an answer to every question.
The overall grade suggested on the summary is never better than the worst part (see profiles.suggest_grade).
"""

from __future__ import annotations

ID = "cosmetics"
NAME = "Ocena wizualna"
KIND = "interactive"
CONTRACT = 1
ORDER = 190

PART_GRADES = ["A", "B", "C", "D"]
DEFAULT_PARTS = [
    {"id": "lcd", "name": "Matryca (LCD)"},
    {"id": "lid", "name": "Klapa"},
    {"id": "body", "name": "Kadłubek"},
    {"id": "palmrest", "name": "Palmrest"},
]


def parts(options: dict) -> list[dict]:
    return [p for p in (options.get("parts") or DEFAULT_PARTS) if isinstance(p, dict) and p.get("id")]


def questions(options: dict) -> list[dict]:
    return [q for q in (options.get("questions") or []) if isinstance(q, dict) and q.get("id") and q.get("text")]


def missing(options: dict, data: dict) -> str | None:
    """What still has to be filled in before PASS, or None."""
    g, a = data.get("grades") or {}, data.get("answers") or {}
    todo = [p["name"] for p in parts(options) if g.get(p["id"]) not in PART_GRADES]
    todo += [q["text"] for q in questions(options) if a.get(q["id"]) not in (True, False)]
    return ("Uzupełnij: " + ", ".join(todo)) if todo else None


def build(ctx, finish):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    ui = ctx.ui
    opts = ctx.options
    data = {"grades": {}, "notes": {}, "answers": {}}

    def sync():
        for k, v in data.items():
            if v:
                ui.data[k] = dict(v)
            else:
                ui.data.pop(k, None)
        ui.data["_block_pass"] = missing(opts, data)

    grid = Gtk.Grid(column_spacing=12, row_spacing=10)
    grid.add_css_class("panel")
    row = 0
    for p in parts(opts):
        grid.attach(Gtk.Label(label=p["name"], xalign=0), 0, row, 1, 1)
        box = Gtk.Box(spacing=6)
        first = None
        for g in PART_GRADES:
            b = Gtk.ToggleButton(label=g)
            b.set_size_request(64, -1)
            if first:
                b.set_group(first)
            else:
                first = b
            b.connect("toggled", lambda btn, pid=p["id"], g=g: btn.get_active() and (data["grades"].__setitem__(pid, g), sync()))
            box.append(b)
        grid.attach(box, 1, row, 1, 1)
        note = Gtk.Entry(placeholder_text="uwagi (opcjonalnie)", width_chars=22, hexpand=True)

        def on_note(e, pid=p["id"]):
            txt = e.get_text().strip()
            if txt:
                data["notes"][pid] = txt
            else:
                data["notes"].pop(pid, None)
            sync()

        note.connect("changed", on_note)
        grid.attach(note, 2, row, 1, 1)
        row += 1

    for q in questions(opts):
        grid.attach(Gtk.Label(label=q["text"], xalign=0, wrap=True), 0, row, 1, 1)
        box = Gtk.Box(spacing=6)
        first = None
        for text, val in (("Tak", True), ("Nie", False)):
            b = Gtk.ToggleButton(label=text)
            if first:
                b.set_group(first)
            else:
                first = b
            b.connect("toggled", lambda btn, qid=q["id"], v=val: btn.get_active() and (data["answers"].__setitem__(qid, v), sync()))
            box.append(b)
        grid.attach(box, 1, row, 1, 1)
        row += 1

    sync()
    ui.set_hint("Oceń każdy element (A = bez śladów, D = mocno zniszczony), potem Enter")
    return grid
