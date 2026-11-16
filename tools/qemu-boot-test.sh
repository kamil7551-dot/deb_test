#!/bin/sh
# Boot the ISO in QEMU and check that the tester start screen appears (OCR on a screenshot).
#   [MEDIA=cdrom|usb|usb-raw] tools/qemu-boot-test.sh out/itad-tester-0.1.0.iso bios|uefi|secureboot [outdir]
# Needs: qemu-system-x86, ovmf, tesseract-ocr, imagemagick, mtools, dosfstools, fdisk, socat, python3-yaml.
# Uses KVM when present. The VM gets a fake laptop identity (SMBIOS) and an NVMe disk, plus the tester as:
#   cdrom    ISO as CD + a separate TESTERCFG disk (start screen shows "Konfiguracja: pendrive")
#   usb      ISO dd-ed to a 2 GB USB stick + TESTERCFG partition added by `tester --make-storage IMAGE`
#   usb-raw  ISO dd-ed to a 2 GB USB stick only; the test presses F9 + Enter on the start screen so the
#            tester creates TESTERCFG on its own stick, then checks the partition in the image afterwards
set -eu

ISO=$1; MODE=$2; MEDIA=${MEDIA:-cdrom}; OUT=${3:-out/boot-$MODE-$MEDIA}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TIMEOUT=${BOOT_TIMEOUT:-300}
mkdir -p "$OUT"
WORK=$(mktemp -d)
MON="$WORK/mon.sock"

export MTOOLS_SKIP_CHECK=1
case "$MEDIA" in
  cdrom)
    # TESTERCFG disk from the example config
    dd if=/dev/zero of="$WORK/cfg.img" bs=1M count=64 status=none
    mkfs.vfat -n TESTERCFG "$WORK/cfg.img" >/dev/null
    mcopy -s -i "$WORK/cfg.img" "$ROOT"/config.example/TESTERCFG/* ::/
    MEDIA_ARGS="-cdrom $ISO -boot d -drive file=$WORK/cfg.img,format=raw,if=virtio" ;;
  usb|usb-raw)
    STICK="$WORK/stick.img"
    cp "$ISO" "$STICK"
    truncate -s 2G "$STICK"
    if [ "$MEDIA" = usb ]; then
      PYTHONPATH="$ROOT/app" TESTER_CFG_TEMPLATE="$ROOT/config.example/TESTERCFG" \
        python3 -m tester --make-storage "$STICK" --yes
    fi
    sfdisk -l "$STICK"
    MEDIA_ARGS="-device qemu-xhci -drive if=none,id=stick,format=raw,file=$STICK -device usb-storage,drive=stick,bootindex=0,removable=on" ;;
  *) echo "MEDIA: cdrom|usb|usb-raw" >&2; exit 1 ;;
esac
qemu-img create -f qcow2 "$WORK/nvme.qcow2" 32G >/dev/null

ACCEL="-accel tcg"
[ -w /dev/kvm ] && ACCEL="-accel kvm"
FW=""
case "$MODE" in
  bios) MACHINE="q35" ;;
  uefi)
    MACHINE="q35"
    cp /usr/share/OVMF/OVMF_VARS_4M.fd "$WORK/vars.fd"
    FW="-drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd -drive if=pflash,format=raw,file=$WORK/vars.fd" ;;
  secureboot)
    MACHINE="q35,smm=on"
    cp /usr/share/OVMF/OVMF_VARS_4M.ms.fd "$WORK/vars.fd"
    FW="-global driver=cfi.pflash01,property=secure,value=on -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd -drive if=pflash,format=raw,file=$WORK/vars.fd" ;;
  *) echo "mode: bios|uefi|secureboot" >&2; exit 1 ;;
esac

# shellcheck disable=SC2086
qemu-system-x86_64 $ACCEL -machine $MACHINE -m 3072 -smp 2 $FW \
  -smbios type=1,manufacturer=TestCo,product=QEMU-Laptop,version=QEMU-Laptop-G1,serial=CI-SERIAL-1234 \
  -smbios type=3,manufacturer=TestCo,serial=CI-SERIAL-1234 \
  $MEDIA_ARGS \
  -drive file="$WORK/nvme.qcow2",if=none,id=nv0 -device nvme,drive=nv0,serial=NVME-CI-1 \
  -vga std -display none -monitor unix:"$MON",server,nowait -serial file:"$OUT/serial.log" \
  -daemonize -pidfile "$WORK/qemu.pid"

mon() { echo "$1" | socat - unix-connect:"$MON" >/dev/null; }
shot() { mon "screendump $WORK/s.ppm"; sleep 1; convert "$WORK/s.ppm" "$1"; }

ok=0; t=0; pressed=0
while [ $t -lt "$TIMEOUT" ]; do
  sleep 15; t=$((t + 15))
  shot "$OUT/screen-$t.png" || continue
  tesseract "$OUT/screen-$t.png" "$WORK/ocr" >/dev/null 2>&1 || true
  TEXT=$(tr '\n' ' ' < "$WORK/ocr.txt" | tr -s ' ')
  echo "[$MODE/$MEDIA ${t}s] $TEXT" | cut -c1-400
  if [ "$MEDIA" = usb-raw ] && [ $pressed = 0 ] && echo "$TEXT" | grep -q "(F9)"; then
    cp "$OUT/screen-$t.png" "$OUT/offer.png"
    mon "sendkey f9"; sleep 3
    shot "$OUT/confirm.png"
    mon "sendkey ret"
    pressed=1; echo "  offer shown, pressed F9 + Enter"
    continue
  fi
  # after Enter: wait until the offer is gone and the footer no longer says "dane tylko w RAM"
  [ "$MEDIA" = usb-raw ] && [ $pressed = 1 ] && echo "$TEXT" | grep -q "(F9)\|tylko w RAM\|Tworzenie" && continue
  if echo "$TEXT" | grep -q "Tester ITAD"; then
    cp "$OUT/screen-$t.png" "$OUT/start-screen.png"
    echo "$TEXT" > "$OUT/start-screen.txt"
    ok=1; break
  fi
done

kill "$(cat "$WORK/qemu.pid")" 2>/dev/null || true
sleep 2
if [ "$MEDIA" != cdrom ]; then
  # The stick must now hold an MBR partition 3 (FAT32, TESTERCFG) with the tester's folders on it
  sfdisk -l "$STICK" | tee "$OUT/partitions.txt"
  START=$(sfdisk --json "$STICK" | python3 -c 'import json,sys; p=json.load(sys.stdin)["partitiontable"]["partitions"]; print(p[2]["start"] if len(p) > 2 else "")')
  if [ -z "$START" ] || ! mdir -i "$STICK@@$((START * 512))" ::/ | tee -a "$OUT/partitions.txt" | grep -q queue; then
    echo "FAIL: no TESTERCFG partition with a queue/ folder on the stick"; ok=0
  fi
  [ "$MEDIA" = usb-raw ] && [ $pressed = 0 ] && { echo "FAIL: the F9 offer was never shown"; ok=0; }
fi
rm -rf "$WORK"
if [ $ok = 1 ]; then
  echo "PASS: $MODE boot reached the tester start screen after ~${t}s"
  grep -q "CI-SERIAL-1234" "$OUT/start-screen.txt" && echo "  serial from SMBIOS shown" || echo "  WARN: serial not found by OCR"
  grep -q "pendrive" "$OUT/start-screen.txt" && echo "  TESTERCFG mounted" || echo "  WARN: TESTERCFG not reported as mounted"
  exit 0
fi
echo "FAIL: $MODE boot did not reach the start screen in ${TIMEOUT}s"
exit 1
