"""Independent reader for the phase-1 native artifacts.

It verifies the bytes the C++ encoder wrote, not a Python re-implementation:
  * chunk lengths and CRC32 of every PNG chunk,
  * IHDR bit depth 16 / color type 2 or 6,
  * IDAT inflate via the standard library, all scanline filters must be 0,
  * every sample must equal the value recomputed from the raw float32 input
    using the documented float32 rule, and also equal the raw uint16 dump,
  * iTXt metadata must round-trip as UTF-8.

Run after build_test.cmd + png16_phase1.exe:
    python tests/verify_png16_phase1.py tests/artifacts/png16
"""
from __future__ import annotations

import binascii
import array
import json
import math
import struct
import sys
import zlib
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def expected_u16(value: float) -> int:
    """Mirror of sd_hp::float_to_u16 with explicit float32 operations."""
    value = f32(value)
    if not (value > 0.0):  # also false for NaN
        return 0
    if value >= 1.0:
        return 65535
    return int(f32(f32(value * 65535.0) + 0.5))


def read_chunks(data: bytes):
    assert data[:8] == PNG_SIGNATURE, "not a PNG"
    offset = 8
    chunks = []
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])[0]
        computed = binascii.crc32(kind + payload) & 0xFFFFFFFF
        assert crc == computed, f"CRC mismatch in {kind!r}: {crc:08x} != {computed:08x}"
        chunks.append((kind, payload))
        offset += 12 + length
    assert chunks and chunks[0][0] == b"IHDR", "IHDR must be first"
    assert chunks[-1][0] == b"IEND", "IEND must be last"
    return chunks


def decode_png16(data: bytes):
    chunks = read_chunks(data)
    ihdr = chunks[0][1]
    width, height, depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", ihdr)
    assert depth == 16, f"bit depth is {depth}, not 16"
    assert color_type in (2, 6), f"color type is {color_type}, not RGB/RGBA"
    assert (compression, filtering, interlace) == (0, 0, 0)
    channels = 3 if color_type == 2 else 4
    idat = b"".join(payload for kind, payload in chunks if kind == b"IDAT")
    assert idat, "no IDAT"
    raw = zlib.decompress(idat)
    row_bytes = width * channels * 2
    assert len(raw) == height * (row_bytes + 1), f"unexpected raw size {len(raw)}"
    samples = []
    for y in range(height):
        start = y * (row_bytes + 1)
        assert raw[start] == 0, f"scanline {y} uses filter {raw[start]}, expected 0"
        samples.append(struct.unpack(f">{width * channels}H", raw[start + 1:start + 1 + row_bytes]))
    text = {}
    for kind, payload in chunks:
        if kind == b"iTXt":
            keyword, rest = payload.split(b"\x00", 1)
            flag, method = rest[0], rest[1]
            body = rest[2:]
            language, body = body.split(b"\x00", 1)
            translated, text_bytes = body.split(b"\x00", 1)
            assert (flag, method) == (0, 0), "compressed iTXt is not expected here"
            assert language == b"" and translated == b""
            text[keyword.decode("latin-1")] = text_bytes.decode("utf-8")
    return {
        "width": width,
        "height": height,
        "channels": channels,
        "color_type": color_type,
        "samples": samples,
        "text": text,
        "idat_bytes": len(idat),
        "raw_bytes": len(raw),
    }


def source_index(entry, x, y, channel, frame):
    """Index into the dumped float32 buffer for the layout recorded in the manifest."""
    width, height = entry["width"], entry["height"]
    channels, frames = entry["channels"], entry["frames"]
    if entry["layout"] == "w-h-c-frame":
        return x + width * (y + height * (channel + channels * frame))
    return x + width * (y + height * (frame + frames * channel))


def check_pattern(directory: Path, entry: dict) -> dict:
    name = entry["name"]
    width, height = entry["width"], entry["height"]
    channels, frames = entry["channels"], entry["frames"]
    count = width * height * channels

    png = decode_png16((directory / f"{name}.png").read_bytes())
    assert (png["width"], png["height"], png["channels"]) == (width, height, channels)
    assert png["raw_bytes"] == height * (width * channels * 2 + 1)

    floats = struct.unpack("<%df" % (count * frames), (directory / f"{name}.f32").read_bytes())
    dumped = struct.unpack("<%dH" % count, (directory / f"{name}.u16").read_bytes())
    samples = [value for row in png["samples"] for value in row]
    assert len(samples) == count

    # Frame 0 of the source, converted through the documented rule.
    expected = [0] * count
    for y in range(height):
        for x in range(width):
            for c in range(channels):
                v = floats[source_index(entry, x, y, c, 0)]
                expected[(y * width + x) * channels + c] = expected_u16(v)

    for i, (got, want, raw_dump) in enumerate(zip(samples, expected, dumped)):
        assert got == want == raw_dump, f"{name}: sample {i} png={got} expected={want} dump={raw_dump}"

    metadata = png["text"].get("parameters")
    assert metadata is not None, f"{name}: missing parameters iTXt"
    parsed = json.loads(metadata)
    assert parsed["pattern"] == name and parsed["note"] == "中文测试", f"{name}: iTXt mismatch {parsed}"

    distinct = len(set(samples))
    modulo = sum(1 for v in samples if v % 257 == 0) / count
    return {
        "name": name,
        "samples": count,
        "distinct": distinct,
        "mod257": modulo,
        "png_bytes": len((directory / f"{name}.png").read_bytes()),
        "raw_bytes": png["raw_bytes"],
        "idat_bytes": png["idat_bytes"],
    }


def main() -> int:
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/artifacts/png16")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for entry in manifest:
        rows.append(check_pattern(directory, entry))

    print(f"{'pattern':10} {'samples':>9} {'distinct':>9} {'v%257==0':>9} {'png B':>10} {'raw B':>10}")
    for row in rows:
        print(f"{row['name']:10} {row['samples']:9d} {row['distinct']:9d} "
              f"{row['mod257'] * 100:8.3f}% {row['png_bytes']:10d} {row['raw_bytes']:10d}")

    # Synthetic-only expectations. They guard the chain, not real-image quality.
    by_name = {row["name"]: row for row in rows}
    assert by_name["ramp3"]["distinct"] == 65536, "ramp must exercise all 16-bit codes"
    assert by_name["ramp3"]["mod257"] < 0.01, "ramp looks like an 8-bit value multiplied by 257"
    assert by_name["rgba4"]["distinct"] == 2048

    bench_png = directory / "bench1024.png"
    bench_u16 = directory / "bench1024.u16"
    if bench_png.is_file() and bench_u16.is_file():
        png = decode_png16(bench_png.read_bytes())
        flat = array.array("H", (value for row in png["samples"] for value in row))
        dumped = array.array("H")
        dumped.frombytes(bench_u16.read_bytes())
        assert flat == dumped, "bench1024 PNG differs from the uint16 buffer"
        print(f"bench1024  {len(flat)} samples, PNG {bench_png.stat().st_size} B, "
              f"raw {bench_u16.stat().st_size} B, bytes match")

    print("\nphase-1 pixel chain verified: C++ conversion + C++ PNG16 encoder + independent readback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
