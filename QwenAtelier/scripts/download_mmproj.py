"""Download the verified Qwen3-VL-8B vision projector without loading a model."""
from concurrent.futures import ThreadPoolExecutor, wait
import hashlib
import json
from pathlib import Path
import shutil
import struct
import time

import httpx


ROOT = Path(__file__).resolve().parents[1]
NAME = 'mmproj-Qwen3VL-8B-Instruct-F16.gguf'
SIZE = 1159029824
SHA256 = 'ca524100ebf825c9a870db1c580d03879e0da0ab2541697e2458e64891cf9d38'
REPO = 'Qwen/Qwen3-VL-8B-Instruct-GGUF'
OFFICIAL = f'https://huggingface.co/{REPO}/blob/main/{NAME}'
SOURCE = f'https://hf-mirror.com/{REPO}/resolve/main/{NAME}'


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    target = ROOT / 'models' / NAME
    if target.exists():
        if target.stat().st_size == SIZE and checksum(target) == SHA256:
            print('Existing download verified:', target, flush=True)
            return
        raise RuntimeError('A different file exists at the destination; it was not overwritten.')
    if shutil.disk_usage(ROOT).free < SIZE * 2 + 512 * 1024**2:
        raise RuntimeError('Not enough free disk space for download and assembly.')
    parts_dir = ROOT / 'downloads' / ('mmproj-parts-' + SHA256[:12])
    parts_dir.mkdir(parents=True, exist_ok=True)
    count = 6
    chunk = (SIZE + count - 1) // count
    progress = [0] * count
    parts = [parts_dir / f'{index}.part' for index in range(count)]

    def download(index):
        start, end = index * chunk, min(SIZE, (index + 1) * chunk) - 1
        expected = end - start + 1
        for attempt in range(5):
            existing = parts[index].stat().st_size if parts[index].exists() else 0
            if existing > expected:
                raise RuntimeError(f'Unexpected part size: {index}')
            progress[index] = existing
            if existing == expected:
                return
            offset = start + existing
            try:
                timeout = httpx.Timeout(connect=20, read=35, write=20, pool=20)
                with httpx.Client(follow_redirects=True, trust_env=False, timeout=timeout) as client:
                    url = SOURCE + f'?download=true&part={index}&offset={offset}&attempt={attempt}'
                    with client.stream('GET', url, headers={'Range': f'bytes={offset}-{end}', 'Accept-Encoding': 'identity'}) as response:
                        response.raise_for_status()
                        if response.status_code != 206 or response.headers.get('content-range') != f'bytes {offset}-{end}/{SIZE}':
                            raise RuntimeError('Server did not honor the requested byte range')
                        with parts[index].open('ab') as output:
                            for data in response.iter_bytes(1024 * 1024):
                                if progress[index] + len(data) > expected:
                                    raise RuntimeError('Server returned too many bytes')
                                output.write(data)
                                progress[index] += len(data)
                if parts[index].stat().st_size == expected:
                    return
                raise RuntimeError('Incomplete range response')
            except (httpx.HTTPError, RuntimeError) as error:
                # Do not print signed CDN URLs from HTTP exceptions.
                print(f'Part {index + 1}: retry {attempt + 1} ({type(error).__name__})', flush=True)
                if attempt == 4:
                    raise RuntimeError(f'Part {index + 1} failed after retries') from None
                time.sleep(2)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=count) as pool:
        pending = {pool.submit(download, index) for index in range(count)}
        while pending:
            finished, pending = wait(pending, timeout=10)
            for future in finished:
                future.result()
            done = sum(progress)
            elapsed = max(time.monotonic() - started, 0.1)
            print(f'{done / SIZE:.1%} | {done / 1024**2:.0f}/{SIZE / 1024**2:.0f} MiB | {done / 1024**2 / elapsed:.1f} MiB/s', flush=True)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.gguf.partial')
    digest = hashlib.sha256()
    with temporary.open('wb') as output:
        for part in parts:
            with part.open('rb') as source:
                while data := source.read(8 * 1024**2):
                    digest.update(data)
                    output.write(data)
    if temporary.stat().st_size != SIZE or digest.hexdigest() != SHA256:
        raise RuntimeError('SHA256 mismatch; unverified file remains .partial and is not installed.')
    with temporary.open('rb') as source:
        magic, version = struct.unpack('<4sI', source.read(8))
    if magic != b'GGUF' or version != 3:
        raise RuntimeError('Unexpected GGUF header')
    if target.exists():
        raise RuntimeError('Destination appeared during download; not overwritten.')
    temporary.rename(target)
    report = {'file': str(target), 'bytes': SIZE, 'sha256': SHA256, 'gguf_version': version,
              'verified': True, 'official_file': OFFICIAL, 'download_source': SOURCE,
              'compatibility_document': 'https://github.com/leejet/stable-diffusion.cpp/blob/0518b34/docs/qwen_image_2.1.md',
              'elapsed_seconds': round(time.monotonic() - started, 1), 'inference_tested': False}
    (ROOT / 'downloads' / 'mmproj-verified.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    # Remove only the six part files created by this downloader, after verification.
    for part in parts:
        part.unlink()
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
