"""Entry point.

    python3 -m tester                      # GTK4 kiosk UI (default on the live system)
    python3 -m tester --headless [...]     # run automatic modules only, print/save the JSON report
    python3 -m tester --list               # list modules and profiles
    python3 -m tester --flush              # send queued reports to ProSpectra
    python3 -m tester --setup              # boot-time setup (config partition, token, Wi-Fi)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from tester import __version__


def _logging(verbose: bool) -> None:
    from tester.core import config

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    logdir = config.DATA_DIR / "logs"
    try:
        logdir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logdir / "tester.log"))
    except OSError:
        pass
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


def headless(args) -> int:
    from tester.core import config, report
    from tester.core.module import discover
    from tester.core.profiles import Profile, TestEntry, load_profiles, pick_default
    from tester.core.runner import Session, run_auto
    from tester.upload import queue
    from tester.upload.prospectra import Client

    cfg = config.load_config()
    mods = discover()
    profiles = load_profiles()
    if args.modules:
        profile = Profile("cli", "CLI", tests=[TestEntry(m) for m in args.modules.split(",")])
    else:
        profile = profiles.get(args.profile) if args.profile else pick_default(profiles, None, cfg.default_profile)
        profile = profile or Profile("all", "All", tests=[TestEntry(m) for m in mods])
    selected = [mods[t.id] for t in profile.tests if t.id in mods and mods[t.id].kind == "auto"]
    session = Session(profile=profile, modules=selected)
    if args.serial:
        session.serial_source = "scanned"
    run_auto(session, lambda mid, r: print(f"[{r.status.upper():4}] {mid} {r.note}", file=sys.stderr)).join()
    session.serial = args.serial or session.machine.get("serial")
    rep = report.build_report(session, cfg, grade=args.grade)
    text = report.dumps(rep)
    if args.out:
        Path(args.out).write_text(text)
    else:
        print(text)
    if args.send:
        from tester.core.util import is_placeholder_serial

        if is_placeholder_serial(rep.get("serial")):
            print("Serial is a placeholder; pass --serial from the sticker. Not sent.", file=sys.stderr)
            return 2
        queue.enqueue(rep)
        for o in queue.flush(Client(cfg.base_url, cfg.token, cfg.station_slug)):
            print(f"{o.run_id}: {o.state} {o.message} {o.url or ''}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="itad-tester", description="ITAD hardware tester")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--headless", action="store_true", help="run automatic modules without UI")
    p.add_argument("--profile", help="profile id (headless)")
    p.add_argument("--modules", help="comma-separated module ids (headless, overrides profile)")
    p.add_argument("--serial", help="serial number to use (e.g. scanned from the sticker)")
    p.add_argument("--grade", choices=["A", "B", "C", "D", "scrap"])
    p.add_argument("--out", help="write report JSON to this file")
    p.add_argument("--send", action="store_true", help="queue and send the report to ProSpectra")
    p.add_argument("--list", action="store_true", help="list modules and profiles")
    p.add_argument("--flush", action="store_true", help="send queued reports")
    p.add_argument("--setup", action="store_true", help="boot-time setup (root)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    if args.setup:
        from tester import setup
        return setup.main()
    _logging(args.verbose)
    if args.list:
        from tester.core.module import discover
        from tester.core.profiles import load_profiles

        for m in discover().values():
            print(f"module  {m.id:10} {m.kind:12} {m.name}")
        for pr in load_profiles().values():
            print(f"profile {pr.id:14} {pr.mode:9} {', '.join(t.id for t in pr.tests)}")
        return 0
    if args.flush:
        from tester.core import config
        from tester.upload import queue
        from tester.upload.prospectra import Client

        cfg = config.load_config()
        for o in queue.flush(Client(cfg.base_url, cfg.token, cfg.station_slug)):
            print(f"{o.run_id}: {o.state} {o.message}")
        return 0
    if args.headless:
        return headless(args)
    from tester.ui.app import main as ui_main
    return ui_main()


if __name__ == "__main__":
    sys.exit(main())
