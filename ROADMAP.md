# ROADMAP.md

Status of the ITAD hardware tester. Update this file in the same PR as the work: tick boxes, add Changelog lines,
move answered questions to Decisions.

## Phase 1 — Skeleton (done in code, ISO build in CI; not yet tested on hardware)

- [x] `live-build` config for Debian 13, hybrid ISO (UEFI + Secure Boot via shim, legacy via ISOLINUX)
- [x] Kiosk session that starts the tester app full-screen
- [x] CI: build the ISO in GitHub Actions, publish it as an artifact, fail if ISO > 1 GB
- [x] `TESTERCFG` config partition: station config, profiles, token, offline queue
- [x] Create `TESTERCFG` on the tester's own boot stick from the start screen (F9, with confirmation), so one stick
      written with Rufus/Etcher is enough; CI boots a dd-ed USB stick and lets the tester create it
- [ ] Export button on the summary/start screen (copy reports as JSON/CSV/PDF to the stick or another USB drive)
- [x] Engine: profile loader, pipeline runner (auto modules in parallel), manual selection, report builder
- [x] UI: start screen, mode choice, test list, summary with grade picker
- [x] Auto modules: `sysinfo`, `bios`, `memory` (dmidecode + SPD), `tpm`, `storage`, `battery`, `network`
- [x] Report schema v1 built and saved locally (queue/sent/failed)
- [ ] JSON Schema file `schemas/report-v1.json`
- [x] Unit tests with recorded fixtures

## Phase 2 — Interactive tests (done in code; verified only on a virtual screen)

- [x] `display`, `keyboard`, `touchpad`, `camera`, `gpu`, `audio`
- [ ] Keyboard layouts for common business laptops (Dell, HP, Lenovo)

## Phase 3 — ProSpectra upload (done in code; ProSpectra API live since 2026-11-16, v31.2)

- [x] Clock sync gate before upload
- [x] `GET /units/{serial}` lookup on start screen
- [x] `POST /tests` with retries and idempotent `run_id`
- [x] Offline queue with `failed/` handling; flush from the start screen ("Wyślij zaległe") and after each run
- [x] Automatic flush ~15 s after the UI starts (queued reports from earlier runs)
- [x] Placeholder serial detection + scan prompt
- [ ] Start-screen status from `GET /ping` ("token OK" / "token odrzucony" / "brak połączenia"), not only token-file presence
- [ ] Treat `404` from `POST /tests` as retryable (keep in queue), remove it from `FINAL_ERRORS`
- [ ] `is_placeholder_serial`: also reject one character repeated (e.g. `11111111`), as ProSpectra does
- [ ] Optional: ASCII-fold `station_slug` (ProSpectra accepts Unicode in `run_id` since v31.2, so this is only hygiene)
- [ ] First end-to-end run against production: create a token in ProSpectra, send one report, check Magazyn → Testy

## Phase 4 — Field testing and 1.0 (next)

- [x] First green ISO build in CI: 602 MiB (2026-11-05, Debian trixie, kernel 6.12)
- [x] Automated QEMU boot test in CI (BIOS, UEFI, UEFI + Secure Boot) reaches the start screen in ~30 s, reads SMBIOS serial, mounts TESTERCFG
- [ ] Test on at least: one Dell, one HP, one Lenovo laptop, one desktop, one legacy-BIOS-only machine
- [ ] Fix issues, measure boot time and full-run time, tag v1.0

## Later (ideas, not committed)

- Disk wipe with certificate (`PUT /tests/{id}/report`), TPM clear via PPI, vendor BIOS tools, label/QR printing,
  English UI.

## Open questions

- Sample report from the currently used tester program, to keep fields the business relies on.
- Network at the test station: wired DHCP, Wi-Fi (company profile assumed), proxy?
- Hardware mix: share of desktops, Macs (Apple Silicon Macs cannot boot this image).
- Will disk wiping be required? It affects the security design.

## Decisions

| Date | Decision |
| --- | --- |
| 2026-11-05 | Base: Debian 13 live via `live-build`, one hybrid ISO, stock kernel (Secure Boot), target ≤ 1 GB |
| 2026-11-05 | App: Python 3 + GTK4 kiosk; tests are plug-in modules; YAML profiles; pipeline or manual mode |
| 2026-11-05 | Results go to ProSpectra `POST /api/ext/v1/tests` by serial number, per-station `tests:write` token; no separate handshake/project selection |
| 2026-11-05 | Kiosk = cage (Wayland) running the GTK4 app as root; camera via GStreamer appsink; GPU test via glmark2-es2-wayland; audio via ALSA tools |
| 2026-11-05 | Config storage = partition labelled TESTERCFG or a TESTERCFG/ folder (Ventoy/second stick), bind-mounted at /var/lib/tester |
| 2026-11-16 | One stick: config storage is created by the tester in the free space of its own boot stick (operator-confirmed, never on internal disks) instead of requiring a second stick or Linux tooling |
| 2026-11-05 | Repo docs (`AGENTS.md`, `INSTRUCTIONS.md`, `ROADMAP.md`) in English; operator UI in Polish |

## Changelog

- 2026-11-16 — ProSpectra v31.2 live: `/ping`, `/units/{serial}`, `/tests`, `/tests/{id}/report`; Unicode `run_id`,
  `device` up to 128 KB. Docs updated; follow-up tasks added to Phase 3.

- 2026-11-16 — v0.2.0: tester creates the `TESTERCFG` partition (or folder, Rufus ISO mode) on its own boot stick
  (start screen F9 / `--make-storage`); image adds `fdisk` + `dosfstools` (~0.5 MB); CI boot tests on a USB stick.

- 2026-11-16 — CI publishes GitHub Release vX.Y.Z with the ISO when a new __version__ lands on main; v0.1.0 released.
- 2026-11-05 — CI: QEMU boot test (BIOS / UEFI / Secure Boot) with OCR of the start screen; first ISO 602 MiB.
- 2026-11-05 — v0.1.0: engine, 13 modules (7 auto, 6 interactive), GTK4 UI, ProSpectra client + offline queue,
  live-build config, build/USB scripts, CI (tests + ISO). Not yet booted on real hardware.
- 2026-11-05 — Repository started with `AGENTS.md`, `INSTRUCTIONS.md`, `ROADMAP.md` (planning only, no code yet).
