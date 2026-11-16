"""Camera test: live preview from the built-in webcam (GStreamer v4l2src → appsink → GTK picture).

Options: required (bool, default: true on laptops). With several cameras (e.g. IR), a button switches between them.
"""

from __future__ import annotations

from pathlib import Path

from tester.core.module import Result
from tester.core.util import read_text

ID = "camera"
NAME = "Kamera"
KIND = "interactive"
CONTRACT = 1
ORDER = 140


def capture_devices(root: Path = Path("/sys/class/video4linux")) -> list[dict]:
    """Video nodes that are capture interfaces (index 0 of each physical camera)."""
    out = []
    for dev in sorted(root.glob("video*"), key=lambda p: int(p.name[5:] or 0)):
        if read_text(dev / "index", "0") != "0":
            continue
        out.append({"node": f"/dev/{dev.name}", "name": read_text(dev / "name") or dev.name})
    return out


def build(ctx, finish):
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gdk, GLib, Gst, Gtk

    ui = ctx.ui
    devices = capture_devices()
    ui.data["cameras"] = devices
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, vexpand=True)
    required = ctx.options.get("required", ctx.machine.get("chassis") == "laptop")
    if not devices:
        msg = "Nie wykryto kamery"
        root.append(Gtk.Label(label=msg + (". Enter = pomiń (brak kamery), Esc = usterka." if not required else ".")))
        if required:
            ui.note = "Brak kamery"
            GLib.timeout_add(1500, lambda: (finish(Result("fail", ui.data, "Brak kamery")), False)[1])
        else:
            GLib.timeout_add(1500, lambda: (finish(Result("skip", ui.data, "Brak kamery")), False)[1])
        return root

    Gst.init(None)
    ui.set_hint("Czy obraz z kamery jest wyraźny i bez zakłóceń? Enter = OK, Esc = usterka")
    picture = Gtk.Picture(vexpand=True, hexpand=True, can_shrink=True)
    status = Gtk.Label(label="Uruchamianie kamery…", xalign=0)
    status.add_css_class("muted")
    bar = Gtk.Box(spacing=10)
    switch = Gtk.Button(label="Następna kamera")
    bar.append(status)
    if len(devices) > 1:
        bar.append(switch)
    root.append(picture)
    root.append(bar)
    state = {"i": 0, "pipe": None, "frames": 0}

    def on_sample(sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        s = sample.get_caps().get_structure(0)
        w, h = s.get_value("width"), s.get_value("height")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            data = GLib.Bytes.new(bytes(info.data))
            buf.unmap(info)
            stride = len(data.get_data()) // h if h else w * 4

            def show():
                tex = Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, data, stride)
                picture.set_paintable(tex)
                state["frames"] += 1
                if state["frames"] == 1:
                    status.set_text(f"{devices[state['i']]['name']} · {w}x{h}")
                    ui.data["resolution"] = f"{w}x{h}"
                    ui.data["frames"] = True
                return False

            GLib.idle_add(show)
        return Gst.FlowReturn.OK

    def stop():
        if state["pipe"] is not None:
            state["pipe"].set_state(Gst.State.NULL)
            state["pipe"] = None

    def start():
        stop()
        state["frames"] = 0
        node = devices[state["i"]]["node"]
        desc = (f"v4l2src device={node} ! videoconvert ! videoscale ! "
                "video/x-raw,format=RGBA,width=960,pixel-aspect-ratio=1/1 ! "
                "appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false")
        try:
            pipe = Gst.parse_launch(desc)
        except GLib.Error as exc:
            status.set_text(f"Błąd GStreamer: {exc.message}")
            ui.note = "Błąd uruchomienia kamery"
            return
        pipe.get_by_name("sink").connect("new-sample", on_sample)
        bus = pipe.get_bus()
        bus.add_signal_watch()

        def on_error(_bus, msg):
            err, _dbg = msg.parse_error()
            status.set_text(f"Błąd kamery: {err.message}")
            ui.note = f"Błąd kamery: {err.message}"

        bus.connect("message::error", on_error)
        pipe.set_state(Gst.State.PLAYING)
        state["pipe"] = pipe

    def next_cam(*_):
        state["i"] = (state["i"] + 1) % len(devices)
        start()

    switch.connect("clicked", next_cam)
    ui.on_cleanup(stop)
    start()

    def check_frames():
        if state["frames"] == 0:
            ui.note = "Brak obrazu z kamery"
            status.set_text("Brak obrazu z kamery (zasłonięta, wyłączona w BIOS lub uszkodzona?)")
        return False

    GLib.timeout_add_seconds(6, check_frames)
    return root
