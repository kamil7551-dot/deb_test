#!/bin/sh
# Write the tester ISO to a USB stick and add a TESTERCFG partition (config, token, Wi-Fi, report queue)
# in the free space after the image.
#   sudo tools/make-usb.sh /dev/sdX out/itad-tester-0.1.0.iso [path/to/TESTERCFG]
# Everything on /dev/sdX is erased. On Windows use Rufus (DD mode) + a second stick or Ventoy instead,
# see INSTRUCTIONS.md § USB stick.
set -eu

DEV=${1:-}; ISO=${2:-}; CFG=${3:-"$(cd "$(dirname "$0")/.." && pwd)/config.example/TESTERCFG"}
[ -b "$DEV" ] && [ -f "$ISO" ] || { echo "usage: $0 /dev/sdX image.iso [TESTERCFG dir]" >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ "$(lsblk -dno TYPE "$DEV")" = disk ] || { echo "$DEV is not a whole disk" >&2; exit 1; }
[ "$(lsblk -dno RM "$DEV")" = 1 ] || [ "${FORCE:-0}" = 1 ] || { echo "$DEV is not removable (set FORCE=1 to override)" >&2; exit 1; }

lsblk -o NAME,SIZE,MODEL,TRAN "$DEV"
printf 'ALL DATA ON %s WILL BE ERASED. Type YES: ' "$DEV"
read -r answer
[ "$answer" = YES ] || exit 1

for p in $(lsblk -lnpo NAME "$DEV" | tail -n +2); do umount "$p" 2>/dev/null || true; done
dd if="$ISO" of="$DEV" bs=4M conv=fsync status=progress
sync
partprobe "$DEV" 2>/dev/null || true
sleep 2

LABEL=$(blkid -o value -s PTTYPE "$DEV" || echo dos)
if [ "$LABEL" = gpt ]; then
  sgdisk -e "$DEV"                                   # move backup GPT header to the real end
  sgdisk -n 0:0:0 -t 0:0700 -c 0:TESTERCFG "$DEV"
else
  echo ',,c' | sfdisk --append --no-reread --force "$DEV"
fi
partprobe "$DEV" 2>/dev/null || true
sleep 2
NEW=$(lsblk -lnpo NAME,TYPE "$DEV" | awk '$2=="part"{p=$1} END{print p}')
mkfs.vfat -F 32 -n TESTERCFG "$NEW"
MNT=$(mktemp -d)
mount "$NEW" "$MNT"
cp -r "$CFG"/. "$MNT"/
mkdir -p "$MNT/queue" "$MNT/sent" "$MNT/failed" "$MNT/logs"
umount "$MNT"
rmdir "$MNT"
echo "Done. Put prospectra.env (token) and wifi/*.nmconnection on the TESTERCFG partition ($NEW)."
