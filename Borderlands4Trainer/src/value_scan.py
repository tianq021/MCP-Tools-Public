"""Bounded exact-value research. Candidate matches are not verified game fields."""
import struct
import time

FORMATS = {'i32': '<i', 'u32': '<I', 'i64': '<q', 'u64': '<Q', 'f32': '<f', 'f64': '<d'}


def encode(value, kind):
    if kind not in FORMATS:
        raise ValueError('Unsupported numeric type')
    number = float(value) if kind.startswith('f') else int(value)
    return struct.pack(FORMATS[kind], number)


def matches(data, base, needle, alignment):
    offset = data.find(needle)
    while offset >= 0:
        if (base + offset) % alignment == 0:
            yield base + offset
        offset = data.find(needle, offset + 1)


def validate_identity(saved, actual):
    if saved != actual:
        raise ValueError('Candidate file belongs to a different process instance; rescan')


def scan(reader, value, kind='i32', max_seconds=30, max_candidates=100000, chunk_size=1024 * 1024):
    needle = encode(value, kind)
    if chunk_size < len(needle) or max_seconds <= 0 or max_candidates <= 0:
        raise ValueError('Invalid scan limits')
    alignment = min(len(needle), 4)
    addresses, read_bytes, skipped = [], 0, 0
    started = time.monotonic()
    reason = None
    for base, size in reader.regions():
        for offset in range(0, size, chunk_size):
            reader.require_alive()
            if time.monotonic() - started >= max_seconds:
                reason = 'time_limit'
                break
            # Overlap lets 64-bit values aligned to 4 bytes span chunk boundaries.
            length = min(chunk_size + len(needle) - 1, size - offset)
            try:
                data = reader.read(base + offset, length)
            except OSError:
                skipped += length
                continue
            read_bytes += len(data)
            for address in matches(data, base + offset, needle, alignment):
                if address >= base + offset + chunk_size:
                    continue
                if len(addresses) >= max_candidates:
                    reason = 'candidate_limit'
                    break
                addresses.append(address)
            if reason:
                break
        if reason:
            break
    reader.require_alive()
    return dict(addresses=addresses, numeric_type=kind, value=str(value),
                complete=reason is None and skipped == 0, stop_reason=reason,
                skipped_bytes=skipped, read_bytes=read_bytes,
                elapsed_seconds=round(time.monotonic() - started, 3),
                scope='committed writable regions; alignment <= 4; exact numeric representation')


def refine(reader, previous, value):
    needle = encode(value, previous['numeric_type'])
    kept, unreadable = [], 0
    for address in previous['addresses']:
        reader.require_alive()
        try:
            if reader.read(address, len(needle)) == needle:
                kept.append(address)
        except OSError:
            unreadable += 1
    return dict(previous, addresses=kept, value=str(value), unreadable_candidates=unreadable)
