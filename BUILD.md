# BUILD.md

How to build the tester ISO on your own machine or VM, without GitHub Actions. This file is enough on its own:
a person or a build bot needs only this repository and these steps. Keep it in sync with `tools/build-iso.sh`,
`tools/vm-build.sh` and the package lists (AGENTS.md rule 1).

## 1. Machine

- Debian 13 "trixie", amd64 (a VM is fine). Other distros: use the container route in step 4.
- Root (sudo), 2+ CPU, 4 GB RAM, 20 GB free disk.
- Internet access to `deb.debian.org` (live-build downloads ~1.5 GB of packages; later builds reuse the cache).

## 2. Get the code

```sh
git clone https://github.com/kamil7551-dot/deb_test.git
cd deb_test
git checkout main && git pull        # or the branch / tag you want to build
```

## 3. Build (one command)

```sh
sudo tools/vm-build.sh
```

It installs the build tools, runs pyflakes + unit tests + a headless smoke run, then builds the image.
Result: `out/itad-tester-<version>.iso` and `out/itad-tester-<version>.iso.sha256`. The version comes from
`app/tester/__init__.py` (`__version__`). Takes 15–40 min the first time, less with a warm cache.

- Already have the tools: `SKIP_DEPS=1 sudo tools/vm-build.sh`.
- Only the ISO: `sudo tools/build-iso.sh`.
- The build fails if the ISO is larger than 1 GiB (`MAX_ISO_BYTES`), and prints its size.
- On failure read `live-build/build.log`. A broken half-build is cleaned automatically on the next run
  (`lb clean --purge`).

## 4. Container route (any Linux host with Docker or Podman)

```sh
docker run --rm -it --privileged -v "$PWD":/src -w /src debian:trixie tools/vm-build.sh
```

`--privileged` is required (live-build mounts /proc and loop devices inside the chroot).

## 5. Check the image (optional, needs KVM for speed)

```sh
sudo apt-get install -y qemu-system-x86 qemu-utils ovmf tesseract-ocr imagemagick mtools dosfstools fdisk socat python3-yaml
tools/qemu-boot-test.sh out/itad-tester-*.iso uefi                 # ISO as CD + TESTERCFG disk
MEDIA=usb-raw tools/qemu-boot-test.sh out/itad-tester-*.iso uefi   # dd-ed USB stick, presses F9 + Enter
```

Each prints `PASS` when the start screen appears; screenshots land in `out/boot-<mode>-<media>/`.

## 6. Publish a release (optional)

With the GitHub CLI logged in to the repo:

```sh
V=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/tester/__init__.py)
gh release create "v$V" out/itad-tester-$V.iso out/itad-tester-$V.iso.sha256 --target main \
  --title "ITAD tester v$V" --notes "See ROADMAP.md Changelog"
```

The ISO (~600 MB) is below GitHub's 2 GB per-file limit. Without a release, copy the ISO to the people who
write sticks; they follow the operator guide (Rufus, DD mode, then F9 on first boot).

## What the build does

`tools/build-iso.sh` copies `app/tester`, `profiles/` and `config.example/` into
`live-build/config/includes.chroot/opt/tester`, runs `lb config` (settings in `live-build/auto/config`) and
`lb build`, then moves the hybrid ISO to `out/`. Packages: `live-build/config/package-lists/*.list.chroot`;
image tweaks: `live-build/config/hooks/`.
