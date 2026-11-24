"""Download a pinned upstream runtime, verify GitHub digests, extract locally."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TAG = 'master-982-0518b34'
API = f'https://api.github.com/repos/leejet/stable-diffusion.cpp/releases/tags/{TAG}'
NAMES = ['sd-master-0518b34-bin-win-cuda12-x64.zip', 'cudart-sd-bin-win-cu12-x64.zip']


def main():
    with urllib.request.urlopen(API, timeout=60) as response:
        release = json.load(response)
    (ROOT / 'tests/artifacts/native-release.json').write_text(json.dumps(release, indent=2), encoding='utf-8')
    records = []
    dest = ROOT / 'runtime/stable-diffusion.cpp'
    for name in NAMES:
        asset = next(a for a in release['assets'] if a['name'] == name)
        archive = ROOT / 'downloads/native' / name
        partial = archive.with_suffix('.zip.part')
        if not archive.exists():
            print(f'Downloading {name}: {asset["size"]:,} bytes', flush=True)
            subprocess.run(['curl.exe', '-fL', '--retry', '4', '--retry-delay', '3',
                            '--connect-timeout', '30', '--max-time', '1800', '-C', '-',
                            '--silent', '--show-error', '-o', str(partial), asset['browser_download_url']], check=True)
            archive_to_check = partial
        else:
            archive_to_check = archive
        with archive_to_check.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if archive_to_check.stat().st_size != asset['size'] or f'sha256:{digest}' != asset['digest']:
            raise RuntimeError(f'Archive integrity check failed: {name}')
        if archive_to_check == partial:
            partial.replace(archive)
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                target = (dest / entry.filename).resolve()
                if not target.is_relative_to(dest.resolve()):
                    raise RuntimeError('Archive contains unsafe path')
            print(f'Verified {name}; extracting {len(z.infolist())} entries', flush=True)
            z.extractall(dest)
            unpacked = sum(e.file_size for e in z.infolist())
        records.append({'name': name, 'url': asset['browser_download_url'], 'size_bytes': asset['size'],
                        'sha256': digest, 'unpacked_bytes': unpacked, 'verified': True})
        (ROOT / 'tests/artifacts/native-downloads.json').write_text(json.dumps({'tag': TAG, 'assets': records}, indent=2), encoding='utf-8')
    print('Native runtime downloads and extraction complete.', flush=True)


if __name__ == '__main__':
    main()
