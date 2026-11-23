#!/bin/sh
# One-shot build of the tester ISO on a Debian 13 (trixie) amd64 VM, as root. See BUILD.md.
#   sudo tools/vm-build.sh            # deps + unit tests + ISO  -> out/itad-tester-<version>.iso
#   SKIP_DEPS=1 SKIP_TESTS=1 sudo tools/vm-build.sh
# On a non-Debian host use the container route in BUILD.md instead.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
. /etc/os-release
[ "${VERSION_CODENAME:-}" = trixie ] || echo "WARNING: expected Debian trixie, found ${PRETTY_NAME:-unknown}" >&2

if [ "${SKIP_DEPS:-0}" != 1 ]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    live-build debootstrap xorriso squashfs-tools mtools dosfstools fdisk isolinux syslinux-common \
    syslinux-utils grub-efi-amd64-bin grub-pc-bin cpio ca-certificates python3 python3-yaml \
    python3-pytest pyflakes3
fi

if [ "${SKIP_TESTS:-0}" != 1 ]; then
  (cd app && python3 -m pyflakes tester tests && python3 -m pytest -q)
  (cd app && TESTER_DATA_DIR=$(mktemp -d) TESTER_PROFILES=../profiles python3 -m tester --headless --profile quick >/dev/null)
fi

tools/build-iso.sh
ls -l out/
