"""GPU test: list GPUs and drivers, run a short 3D benchmark (glmark2) on each GPU, operator checks for artefacts.

On hybrid laptops the second GPU is exercised with DRI_PRIME=1. Software rendering (llvmpipe) means no working
3D driver: the result is downgraded to a warning.
Options: duration_s (int, total seconds per GPU, default 20).
"""

from __future__ import annotations

import os
import re
import subprocess
import threading

from tester.core.util import have

ID = "gpu"
NAME = "Karta graficzna (3D)"
KIND = "interactive"
CONTRACT = 1
ORDER = 160

GLMARK = ("glmark2-es2-wayland", "glmark2-wayland", "glmark2-es2", "glmark2")


def parse_glmark(text: str) -> dict:
    out: dict = {}
    for key, pat in (("renderer", r"GL_RENDERER:\s*(.+)"), ("vendor", r"GL_VENDOR:\s*(.+)"),
                     ("gl_version", r"GL_VERSION:\s*(.+)")):
        m = re.search(pat, text)
        if m:
            out[key] = m.group(1).strip()
    m = re.search(r"glmark2 Score:\s*(\d+)", text)
    if m:
        out["score"] = int(m.group(1))
    fps = [int(x) for x in re.findall(r"FPS:\s*(\d+)", text)]
    if fps:
        out["fps_min"], out["fps_max"] = min(fps), max(fps)
    out["software"] = bool(re.search(r"llvmpipe|softpipe|swrast", out.get("renderer", ""), re.I))
    return out


def build(ctx, finish):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    from tester.modules.sysinfo import gpu_list

    ui = ctx.ui
    gpus = gpu_list()
    ui.data["gpus"] = gpus
    duration = int(ctx.options.get("duration_s", 20))
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for g in gpus:
        root.append(Gtk.Label(label=f"• {g['name']}  (sterownik: {g.get('driver') or 'brak'})", xalign=0))
    status = Gtk.Label(xalign=0, wrap=True)
    status.add_css_class("hint")
    root.append(status)
    if not gpus:
        ui.note = "Nie wykryto karty graficznej"
    binary = next((b for b in GLMARK if have(b)), None)
    if not binary:
        status.set_text("Brak programu glmark2: oceń obraz na podstawie testu ekranu.")
        ui.note = ui.note or "Test 3D niedostępny"
        return root

    runs = [{"env": {}, "label": gpus[0]["name"] if gpus else "GPU"}]
    if len(gpus) > 1:
        runs.append({"env": {"DRI_PRIME": "1"}, "label": gpus[1]["name"]})
    results: list[dict] = []
    half = max(4, duration // 2)
    procs: list[subprocess.Popen] = []

    def work():
        for r in runs:
            GLib.idle_add(status.set_text, f"Test 3D: {r['label']} ({duration} s)… obserwuj obraz.")
            env = dict(os.environ, **r["env"])
            cmd = [binary, "--fullscreen", "-b", f"build:use-vbo=true:duration={half}",
                   "-b", f"shading:shading=phong:duration={half}"]
            try:
                p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                procs.append(p)
                out, _ = p.communicate(timeout=duration + 60)
            except (OSError, subprocess.TimeoutExpired) as exc:
                out = f"error: {exc}"
            res = parse_glmark(out)
            res["gpu"] = r["label"]
            res["exit_code"] = procs[-1].returncode if procs else None
            results.append(res)
        GLib.idle_add(finished)

    def finished():
        ui.data["runs"] = results
        lines, warn = [], False
        for r in results:
            if r.get("score") is None:
                lines.append(f"{r['gpu']}: test 3D nie zadziałał")
                warn = True
            else:
                lines.append(f"{r['gpu']}: {r.get('renderer', '?')} · wynik {r['score']} · FPS {r.get('fps_min')}–{r.get('fps_max')}")
                if r["software"]:
                    lines.append("  Uwaga: renderowanie programowe (brak akceleracji 3D)")
                    warn = True
        status.set_text("\n".join(lines) + "\nCzy były artefakty, zawieszenia lub migotanie? Enter = OK, Esc = usterka")
        if warn:
            ui.data["_pass_as_warn"] = True
            ui.note = "; ".join(line.strip() for line in lines if "Uwaga" in line or "nie zadziałał" in line)
        return False

    def kill():
        for p in procs:
            if p.poll() is None:
                p.kill()

    ui.on_cleanup(kill)
    threading.Thread(target=work, daemon=True).start()
    return root
