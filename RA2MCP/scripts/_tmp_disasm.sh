#!/usr/bin/env bash
set -e
cp /dll/libra2yrcpp.dll /tmp/libra2yrcpp.dll
cd /tmp
IB=$(i686-w64-mingw32-objdump -p libra2yrcpp.dll | awk '/^ImageBase/ {printf "0x%s", $2; exit}')
echo "ImageBase=$IB  (RVA 0x37071c -> VA $((IB + 0x37071c)))"
START=$((IB + 0x3706c0))
END=$((IB + 0x370780))
echo "--- disasm $START..$END ---"
i686-w64-mingw32-objdump -d --start-address=$START --stop-address=$END -M intel libra2yrcpp.dll
echo "--- .text ?????---"
i686-w64-mingw32-objdump -h libra2yrcpp.dll | grep -A1 -E '\.text'

