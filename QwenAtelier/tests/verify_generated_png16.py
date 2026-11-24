"""Inspect a generated PNG's actual samples without truncating them through Pillow."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from verify_png16_phase1 import decode_png16


def inspect_image(path: Path) -> dict:
    raw = path.read_bytes()
    decoded = decode_png16(raw)
    channels = decoded['channels']
    values = [set() for _ in range(channels)]
    non_u8 = [0] * channels
    for row in decoded['samples']:
        for channel in range(channels):
            samples = row[channel::channels]
            values[channel].update(samples)
            non_u8[channel] += sum(sample % 257 != 0 for sample in samples)
    config = json.loads(decoded['text'].get('qwen_studio', '{}'))
    return {
        'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest(),
        'width': decoded['width'], 'height': decoded['height'], 'bit_depth': 16,
        'channels': channels, 'all_chunk_crcs_valid': True,
        'distinct_values_per_channel': [len(channel) for channel in values],
        'samples_not_on_u8_times_257_grid': non_u8,
        'not_simple_u8_times_257_conversion': any(non_u8[:3]),
        'engine_api': config.get('engine_api'),
        'engine_declared_bit_depth': config.get('bit_depth_engine_declared'),
        'precision_source': config.get('precision_source'),
        'note': 'File format and sample inspection only; this does not establish 16 effective bits of model precision.',
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('image', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = inspect_image(args.image)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report['engine_api'] != 'generate_image_u16' or report['engine_declared_bit_depth'] != 16:
        raise RuntimeError('The image lacks the expected native 16-bit generation provenance')
    if not report['not_simple_u8_times_257_conversion']:
        raise RuntimeError('All RGB samples lie on the 8-bit times 257 grid; inspect the output chain')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
