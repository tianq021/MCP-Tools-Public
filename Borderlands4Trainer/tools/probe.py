"""CLI for inspect / exact-value scan / refine. All target access is read-only."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from process_reader import Reader, game_pids, modules
from value_scan import FORMATS, scan, refine, validate_identity


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'scan', 'refine'])
    parser.add_argument('--pid', type=int)
    parser.add_argument('--value')
    parser.add_argument('--type', choices=FORMATS, default='i32')
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    expected = load(ROOT / 'trainer_config.json')['expected_executable']
    matching, errors = [], []
    for pid in ([args.pid] if args.pid else game_pids()):
        try:
            with Reader(pid, expected) as reader:
                matching.append(pid)
        except (OSError, ValueError) as error:
            errors.append(str(error))
    if len(matching) != 1:
        raise RuntimeError(f'Expected one matching BL4 process; found {len(matching)}. {errors}')
    with Reader(matching[0], expected) as reader:
        identity = reader.identity()
        result = dict(observed_at=datetime.now(timezone.utc).isoformat(),
                      process=identity, read_only=True)
        if args.action == 'inspect':
            with open(reader.path, 'rb') as stream:
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                    digest.update(chunk)
            baseline = load(ROOT / 'research' / 'local_baseline.json')
            result.update(executable_sha256=digest.hexdigest().upper())
            try:
                result['modules'] = modules(reader.pid)
                result['module_enumeration_error'] = None
            except OSError as error:
                result['modules'] = []
                result['module_enumeration_error'] = str(error)
            result['baseline_matches'] = any(e['sha256'].upper() == result['executable_sha256'] for e in baseline['executables'])
            regions = list(reader.regions())
            result.update(writable_region_count=len(regions), writable_bytes=sum(size for _, size in regions))
            output = args.output or ROOT / 'research' / 'runtime_inspection.json'
        else:
            if args.value is None:
                parser.error('--value is required for scan/refine')
            if args.action == 'scan':
                result['scan'] = scan(reader, args.value, args.type, args.seconds)
            else:
                if args.input is None:
                    parser.error('--input is required for refine')
                previous = load(args.input)
                validate_identity(previous['process'], identity)
                result['scan'] = refine(reader, previous['scan'], args.value)
            output = args.output or ROOT / 'research' / 'candidates.local.json'
        reader.require_alive()
        save(output, result)
    summary = dict(output=str(output), pid=matching[0], read_only=True)
    if 'scan' in result:
        summary.update(candidates=len(result['scan']['addresses']), complete=result['scan']['complete'])
    else:
        summary.update(baseline_matches=result['baseline_matches'], modules=len(result['modules']),
                       writable_region_count=result['writable_region_count'],
                       module_enumeration_error=result['module_enumeration_error'])
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        sys.exit(1)
