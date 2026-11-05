#!/bin/sh
# Boot the ISO in QEMU and check that the tester start screen appears (OCR on a screenshot).
#   tools/qemu-boot-test.sh out/itad-tester-0.1.0.iso bios|uefi|secureboot [outdir]
# Needs: qemu-system-x86, ovmf, tesseract-ocr, imagemagick, mtools, dosfstools, socat. Uses KVM when present.
# The VM gets a fake laptop identity (SMBIOS), an NVMe disk and a TESTERCFG disk, so the start screen
# should show the serial and "Konfiguracja: pendrive".
set -eu

ISO=$1; MODE=$2; OUT=${3:-out/boot-$MODE}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TIMEOUT=${BOOT_TIMEOUT:-300}
mkdir -p "$OUT"
WORK=$(mktemp -d)
MON="$WORK/mon.sock"

# TESTERCFG disk from the example config
dd if=/dev/zero of="$WORK/cfg.img" bs=1M count=64 status=none
mkfs.vfat -n TESTERCFG "$WORK/cfg.img" >/dev/null
MTOOLS_SKIP_CHECK=1 mcopy -s -i "$WORK/cfg.img" "$ROOT"/config.example/TESTERCFG/* ::/
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
  -cdrom "$ISO" -boot d \
  -drive file="$WORK/cfg.img",format=raw,if=virtio \
  -drive file="$WORK/nvme.qcow2",if=none,id=nv0 -device nvme,drive=nv0,serial=NVME-CI-1 \
  -vga std -display none -monitor unix:"$MON",server,nowait -serial file:"$OUT/serial.log" \
  -daemonize -pidfile "$WORK/qemu.pid"

shot() { echo "screendump $WORK/s.ppm" | socat - unix-connect:"$MON" >/dev/null; sleep 1; convert "$WORK/s.ppm" "$1"; }

ok=0; t=0
while [ $t -lt "$TIMEOUT" ]; do
  sleep 15; t=$((t + 15))
  shot "$OUT/screen-$t.png" || continue
  tesseract "$OUT/screen-$t.png" "$WORK/ocr" >/dev/null 2>&1 || true
  TEXT=$(tr '\n' ' ' < "$WORK/ocr.txt" | tr -s ' ')
  echo "[$MODE ${t}s] $TEXT" | cut -c1-400
  if echo "$TEXT" | grep -q "Tester ITAD"; then
    cp "$OUT/screen-$t.png" "$OUT/start-screen.png"
    echo "$TEXT" > "$OUT/start-screen.txt"
    ok=1; break
  fi
done

kill "$(cat "$WORK/qemu.pid")" 2>/dev/null || true
if [ $ok = 1 ]; then
  echo "PASS: $MODE boot reached the tester start screen after ~${t}s"
  grep -q "CI-SERIAL-1234" "$OUT/start-screen.txt" && echo "  serial from SMBIOS shown" || echo "  WARN: serial not found by OCR"
  grep -q "pendrive" "$OUT/start-screen.txt" && echo "  TESTERCFG mounted" || echo "  WARN: TESTERCFG not reported as mounted"
  exit 0
fi
echo "FAIL: $MODE boot did not reach the start screen in ${TIMEOUT}s"
exit 1
