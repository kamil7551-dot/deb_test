"""GTK4 kiosk UI: start screen → (manual selection) → test run → summary/send.

Keyboard: Enter = PASS / start, Esc = FAIL, S = skip, R = repeat current test.
Interactive modules get a UiHelper as ctx.ui (see helper docs below).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from tester import __version__  # noqa: E402
from tester.core import config  # noqa: E402
from tester.core.module import ModuleInfo, Result, discover  # noqa: E402
from tester.core.profiles import GRADES, Profile, TestEntry, load_profiles, pick_default, suggest_grade  # noqa: E402
from tester.core.report import build_report, make_summary  # noqa: E402
from tester.core.runner import Session, overall_result, run_auto, run_module  # noqa: E402
from tester.core.util import is_placeholder_serial  # noqa: E402
from tester.core.util import run as sh  # noqa: E402
from tester.upload import queue  # noqa: E402
from tester.upload.prospectra import Client  # noqa: E402

log = logging.getLogger("tester.ui")

STATUS_TEXT = {"pass": "✓ OK", "fail": "✗ BŁĄD", "warn": "! UWAGA", "skip": "– pominięty", "run": "… trwa",
               "wait": "· czeka"}
GRADE_LABEL = {"A": "A", "B": "B", "C": "C", "D": "D", "scrap": "Złom"}


def label(text: str = "", css: str | None = None, xalign: float = 0.0, wrap: bool = True) -> Gtk.Label:
    lb = Gtk.Label(label=text, xalign=xalign, wrap=wrap)
    if css:
        for c in css.split():
            lb.add_css_class(c)
    return lb


def button(text: str, css: str | None = None, cb=None) -> Gtk.Button:
    b = Gtk.Button(label=text)
    if css:
        for c in css.split():
            b.add_css_class(c)
    if cb:
        b.connect("clicked", lambda *_: cb())
    return b


def scrolled(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    """Wrap a page so its minimum height never exceeds small (1366x768) screens."""
    sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, hexpand=True)
    sw.set_child(child)
    return sw


def set_status(lb: Gtk.Label, status: str) -> None:
    for s in ("pass", "fail", "warn", "skip", "run"):
        lb.remove_css_class(f"st-{s}")
    lb.add_css_class(f"st-{'run' if status == 'wait' else status}")
    lb.set_text(STATUS_TEXT.get(status, status))


def in_thread(fn, done=None) -> None:
    """Run fn() in a worker thread, then done(result) on the GTK main loop."""

    def work():
        try:
            res = fn()
        except Exception as exc:  # surface errors in the UI rather than killing the thread silently
            log.exception("background task failed")
            res = exc
        if done:
            GLib.idle_add(lambda: (done(res), False)[1])

    threading.Thread(target=work, daemon=True).start()


class UiHelper:
    """What an interactive module may use through ctx.ui.

    data/note        → copied into the Result when the operator presses PASS/FAIL/skip
    set_hint(text)   → instruction line under the test title
    capture_keys     → True disables Enter/Esc/S/R shortcuts (keyboard test needs every key)
    fullscreen(w)    → show widget w over the whole window; restore() goes back
    on_cleanup(cb)   → called when the test ends (stop pipelines, processes)
    finish(result)   → end the test programmatically
    """

    def __init__(self, window: "MainWindow"):
        self.window = window
        self.data: dict = {}
        self.note: str = ""
        self.capture_keys = False
        self._cleanup: list = []
        self._finish = None

    def set_hint(self, text: str) -> None:
        self.window.run_hint.set_text(text)

    def fullscreen(self, widget: Gtk.Widget) -> None:
        self.window.show_fullscreen(widget)

    def restore(self) -> None:
        self.window.hide_fullscreen()

    def on_cleanup(self, cb) -> None:
        self._cleanup.append(cb)

    def finish(self, result: Result) -> None:
        if self._finish:
            self._finish(result)

    def cleanup(self) -> None:
        for cb in self._cleanup:
            try:
                cb()
            except Exception:
                log.exception("cleanup failed")
        self._cleanup.clear()


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="Tester ITAD")
        self.set_default_size(1024, 640)   # real size comes from fullscreen (1366x768 laptops must fit)
        self.cfg = config.load_config()
        self.mods = discover()
        self.profiles = load_profiles()
        self.client = Client(self.cfg.base_url, self.cfg.token, self.cfg.station_slug)
        self.session: Session | None = None
        self.sysinfo_result: dict | None = None
        self.machine: dict = {}
        self.stage = "start"
        self.helper: UiHelper | None = None
        self.current: ModuleInfo | None = None
        self.queue_interactive: list[ModuleInfo] = []
        self.auto_thread: threading.Thread | None = None
        self.grade: str | None = None

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vhomogeneous=False,
                               hhomogeneous=False)
        self.set_child(self.stack)
        self.stack.add_named(scrolled(self._build_start()), "start")
        self.stack.add_named(scrolled(self._build_select()), "select")
        self.stack.add_named(self._build_run(), "run")
        self.stack.add_named(scrolled(self._build_summary()), "summary")
        self.full_holder = Gtk.Box()
        self.stack.add_named(self.full_holder, "full")

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)   # before focused buttons see Enter
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.show_stage("start")
        self.detect_machine()
        GLib.timeout_add_seconds(5, self._refresh_status)
        GLib.timeout_add_seconds(15, self._auto_flush)

    def _auto_flush(self) -> bool:
        """Send reports left in the queue by earlier runs (e.g. tested offline yesterday)."""
        if self.client.configured and queue.counts()["queued"]:
            in_thread(lambda: queue.flush(self.client), lambda _res: self._refresh_status())
        return False

    # ---------- start screen ----------
    def _build_start(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=40, margin_bottom=30,
                       margin_start=60, margin_end=60)
        head = Gtk.Box(spacing=12)
        head.append(label("Tester ITAD", "title"))
        head.append(label(f"v{__version__} · {self.cfg.station}", "muted", xalign=1.0))
        head.get_last_child().set_hexpand(True)
        root.append(head)

        self.machine_lbl = label("Wykrywanie sprzętu…", "subtitle")
        root.append(self.machine_lbl)

        panel = Gtk.Grid(column_spacing=16, row_spacing=10)
        panel.add_css_class("panel")
        panel.attach(label("Numer seryjny", "section"), 0, 0, 1, 1)
        self.serial_entry = Gtk.Entry(hexpand=True, placeholder_text="Zeskanuj numer seryjny z naklejki")
        self.serial_entry.connect("activate", lambda *_: self.start_default())
        self._lookup_src = 0
        self.serial_entry.connect("changed", lambda *_: self._schedule_lookup())
        panel.attach(self.serial_entry, 1, 0, 1, 1)
        self.serial_hint = label("", "muted")
        panel.attach(self.serial_hint, 1, 1, 1, 1)
        self.operator_entry = Gtk.Entry(hexpand=True, placeholder_text="Imię i nazwisko / inicjały")
        self.operator_entry.connect("activate", lambda *_: self.start_default())
        last_op = config.DATA_DIR / "operator"
        if last_op.exists():
            self.operator_entry.set_text(last_op.read_text().strip())
        if self.cfg.operator_prompt:
            panel.attach(label("Operator", "section"), 0, 2, 1, 1)
            panel.attach(self.operator_entry, 1, 2, 1, 1)
        self.unit_lbl = label("", "hint")
        panel.attach(self.unit_lbl, 1, 3, 1, 1)
        root.append(panel)

        root.append(label("Wybierz profil testu (Enter = podświetlony)", "section"))
        self.profile_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                                       column_spacing=12, row_spacing=12, homogeneous=False)
        root.append(self.profile_box)

        self.status_lbl = label("", "muted")
        self.status_lbl.set_vexpand(True)
        self.status_lbl.set_valign(Gtk.Align.END)
        root.append(self.status_lbl)

        foot = Gtk.Box(spacing=12)
        self.flush_btn = button("Wyślij zaległe", None, self.flush_queue)
        foot.append(self.flush_btn)
        spacer = Gtk.Box(hexpand=True)
        foot.append(spacer)
        foot.append(button("Uruchom ponownie", None, lambda: sh(["systemctl", "reboot"])))
        foot.append(button("Wyłącz", None, lambda: sh(["systemctl", "poweroff"])))
        root.append(foot)
        return root

    def _fill_profiles(self) -> None:
        while (child := self.profile_box.get_first_child()) is not None:
            self.profile_box.remove(child)
        self.default_profile = pick_default(self.profiles, self.machine.get("chassis"), self.cfg.default_profile)
        for p in self.profiles.values():
            css = "profile suggested-action" if p is self.default_profile else "profile"
            self.profile_box.append(button(p.name, css, lambda p=p: self.start(p)))
        self.profile_box.append(button("Tryb ręczny…", "profile", lambda: self.show_stage("select")))

    def detect_machine(self) -> None:
        def work():
            s = Session(profile=Profile("detect", "detect"), modules=[self.mods["sysinfo"]])
            run_module(s, self.mods["sysinfo"])
            return s

        def done(s):
            if isinstance(s, Exception):
                self.machine_lbl.set_text(f"Błąd wykrywania sprzętu: {s}")
                self._fill_profiles()
                return
            self.machine = s.machine
            self.sysinfo_result = s.results.get("sysinfo")
            d = (self.sysinfo_result or {}).get("data", {})
            parts = [d.get("manufacturer"), d.get("model"), d.get("cpu"),
                     f"{d.get('ram_visible_gb')} GB RAM" if d.get("ram_visible_gb") else None]
            self.machine_lbl.set_text(" · ".join(p for p in parts if p) or "Nieznany komputer")
            serial = d.get("serial")
            if serial and not d.get("serial_placeholder"):
                self.serial_entry.set_text(serial)
                self.serial_hint.set_text("Odczytano z BIOS")
            else:
                self.serial_hint.set_text(f"BIOS podaje „{serial or 'pusty'}”: zeskanuj numer z naklejki")
                self.serial_entry.grab_focus()
            self._fill_profiles()
            self.lookup_unit()

        in_thread(work, done)

    def _schedule_lookup(self) -> None:
        if self._lookup_src:
            GLib.source_remove(self._lookup_src)
        self.unit_lbl.set_text("")
        self._lookup_src = GLib.timeout_add(900, self._lookup_now)

    def _lookup_now(self) -> bool:
        self._lookup_src = 0
        self.lookup_unit()
        return False

    def lookup_unit(self) -> None:
        serial = self.serial_entry.get_text().strip()
        if not serial or is_placeholder_serial(serial) or not self.client.configured:
            return

        def done(resp):
            if isinstance(resp, Exception) or resp.status != 200 or not resp.body:
                return
            if not resp.body.get("found"):
                self.unit_lbl.set_text("W ProSpectrze: brak sztuki z tym S/N (podepnie się po PZ)")
                return
            u = resp.body.get("unit") or {}
            txt = f"W ProSpectrze: {(u.get('product') or {}).get('name', '')}"
            lt = u.get("last_test")
            if lt:
                txt += f" · ostatni test {lt.get('result')} {lt.get('grade') or ''} {str(lt.get('tested_at', ''))[:10]}"
            self.unit_lbl.set_text(txt)

        in_thread(lambda: self.client.lookup_unit(serial), done)

    def _refresh_status(self) -> bool:
        def work():
            net = (sh(["nmcli", "-t", "-f", "STATE", "general"], timeout=5) or "").strip()
            return net, queue.clock_ok(), queue.counts()

        def done(res):
            if isinstance(res, Exception):
                return
            net, clock, cnt = res
            bits = [
                f"Sieć: {'połączono' if net.startswith('connected') else 'brak'}",
                f"Zegar: {'OK' if clock else 'niezsynchronizowany'}",
                f"ProSpectra: {'token OK' if self.client.configured else 'brak tokenu'}",
                f"Konfiguracja: {'pendrive' if self.cfg.persistent else 'brak partycji TESTERCFG (dane tylko w RAM)'}",
                f"Kolejka: {cnt['queued']} · wysłane: {cnt['sent']} · odrzucone: {cnt['failed']}",
            ]
            self.status_lbl.set_text("   ·   ".join(bits))
            self.flush_btn.set_label(f"Wyślij zaległe ({cnt['queued']})")
            self.flush_btn.set_sensitive(cnt["queued"] > 0)

        in_thread(work, done)
        return True

    def flush_queue(self) -> None:
        self.flush_btn.set_sensitive(False)
        self.flush_btn.set_label("Wysyłanie…")

        def done(outs):
            if isinstance(outs, Exception):
                self.status_lbl.set_text(f"Błąd wysyłki: {outs}")
            else:
                sent = sum(o.state == "sent" for o in outs)
                self.status_lbl.set_text(f"Wysłano {sent} z {len(outs)}. " +
                                         "; ".join(f"{o.run_id}: {o.message}" for o in outs if o.state != "sent"))
            self._refresh_status()

        in_thread(lambda: queue.flush(self.client), done)

    # ---------- manual selection ----------
    def _build_select(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=40, margin_bottom=30,
                       margin_start=60, margin_end=60)
        root.append(label("Tryb ręczny: wybierz testy", "title"))
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, column_spacing=24)
        self.checks: dict[str, Gtk.CheckButton] = {}
        for m in self.mods.values():
            cb = Gtk.CheckButton(label=f"{m.name}  ({'automatyczny' if m.kind == 'auto' else 'interaktywny'})")
            cb.set_active(True)
            self.checks[m.id] = cb
            grid.append(cb)
        root.append(grid)
        bar = Gtk.Box(spacing=12, vexpand=True, valign=Gtk.Align.END)
        bar.append(button("Wstecz", None, lambda: self.show_stage("start")))
        bar.append(button("Zaznacz wszystkie", None, lambda: [c.set_active(True) for c in self.checks.values()]))
        bar.append(button("Odznacz wszystkie", None, lambda: [c.set_active(False) for c in self.checks.values()]))
        bar.append(Gtk.Box(hexpand=True))
        bar.append(button("Start", "primary", self.start_manual))
        root.append(bar)
        return root

    def start_manual(self) -> None:
        ids = [mid for mid, cb in self.checks.items() if cb.get_active()]
        if "sysinfo" not in ids:
            ids.insert(0, "sysinfo")        # identity is always needed for the report
        base = self.default_profile
        tests = [TestEntry(mid, base.options_for(mid) if base else {}) for mid in ids]
        self.start(Profile("manual", "Ręczny", mode="manual", tests=tests,
                           grade_rules=base.grade_rules if base else []))

    # ---------- run ----------
    def _build_run(self) -> Gtk.Widget:
        root = Gtk.Box()
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=360)
        side.add_css_class("sidebar")
        side.append(label("Testy", "section", wrap=False))
        side.set_margin_top(20)
        self.run_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        side.append(self.run_list)
        root.append(side)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, margin_top=24,
                       margin_bottom=20, margin_start=32, margin_end=32)
        self.run_title = label("", "title")
        self.run_hint = label("", "hint")
        main.append(self.run_title)
        main.append(self.run_hint)
        self.test_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, hexpand=True)
        main.append(scrolled(self.test_holder))
        bar = Gtk.Box(spacing=12)
        self.btn_fail = button("Usterka (Esc)", "fail", lambda: self.operator_verdict("fail"))
        self.btn_skip = button("Pomiń (S)", None, lambda: self.operator_verdict("skip"))
        self.btn_redo = button("Powtórz (R)", None, self.repeat_current)
        self.btn_pass = button("OK (Enter)", "pass", lambda: self.operator_verdict("pass"))
        for b in (self.btn_fail, self.btn_skip, self.btn_redo):
            bar.append(b)
        bar.append(Gtk.Box(hexpand=True))
        bar.append(self.btn_pass)
        self.run_bar = bar
        main.append(bar)
        root.append(main)
        return root

    def start_default(self) -> None:
        if self.stage == "start" and getattr(self, "default_profile", None):
            self.start(self.default_profile)

    def start(self, profile: Profile) -> None:
        serial = self.serial_entry.get_text().strip()
        if is_placeholder_serial(serial):
            self.serial_hint.set_text("Najpierw zeskanuj poprawny numer seryjny z naklejki")
            self.serial_entry.grab_focus()
            return
        operator = self.operator_entry.get_text().strip()
        if operator:
            try:
                (config.DATA_DIR / "operator").write_text(operator)
            except OSError:
                pass
        mods = [self.mods[t.id] for t in profile.tests if t.id in self.mods]
        missing = [t.id for t in profile.tests if t.id not in self.mods]
        if missing:
            log.warning("profile %s references unknown modules: %s", profile.id, missing)
        s = Session(profile=profile, modules=mods, machine=dict(self.machine))
        s.serial = serial
        dmi_serial = (self.sysinfo_result or {}).get("data", {}).get("serial")
        s.serial_source = "dmi" if serial == dmi_serial else "scanned"
        s.operator = operator or None
        self.session = s
        self.row_status: dict[str, Gtk.Label] = {}
        while (row := self.run_list.get_first_child()) is not None:
            self.run_list.remove(row)
        for m in mods:
            box = Gtk.Box(spacing=8)
            box.append(label(m.name, wrap=False))
            st = label("", xalign=1.0, wrap=False)
            st.set_hexpand(True)
            set_status(st, "wait")
            box.append(st)
            self.run_list.append(box)
            self.row_status[m.id] = st
        self.auto_thread = run_auto(s, lambda mid, r: GLib.idle_add(self._auto_done, mid, r))
        for m in s.auto_modules:
            set_status(self.row_status[m.id], "run")
        self.queue_interactive = list(s.interactive_modules)
        self.show_stage("run")
        self.next_interactive()

    def _auto_done(self, mid: str, r: Result) -> bool:
        if mid in self.row_status:
            set_status(self.row_status[mid], r.status)
        if self.current is None and not self.queue_interactive:
            self._maybe_summary()
        return False

    def next_interactive(self) -> None:
        if self.helper:
            self.helper.cleanup()
        while (c := self.test_holder.get_first_child()) is not None:
            self.test_holder.remove(c)
        if not self.queue_interactive:
            self.current = None
            self.run_title.set_text("Testy automatyczne")
            self.run_hint.set_text("Czekam na zakończenie testów automatycznych…")
            self.run_bar.set_visible(False)
            self._maybe_summary()
            return
        self.run_bar.set_visible(True)
        m = self.queue_interactive.pop(0)
        self.current = m
        set_status(self.row_status[m.id], "run")
        self.run_title.set_text(m.name)
        self.run_hint.set_text("")
        self.helper = UiHelper(self)
        self.helper._finish = lambda res, m=m: self._finish_interactive(m, res)
        ctx = self.session.context(m, ui=self.helper)
        try:
            reason = m.mod.available(ctx) if hasattr(m.mod, "available") else None
            if reason:
                self._finish_interactive(m, Result("skip", note=str(reason)))
                return
            widget = m.mod.build(ctx, self.helper.finish)
        except Exception as exc:
            log.exception("module %s build failed", m.id)
            widget = label(f"Błąd modułu: {exc}", "hint")
            self.helper.note = f"Błąd modułu: {exc}"
        self.test_holder.append(widget)
        cap = self.helper.capture_keys
        self.btn_pass.set_label("OK" if cap else "OK (Enter)")
        self.btn_fail.set_label("Usterka" if cap else "Usterka (Esc)")
        self.btn_skip.set_label("Pomiń" if cap else "Pomiń (S)")
        self.btn_redo.set_label("Powtórz" if cap else "Powtórz (R)")
        self._started = GLib.get_monotonic_time()

    def _finish_interactive(self, m: ModuleInfo, res: Result) -> None:
        if self.current is not m:
            return
        if self.helper:
            self.helper.restore()
        seconds = (GLib.get_monotonic_time() - getattr(self, "_started", 0)) / 1e6
        self.session.record(m.id, res, seconds)
        set_status(self.row_status[m.id], res.status)
        self.next_interactive()

    def operator_verdict(self, status: str) -> None:
        if not self.current or not self.helper:
            return
        note = self.helper.note
        if status == "skip" and not note:
            note = "Pominięty przez operatora"
        # a module can downgrade an operator "OK" to "warn" (e.g. GPU without acceleration)
        st = "warn" if status == "pass" and self.helper.data.get("_pass_as_warn") else status
        data = {k: v for k, v in self.helper.data.items() if not k.startswith("_")}
        self.helper.finish(Result(st, data, note))

    def repeat_current(self) -> None:
        if self.current:
            self.queue_interactive.insert(0, self.current)
            self.current = None
            self.next_interactive()

    def show_fullscreen(self, widget: Gtk.Widget) -> None:
        while (c := self.full_holder.get_first_child()) is not None:
            self.full_holder.remove(c)
        widget.set_hexpand(True)
        widget.set_vexpand(True)
        self.full_holder.append(widget)
        self.stack.set_visible_child_name("full")
        widget.grab_focus()

    def hide_fullscreen(self) -> None:
        if self.stack.get_visible_child_name() == "full":
            self.stack.set_visible_child_name(self.stage)
            while (c := self.full_holder.get_first_child()) is not None:
                self.full_holder.remove(c)

    def _maybe_summary(self) -> None:
        if self.stage != "run" or self.current is not None or self.queue_interactive:
            return
        if self.auto_thread and self.auto_thread.is_alive():
            return
        self.show_summary()

    # ---------- summary ----------
    def _build_summary(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=30, margin_bottom=24,
                       margin_start=50, margin_end=50)
        self.sum_title = label("Podsumowanie", "title")
        root.append(self.sum_title)
        self.sum_machine = label("", "subtitle")
        root.append(self.sum_machine)
        scroller = Gtk.ScrolledWindow(vexpand=True, min_content_height=200)
        self.sum_grid = Gtk.Grid(column_spacing=24, row_spacing=6)
        self.sum_grid.add_css_class("panel")
        scroller.set_child(self.sum_grid)
        root.append(scroller)

        gl = Gtk.Box(spacing=10)
        gl.append(label("Klasa:", "big", wrap=False))
        self.grade_btns: dict[str, Gtk.ToggleButton] = {}
        first = None
        for g in GRADES:
            b = Gtk.ToggleButton(label=GRADE_LABEL[g])
            b.add_css_class("grade")
            if first:
                b.set_group(first)
            else:
                first = b
            b.connect("toggled", lambda btn, g=g: btn.get_active() and setattr(self, "grade", g))
            self.grade_btns[g] = b
            gl.append(b)
        self.grade_reason = label("", "muted")
        gl.append(self.grade_reason)
        root.append(gl)

        rl = Gtk.Box(spacing=10)
        rl.append(label("Wynik:", "big", wrap=False))
        self.result_drop = Gtk.DropDown.new_from_strings(["pass", "fail", "partial"])
        rl.append(self.result_drop)
        rl.append(label("Uwagi:", "big", wrap=False))
        self.summary_entry = Gtk.Entry(hexpand=True)
        rl.append(self.summary_entry)
        root.append(rl)

        self.send_lbl = label("", "hint")
        root.append(self.send_lbl)
        bar = Gtk.Box(spacing=12)
        self.btn_back_tests = button("Wróć do testów", None, self.back_to_tests)
        bar.append(self.btn_back_tests)
        bar.append(Gtk.Box(hexpand=True))
        self.btn_new = button("Nowy test", None, self.new_test)
        bar.append(self.btn_new)
        bar.append(button("Wyłącz komputer", None, lambda: sh(["systemctl", "poweroff"])))
        self.btn_send = button("Zapisz i wyślij (Enter)", "primary", self.save_and_send)
        bar.append(self.btn_send)
        root.append(bar)
        return root

    def show_summary(self) -> None:
        s = self.session
        while (c := self.sum_grid.get_first_child()) is not None:
            self.sum_grid.remove(c)
        row = 0
        for m in s.modules:
            r = s.results.get(m.id, {"status": "skip", "note": "nie uruchomiono"})
            st = label("", wrap=False)
            set_status(st, r["status"])
            self.sum_grid.attach(label(m.name, wrap=False), 0, row, 1, 1)
            self.sum_grid.attach(st, 1, row, 1, 1)
            self.sum_grid.attach(label(r.get("note") or "", "muted"), 2, row, 1, 1)
            row += 1
        d = s.results.get("sysinfo", {}).get("data", {})
        self.sum_machine.set_text(f"{d.get('manufacturer') or ''} {d.get('model') or ''} · S/N {s.serial}")
        grade, reasons = suggest_grade(s.profile, s.results)
        self.grade = grade
        self.grade_btns[grade].set_active(True)
        self.grade_reason.set_text("Sugestia: " + ("; ".join(reasons) if reasons else "brak zastrzeżeń"))
        self.result_drop.set_selected(["pass", "fail", "partial"].index(overall_result(s.results)))
        self.summary_entry.set_text(make_summary(s.results))
        self.send_lbl.set_text("")
        self.btn_send.set_sensitive(True)
        self.btn_back_tests.set_sensitive(True)
        self.show_stage("summary")

    def back_to_tests(self) -> None:
        self.queue_interactive = [m for m in self.session.interactive_modules]
        for m in self.queue_interactive:
            self.session.results.pop(m.id, None)
            set_status(self.row_status[m.id], "wait")
        self.show_stage("run")
        self.next_interactive()

    def save_and_send(self) -> None:
        s = self.session
        result = ["pass", "fail", "partial"][self.result_drop.get_selected()]
        rep = build_report(s, self.cfg, grade=self.grade, result=result,
                           summary=self.summary_entry.get_text().strip() or None)
        try:
            path = queue.enqueue(rep)
        except OSError as exc:
            self.send_lbl.set_text(f"Nie można zapisać raportu: {exc}")
            return
        self.btn_send.set_sensitive(False)
        self.btn_back_tests.set_sensitive(False)
        self.send_lbl.set_text(f"Zapisano {path.name}. Wysyłanie do ProSpectry…")
        if s.profile.upload == "off":
            self.send_lbl.set_text(f"Zapisano {path.name} (wysyłka wyłączona w profilu)")
            return

        def done(outs):
            if isinstance(outs, Exception):
                self.send_lbl.set_text(f"Raport w kolejce, błąd wysyłki: {outs}")
                return
            mine = [o for o in outs if o.run_id == rep["run_id"]]
            if not mine:
                self.send_lbl.set_text("Raport w kolejce")
                return
            o = mine[0]
            if o.state == "sent":
                self.send_lbl.set_text(f"✓ {o.message}. {o.url or ''}")
            elif o.state == "failed":
                self.send_lbl.set_text(f"✗ ProSpectra odrzuciła raport: {o.message}")
            else:
                self.send_lbl.set_text(f"Raport w kolejce offline ({o.message}). Wyśle się później.")

        in_thread(lambda: queue.flush(self.client), done)

    def new_test(self) -> None:
        self.session = None
        self.cfg = config.load_config()
        self.show_stage("start")
        self.detect_machine()

    # ---------- common ----------
    def show_stage(self, name: str) -> None:
        self.stage = name
        self.stack.set_visible_child_name(name)
        if name == "start":
            self._refresh_status()

    def _on_key(self, _ctrl, keyval, _code, state) -> bool:
        if self.helper and self.helper.capture_keys and self.stage == "run":
            return False
        if self.stack.get_visible_child_name() == "full":
            return False
        name = (Gdk.keyval_name(keyval) or "").lower()
        if self.stage == "run" and self.current:
            if name in ("return", "kp_enter"):
                self.operator_verdict("pass")
                return True
            if name == "escape":
                self.operator_verdict("fail")
                return True
            if name == "s":
                self.operator_verdict("skip")
                return True
            if name == "r":
                self.repeat_current()
                return True
        if self.stage == "summary" and name in ("return", "kp_enter") and self.btn_send.get_sensitive():
            if not self.summary_entry.has_focus():
                self.save_and_send()
                return True
        if self.stage == "start" and name in ("return", "kp_enter"):
            self.start_default()
            return True
        return False


class TesterApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="pl.itad.tester")

    def do_activate(self):
        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).with_name("style.css")))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", True)
        win = MainWindow(self)
        win.present()
        win.fullscreen()


def main() -> int:
    return TesterApp().run([])
