#!/usr/bin/env bash
set -eu
DLL=/p/bin/libra2yrcpp.dll
RVA=0x37071c
IB_HEX=$(i686-w64-mingw32-objdump -p "$DLL" | awk '/^ImageBase/ {print $2}' | head -1)
IB=$((0x$IB_HEX))
VA=$((IB + RVA))
echo "ImageBase=0x$IB_HEX rva=0x37071c va=0x$(printf %x $VA)"
START=$((VA - 0x80))
STOP=$((VA + 0x80))
echo "--- disasm $START..$STOP ---"
i686-w64-mingw32-objdump -d --start-address=$START --stop-address=$STOP -M intel "$DLL" > /tmp/d.txt
cat /tmp/d.txt | tail -100
echo "--- symbols near VA (if any) ---"
i686-w64-mingw32-objdump -t "$DLL" > /tmp/sym.txt 2>/dev/null || true
grep -iE ' (T|t) ' /tmp/sym.txt | head -20 || true
echo "--- done ---"
