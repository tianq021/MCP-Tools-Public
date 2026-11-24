"""Smoke-check the installed libraries without loading image models or serving ports."""
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import secrets
import subprocess
import sys

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
import pynvml
import uvicorn

ROOT = Path(__file__).resolve().parents[1]


def main():
    result = {'python': sys.version, 'executable': sys.executable, 'checks': {}}
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
    result['checks']['pip_check'] = True
    image = Image.new('RGB', (64, 64), (24, 48, 72))
    info = PngImagePlugin.PngInfo()
    info.add_text('generation', json.dumps({'seed': 42, 'prompt': 'environment check'}))
    data = io.BytesIO()
    image.save(data, format='PNG', pnginfo=info)
    reopened = Image.open(io.BytesIO(data.getvalue()))
    assert json.loads(reopened.info['generation'])['seed'] == 42
    reopened.thumbnail((16, 16))
    assert reopened.size == (16, 16)
    result['checks']['png_metadata_and_thumbnail'] = True
    cipher = AESGCM(AESGCM.generate_key(bit_length=256))
    nonce = secrets.token_bytes(12)
    ciphertext = cipher.encrypt(nonce, data.getvalue(), b'smoke-check')
    assert cipher.decrypt(nonce, ciphertext, b'smoke-check') == data.getvalue()
    tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    try:
        cipher.decrypt(nonce, tampered, b'smoke-check')
    except InvalidTag:
        pass
    else:
        raise AssertionError('Tampering was not rejected')
    result['checks']['aes_gcm_roundtrip_and_authentication'] = True
    app = FastAPI()

    @app.post('/upload')
    async def upload(file: UploadFile):
        return {'size': len(await file.read())}

    with TestClient(app) as client:
        response = client.post('/upload', files={'file': ('test.png', data.getvalue(), 'image/png')})
        assert response.status_code == 200 and response.json()['size'] == len(data.getvalue())
    result['checks']['fastapi_httpx_multipart'] = True
    pynvml.nvmlInit()
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
        result['gpu'] = {'name': pynvml.nvmlDeviceGetName(handle), 'total_bytes': memory.total,
                         'free_bytes': memory.free, 'used_bytes': memory.used}
    finally:
        pynvml.nvmlShutdown()
    result['checks']['nvml_gpu_monitoring'] = True
    result['packages'] = {d.metadata['Name']: d.version for d in importlib.metadata.distributions()}
    wheels = []
    for path in sorted((ROOT / 'downloads/wheels').glob('*.whl')):
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        wheels.append({'filename': path.name, 'size_bytes': path.stat().st_size, 'sha256': sha})
    result['wheels'] = wheels
    result['downloaded_wheel_bytes'] = sum(w['size_bytes'] for w in wheels)
    result['real_image_inference_tested'] = False
    (ROOT / 'tests/artifacts/environment-check.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('wheels', 'packages')}, indent=2))


if __name__ == '__main__':
    main()
