# INSTRUCTIONS.md

Product specification of the ITAD hardware tester. This is the source of truth for *what* to build.
Keep it current: any change in behaviour, formats or contracts is reflected here in the same PR.

## Goals

- **Fast:** from power-on to start screen in about 40–60 s from USB 2.0; a full laptop run in 3–5 minutes.
- **Clear:** one test on screen at a time, big controls, keyboard-only operation possible.
- **Configurable for the future:** tests chosen by profile (automatic pipeline) or manually; new tests are new
  module files, not engine changes; profiles are editable YAML without rebuilding the image.
- **Complete output:** every run produces one report containing the machine **configuration** and the
  **pass/fail result of every test**, sent to ProSpectra or queued offline.

## Boot and image

- One hybrid ISO (isohybrid): UEFI via signed shim + GRUB (works with Secure Boot on), legacy BIOS via ISOLINUX.
  Written to USB with `dd`, Rufus or Ventoy.
- Debian 13 live (`live-build`), stock kernel, non-free firmware included.
- Boot parameter `toram` by default when RAM ≥ 2 GB, so the stick can be removed after boot; otherwise run from USB.
- Size budget: **≤ 1 GB ISO**, target 700–850 MB. Largest item is firmware; NVIDIA GSP firmware may be stripped.
- Kiosk session: the tester app starts full-screen automatically, no desktop environment.
- A second small FAT32 partition on the stick (label `TESTERCFG`) holds station config, profiles, the token and
  the offline queue. Mounted at `/var/lib/tester` (queue) and read into `/etc/tester/` at boot.

## Operator flow

1. Boot → start screen shows model, serial number and network/clock status.
2. Serial check: if the DMI serial is a placeholder (`To be filled by O.E.M.`, `Default string`, `0123456789`,
   `System Serial Number`, empty, all zeros) the operator must scan the serial from the sticker.
3. Optional lookup in ProSpectra (`GET /units/{serial}`) shows product name and last test.
4. Choose mode: **profile (automatic pipeline)** or **manual** (checkbox list of all modules).
5. Automatic modules run in the background in parallel while the operator goes through interactive ones.
6. Summary screen: green/red list, operator picks grade (A, B, C, D, scrap; profile may suggest), optional note.
7. Send → ProSpectra, or queue offline. Show the result and link/ID.

Keyboard: Enter = PASS, Esc = FAIL, S = skip, R = repeat. Mouse/touchpad optional (it may be the broken part).

## Test modules

| id | Kind | What it does | Tools |
| --- | --- | --- | --- |
| `sysinfo` | auto | Manufacturer, model, serial, CPU, NICs and MACs, USB devices | `dmidecode`, `lscpu`, `lspci`, `lsusb` |
| `bios` | auto | BIOS vendor/version/date, board, UUID, asset tag, boot mode, Secure Boot state | `dmidecode -t 0,1,2,3`, efivars, `mokutil` |
| `memory` | auto | Slots and modules; SPD decode; rated vs configured speed; mixed modules warning | `dmidecode -t 17`, `decode-dimms` (`ee1004`, `spd5118`) |
| `tpm` | auto | Present/enabled, version 1.2/2.0, manufacturer, firmware version, owned state | sysfs, `tpm2_getcap` |
| `storage` | auto | Model, serial, capacity, SMART health, power-on hours | `smartctl`, `nvme-cli` |
| `battery` | auto | Design vs full capacity (health %), cycles | `/sys/class/power_supply` |
| `display` | interactive | Full-screen colours, gradient, dead pixels, backlight | GTK4 |
| `keyboard` | interactive | On-screen key map lights up pressed keys; PASS when all pressed | `python-evdev` |
| `touchpad` | interactive | Draw over surface, left/right click, two-finger scroll | libinput/evdev |
| `camera` | interactive | Live preview, operator judges | GStreamer `v4l2src` |
| `gpu` | semi-auto | Detect GPU and driver, 30–60 s 3D render, FPS, operator checks artefacts | `glxinfo`, `glmark2` |
| `audio` | interactive | Left/right speaker, mic record + playback | ALSA / PipeWire |
| `network` | auto | Wi-Fi/LAN adapters present, optional Wi-Fi scan | `iw`, `ethtool` |

Known limits: soldered RAM (LPDDR) has no SPD, and some business laptops lock SMBus, so SPD may be unavailable;
the report then says `spd: unavailable`, not an error. BIOS passwords cannot be read from Linux; detecting whether
one is set needs vendor tools (later module).

### Module contract (version 1)

Each module is one Python file in `app/tester/modules/` exposing:

- `ID` (str, stable, snake_case), `NAME` (Polish display name), `KIND` (`auto` | `interactive` | `semi`),
  `CONTRACT = 1`
- `run(ctx) -> Result` where `Result` has `status` (`pass` | `fail` | `warn` | `skip`), `data` (dict, goes into the
  report), `note` (str, optional).
- `ctx` gives: module options from the profile, a logger, a UI handle (interactive only), and shared machine info.

The engine knows nothing module-specific. Adding a test = new file + entry in a profile.

## Profiles

YAML files in `profiles/` (defaults) and on the `TESTERCFG` partition (overrides, same file name wins).

