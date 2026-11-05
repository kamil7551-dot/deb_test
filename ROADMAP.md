# ROADMAP.md

Status of the ITAD hardware tester. Update this file in the same PR as the work: tick boxes, add Changelog lines,
move answered questions to Decisions.

## Phase 1 — Skeleton (not started)

- [ ] `live-build` config for Debian 13, hybrid ISO (UEFI + Secure Boot via shim, legacy via ISOLINUX)
- [ ] Kiosk session that starts the tester app full-screen
- [ ] CI: build the ISO in GitHub Actions, publish it as an artifact, fail if ISO > 1 GB
- [ ] `TESTERCFG` config partition: station config, profiles, token, offline queue
- [ ] Engine: profile loader, pipeline runner (auto modules in parallel), manual selection, report builder
- [ ] UI: start screen, mode choice, test list, summary with grade picker
- [ ] Auto modules: `sysinfo`, `bios`, `memory` (dmidecode + SPD), `tpm`, `storage`, `battery`, `network`
- [ ] Report schema v1 (`schemas/report-v1.json`), saved locally
- [ ] Unit tests with recorded fixtures

## Phase 2 — Interactive tests (not started)

- [ ] `display`, `keyboard`, `touchpad`, `camera`, `gpu`, `audio`
- [ ] Keyboard layouts for common business laptops (Dell, HP, Lenovo)

## Phase 3 — ProSpectra upload (not started; needs ProSpectra v31 API)

- [ ] Clock sync gate before upload
- [ ] `GET /units/{serial}` lookup on start screen
- [ ] `POST /tests` with retries and idempotent `run_id`
- [ ] Offline queue with `failed/` handling and flush at boot
- [ ] Placeholder serial detection + scan prompt

## Phase 4 — Field testing and 1.0 (not started)

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
| 2026-11-05 | Repo docs (`AGENTS.md`, `INSTRUCTIONS.md`, `ROADMAP.md`) in English; operator UI in Polish |

## Changelog

- 2026-11-05 — Repository started with `AGENTS.md`, `INSTRUCTIONS.md`, `ROADMAP.md` (planning only, no code yet).
