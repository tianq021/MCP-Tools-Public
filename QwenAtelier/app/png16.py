"""PNG byte-level helpers for the 16-bit master.

Only text chunks are added or replaced. Image data (IHDR/PLTE/IDAT and all
other chunks) is copied verbatim, so the stored samples are exactly the bytes
the native encoder produced.
"""
from __future__ import annotations

import binascii
import struct

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def read_header(data: bytes) -> dict:
    """Parse and CRC-check the IHDR. `data` may be only the first 33 bytes."""
    if len(data) < 33 or data[:8] != PNG_SIGNATURE:
        raise ValueError("不是有效的 PNG 文件")
    length, kind = struct.unpack(">I4s", data[8:16])
    if kind != b"IHDR" or length != 13:
        raise ValueError("PNG 缺少 IHDR")
    payload = data[16:29]
    expected = struct.unpack(">I", data[29:33])[0]
    if (binascii.crc32(kind + payload) & 0xFFFFFFFF) != expected:
        raise ValueError("PNG IHDR 校验失败")
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", payload
    )
    return {
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        "compression": compression,
        "filtering": filtering,
        "interlace": interlace,
    }


def _chunks(data: bytes):
    if len(data) < 8 or data[:8] != PNG_SIGNATURE:
        raise ValueError("不是有效的 PNG 文件")
    offset = 8
    chunks = []
    while True:
        if offset + 12 > len(data):
            raise ValueError("PNG 块结构不完整")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("PNG 块长度越界")
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", data[offset + 8 + length:end])[0]
        if (binascii.crc32(kind + payload) & 0xFFFFFFFF) != crc:
            raise ValueError(f"PNG 块 CRC 校验失败: {kind!r}")
        chunks.append((kind, payload, data[offset:end]))
        offset = end
        if kind == b"IEND":
            break
    if chunks[-1][0] != b"IEND":
        raise ValueError("PNG 缺少 IEND")
    return chunks


def _itxt_chunk(keyword: str, text: str) -> bytes:
    payload = (
        keyword.encode("latin-1")
        + b"\x00"          # keyword terminator
        + b"\x00\x00"      # uncompressed, method 0
        + b"\x00" + b"\x00"  # empty language tag, empty translated keyword
        + text.encode("utf-8")
    )
    crc = binascii.crc32(b"iTXt" + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + b"iTXt" + payload + struct.pack(">I", crc)


def insert_text(data: bytes, keyword: str, text: str) -> bytes:
    """Insert or replace one UTF-8 iTXt chunk, leaving image chunks untouched."""
    if not keyword or len(keyword.encode("latin-1")) > 79:
        raise ValueError("PNG 文本关键字无效")
    chunks = _chunks(data)
    targets = keyword.encode("latin-1")
    replacement = _itxt_chunk(keyword, text)
    out = bytearray(PNG_SIGNATURE)
    inserted = False
    for kind, payload, raw in chunks:
        if kind in (b"iTXt", b"tEXt", b"zTXt") and payload.split(b"\x00", 1)[0] == targets:
            continue
        if kind == b"IDAT" and not inserted:
            out.extend(replacement)
            inserted = True
        out.extend(raw)
    if not inserted:
        raise ValueError("PNG 缺少 IDAT")
    return bytes(out)