```yaml
profile: laptop_full
name: Laptop - pełny test
mode: pipeline          # pipeline | manual
tests:
  - sysinfo
  - bios
  - memory: { warn_if_speed_below_rated: true }
  - tpm
  - storage: { smart_required: true }
  - battery: { fail_below_health_pct: 60 }
  - display
  - keyboard: { layout: auto }
  - touchpad
  - camera
  - gpu: { duration_s: 30 }
grade_rules:
  - if: battery.health_pct < 60
    max_grade: B
upload: auto            # auto | ask | off
```

A profile may be auto-selected by chassis type from DMI (laptop vs desktop).

## Station config

`TESTERCFG/config.yaml` (example in `config.example/`, never with real secrets):

```yaml
station: Stanowisko 1
operator_prompt: true
prospectra:
  base_url: https://prospectra-ai.app/api/ext/v1
  token_file: prospectra.env     # contains PROSPECTRA_TOKEN=psk_live_...
wifi:
  nmconnection: firma.nmconnection
```

At boot the token is copied to `/etc/tester/prospectra.env` with mode `0600`.

## Report (schema version 1)

One JSON document per run. It is the body sent to ProSpectra `POST /tests`; tester-specific detail goes inside
`device`, which ProSpectra stores as-is. Schema file: `schemas/report-v1.json` (to be created).

```json
{
  "run_id": "st1-2026-11-05-000123",
  "serial": "5CD1234XYZ",
  "tested_at": "2026-11-05T10:12:00Z",
  "station": "Stanowisko 1",
  "operator": "Jan K.",
  "result": "pass",
  "grade": "A",
  "summary": "Bateria 87%, brak uszkodzeń",
  "device": {
    "manufacturer": "HP", "model": "EliteBook 840 G8", "cpu": "i5-1145G7",
    "ram_gb": 16, "storage": [{ "type": "NVMe", "size_gb": 512, "health_pct": 98 }],
    "screen": "14\" 1920x1080", "gpu": "Intel Iris Xe",
    "battery": { "health_pct": 87, "cycles": 212 },
    "bios_password": false, "computrace": false,
    "tester": {
      "version": "0.1.0", "report_schema": 1, "profile": "laptop_full", "mode": "pipeline",
      "bios": { "vendor": "HP", "version": "T37 Ver. 01.10.00", "date": "2022-05-10", "boot_mode": "uefi", "secure_boot": true },
      "memory": [{ "slot": "DIMM 0", "size_gb": 8, "type": "DDR4", "manufacturer": "Samsung", "part": "M471A1K43DB1-CWE",
                   "serial": "…", "rated_mts": 3200, "configured_mts": 3200, "spd": "ok" }],
      "tpm": { "present": true, "version": "2.0", "manufacturer": "IFX", "firmware": "7.85" }
    }
  },
  "checks": [
    { "name": "Klawiatura", "status": "pass" },
    { "name": "Matryca", "status": "fail", "note": "2 martwe piksele" }
  ]
}
```

- `result`: `pass` | `fail` | `partial`. `grade`: `A` | `B` | `C` | `D` | `scrap`. `checks[].status`: `pass` | `fail` | `skip`
  (a module `warn` is sent as `pass` with a note).
- `run_id` = station slug + date + local counter; unique per run, reused on every retry of the same run.

## ProSpectra integration

ProSpectra is the company CRM/warehouse (Cloudflare Workers + R2). Contract as agreed on 2026-11-05 (ProSpectra v31,
pending deployment):

- Base URL `https://prospectra-ai.app/api/ext/v1`; header `Authorization: Bearer psk_live_…`; `Content-Type: application/json`;
  fixed `User-Agent`, e.g. `itad-tester/0.1 (stanowisko-1)`.
- One token per station or USB stick, scope `tests:write` only; created in ProSpectra → Ustawienia → Integracje.
- `GET /units/{serial}` → `{"found": true, "unit": {…, "last_test": {…}}}` or `{"found": false}` (not an error).
- `POST /tests` with the report → `201` (or `200` for a repeated `run_id`) with `{"test": {"id", "linked", "unit_id", "url"}}`.
  `linked: false` = unit not yet received on PZ; ProSpectra links it automatically later.
- `PUT /tests/{test_id}/report` with `application/pdf` (≤ 10 MB) for a PDF report or wipe certificate (later).
- Errors `{ "error": "…", "code": "…" }`: `400` bad request (incl. placeholder serial), `401`, `403`, `413` (JSON > 256 KB),
  `429` (> 120 req/min per token).

Client rules:

- Sync the clock before any upload (`systemd-timesyncd`/`chronyd -q`); dead CMOS batteries break TLS and `tested_at`.
  No valid time → do not send, keep in queue.
- Retry on network errors, `429`, `5xx` with backoff 2, 4, 8, 16 s, always the same `run_id`.
- Offline queue: `/var/lib/tester/queue/*.json` on the config partition; delete only after `200`/`201`;
  on `400`/`401`/`403` move to `failed/` and show the operator. Queue is flushed at next boot with network.
- Send the serial exactly as read from DMI (or scanned); ProSpectra normalises it.
- `ca-certificates` must be in the image; never disable TLS verification.

## Out of scope for now (possible later modules)

Disk wipe (NIST 800-88) with certificate, TPM clear via PPI, vendor BIOS tools (Dell `cctk`, HP BCU, Lenovo WMI)
for BIOS password/Computrace detection, label/QR printing.
