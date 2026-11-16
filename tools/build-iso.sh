#!/bin/sh
# Build the ITAD tester ISO. Needs Debian trixie (or a privileged debian:trixie container) and root.
#   sudo tools/build-iso.sh            -> out/itad-tester-<version>.iso
# Size budget: the build fails when the ISO is larger than MAX_ISO_BYTES (default 1 GiB).
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
LB="$ROOT/live-build"
OUT="$ROOT/out"
MAX_ISO_BYTES=${MAX_ISO_BYTES:-1073741824}
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/app/tester/__init__.py")

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

# Fresh copy of the application into the image tree
INC="$LB/config/includes.chroot/opt/tester"
rm -rf "$INC"
mkdir -p "$INC"
cp -r "$ROOT/app/tester" "$INC/tester"
cp -r "$ROOT/profiles" "$INC/profiles"
cp -r "$ROOT/config.example" "$INC/config.example"
find "$INC" -name '__pycache__' -type d -exec rm -rf {} +
echo "$VERSION" > "$INC/VERSION"

cd "$LB"
lb clean --purge >/dev/null 2>&1 || true
lb config
lb build

ISO=$(ls -1 "$LB"/*.iso 2>/dev/null | head -1)
[ -n "$ISO" ] || { echo "no ISO produced, see live-build/build.log" >&2; exit 1; }
mkdir -p "$OUT"
DEST="$OUT/itad-tester-$VERSION.iso"
mv "$ISO" "$DEST"
SIZE=$(stat -c %s "$DEST")
echo "ISO: $DEST ($((SIZE / 1024 / 1024)) MiB)"
sha256sum "$DEST" > "$DEST.sha256"
if [ "$SIZE" -gt "$MAX_ISO_BYTES" ]; then
  echo "ERROR: ISO is larger than the budget of $((MAX_ISO_BYTES / 1024 / 1024)) MiB" >&2
  exit 2
fi
