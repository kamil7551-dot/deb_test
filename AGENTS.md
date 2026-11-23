# AGENTS.md

Guide for AI agents, CI pipelines and human contributors working on this repository.
Read this file first, then `INSTRUCTIONS.md` (what we are building) and `ROADMAP.md` (where we are).
To build the ISO yourself (VM or container, no CI needed) follow `BUILD.md`.

## Project in one paragraph

`deb_test` is a bootable Debian-based live USB used in an ITAD (IT asset disposition) workflow to test used
laptops and desktops. It boots on both UEFI (Secure Boot on) and legacy BIOS from one USB stick, collects
hardware configuration (BIOS, CPU, RAM incl. SPD, disks, battery, TPM, GPU), runs automatic and interactive
pass/fail tests (screen, keyboard, touchpad, camera, GPU, audio), and sends one JSON result per machine to the
company system **ProSpectra** (`https://prospectra-ai.app/api/ext/v1`), matched by the device serial number.
Target image size: under 1 GB, so it loads fast from USB 2.0.

## Mandatory rules for every change

1. **Keep the docs in sync, in the same commit/PR as the change.**
   - Change behaviour, requirements, formats or the API contract → update `INSTRUCTIONS.md`.
   - Finish, add, drop or re-order work → update `ROADMAP.md` (tick boxes, add a line to its Changelog).
   - Change repo layout, tooling, conventions or these rules → update `AGENTS.md`.
   - Change how the image is built (packages, scripts, build host) → update `BUILD.md`. The owner builds on a VM
     from the repo and BUILD.md alone (GitHub Actions minutes are limited), so it must always work as written.
   A PR that changes behaviour without touching the docs is incomplete.
2. **Never commit secrets.** ProSpectra tokens (`psk_live_…`), Wi-Fi passwords and station configs live only on
   the USB config partition or in CI secrets. Use `*.example` files with placeholders in the repo.
3. **Do not break the module contract or the report schema silently.** Changing either means bumping its
   version (see `INSTRUCTIONS.md`) and noting it in the `ROADMAP.md` Changelog.
4. **Stay inside the size budget.** Any package added to the image must be justified in the PR description with
   its approximate compressed size. The build must report the final ISO size.
5. **Stock Debian kernel only.** No out-of-tree or self-built kernel modules: they break Secure Boot.
6. **Never use insecure TLS** (`curl -k`, `verify=False`). Fix the clock instead (see `INSTRUCTIONS.md`).
7. **Nothing destructive by default.** Any module that writes to the tested machine (disk wipe, TPM clear,
   BIOS settings) must be opt-in per profile and require explicit operator confirmation on screen.

## Conventions

| Topic | Rule |
| --- | --- |
| Operator UI language | Polish (strings kept in one translation file so English can be added) |
| Code, comments, commit messages, identifiers | English |
| Docs in this repo | English, so any agent or pipeline can read them |
| Base system | Debian 13 "trixie", built with `live-build` |
| App language | Python 3 (Debian packaged version), GTK4 UI; no pip downloads at boot |
| Config format | YAML for profiles and station config; JSON for reports |
| Time | Always UTC, ISO 8601 with `Z` |
| Branches | `main` is releasable; work on feature branches and open a PR |
| Module imports | Interactive modules import GTK/GStreamer inside `build()` only, so headless mode and CI work without GTK |
| Shell helper | Import `tester.core.util.run` as `sh` in modules (a module's own `run()` would shadow it) |
| Commits | Small, imperative subject line ("Add TPM module"), body says why |

## Repository layout

```
AGENTS.md              this file
INSTRUCTIONS.md        product spec: what the tester must do
ROADMAP.md             phases, status, changelog, open questions
BUILD.md               build the ISO on a Debian VM or in a container, step by step
app/tester/            the tester application (copied to /opt/tester in the image)
  __main__.py            entry point: UI, --headless, --list, --flush, --setup, --make-storage
  setup.py               boot-time setup: TESTERCFG mount, token, Wi-Fi profiles
  bootstick.py           create TESTERCFG on the tester's own boot stick (partition or folder), --make-storage
  label.py               device label: fill a template from the report, render ZPL/TSPL/text, print over raw TCP
  core/                  engine: module contract, profiles, runner, report builder, config, helpers
  modules/               one file per test module (auto: sysinfo, bios, memory, tpm, storage, battery,
                         network; interactive: display, touchscreen, keyboard, touchpad, camera, audio, gpu,
                         cosmetics, wipe [destructive disk format/erase, opt-in per profile])
  assets/                small data files used by modules (left.wav / right.wav voice prompts, espeak-ng)
  upload/                ProSpectra client (urllib) + offline queue
  ui/                    GTK4 kiosk UI (app.py) and style.css
app/tests/             pytest unit tests + fixtures/ (recorded command output from real tools)
profiles/              built-in test profiles (*.yaml)
config.example/        TESTERCFG folder template (no secrets; includes label.yaml print template)
live-build/            Debian live-build config: auto/, package-lists, hooks, systemd units
tools/vm-build.sh      one-shot on a Debian trixie VM: build deps, unit tests, then build-iso.sh
tools/build-iso.sh     builds out/itad-tester-<version>.iso (root, Debian trixie), enforces the 1 GB budget
tools/make-usb.sh      writes the ISO to a stick and adds the TESTERCFG partition
tools/qemu-boot-test.sh boots the ISO in QEMU (bios|uefi|secureboot; MEDIA=cdrom|usb|usb-raw) and OCRs the start screen
.github/workflows/     CI: lint + tests + headless smoke run, ISO build in a privileged trixie container, QEMU boot tests, GitHub Release when `__version__` changes on main
```

Run locally without hardware: `cd app && TESTER_DATA_DIR=/tmp/td TESTER_PROFILES=../profiles python3 -m tester --headless --profile quick`.
The UI runs on any Linux desktop with GTK4 + PyGObject (`python3 -m tester`); it needs `python3-gi-cairo`.

## How to work on it

- Unit tests must run on any Linux machine without the target hardware: feed modules recorded command output
  (`dmidecode`, `decode-dimms`, `smartctl`, `tpm2_getcap`) from `app/tests/fixtures/`.
- When you add a hardware module, also add at least one fixture from a real machine and a test for it.
- The ISO is built in CI (GitHub Actions, Debian container, privileged) and published as a workflow artifact.
- Before claiming a change works on hardware, say which machine it was tested on; otherwise say it is untested.

## Where the external contract lives

The ProSpectra integration contract is summarised in `INSTRUCTIONS.md` § ProSpectra integration. ProSpectra is a
separate system maintained elsewhere; if its API changes, update that section and the upload client together.
