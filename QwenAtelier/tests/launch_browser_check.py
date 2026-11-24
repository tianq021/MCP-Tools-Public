from pathlib import Path
import json
import subprocess

root = Path(__file__).resolve().parents[1]
artifacts = root / 'tests/artifacts'
logs = root / 'logs'
profile = artifacts / 'browser-profile'
profile.mkdir(parents=True, exist_ok=True)
logs.mkdir(exist_ok=True)
with (logs / 'browser-launch.log').open('w') as log:
    process = subprocess.Popen([
        r'C:\Program Files\Google\Chrome\Application\chrome.exe', '--headless=new',
        '--disable-gpu', '--no-first-run', '--no-default-browser-check',
        '--remote-debugging-port=0', f'--user-data-dir={profile}',
        '--window-size=1512,1100', 'about:blank'],
        creationflags=subprocess.CREATE_NO_WINDOW, stdout=log, stderr=log)
(artifacts / 'browser-process.json').write_text(json.dumps({'pid':process.pid,'profile':str(profile)}))
print('Isolated headless browser PID:', process.pid)
