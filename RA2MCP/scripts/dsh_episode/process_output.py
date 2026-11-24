"""Capture OS command output as bytes; retain evidence and previous game logs."""
from pathlib import Path
import hashlib
import json
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def run_captured(args, timeout=20, evidence_root=None):
    folder = Path(evidence_root or ROOT/'build/process-command-output') / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    started = time.time()
    result = None
    error = None
    try:
        result = subprocess.run(args, capture_output=True, text=False, timeout=timeout)
        return result
    except (subprocess.TimeoutExpired, OSError) as exc:
        error = exc
        raise
    finally:
        stdout = result.stdout if result is not None else getattr(error, 'stdout', None)
        stderr = result.stderr if result is not None else getattr(error, 'stderr', None)
        for name, value in [('stdout.bin', stdout), ('stderr.bin', stderr)]:
            if value is not None:
                (folder/name).write_bytes(value)
        (folder/'command.json').write_text(json.dumps({
            'args': [str(a) for a in args], 'started': started,
            'returncode': None if result is None else result.returncode,
            'timeout': isinstance(error, subprocess.TimeoutExpired),
            'error': None if error is None else str(error), 'raw_output': True,
        }, ensure_ascii=False, indent=2), encoding='utf-8')


def archive_launch_logs(game_dir):
    game_dir = Path(game_dir).resolve()
    relative = game_dir.relative_to(ROOT.resolve())
    if not relative.parts or not relative.parts[0].startswith('test-game'):
        raise ValueError('Only isolated test-game directories may be archived')
    files = [game_dir/name for name in ('ra2yrcpp.log', 'stats.dmp', 'dsh-game-stdout.log', 'dsh-game-stderr.log')
             if (game_dir/name).is_file()]
    if not files:
        return None
    folder = game_dir/'old'/('launch-'+uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=False)
    entries = [{'source': str(p), 'target': str(folder/p.name), 'size': p.stat().st_size,
                'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    # Manifest precedes moves so a partial archive is recoverable. No overwrite.
    (folder/'MANIFEST.json').write_text(json.dumps(entries, indent=2), encoding='utf-8')
    for item in entries:
        source, target = Path(item['source']), Path(item['target'])
        if target.exists():
            raise FileExistsError(target)
        source.rename(target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Archived log hash mismatch')
    return folder
