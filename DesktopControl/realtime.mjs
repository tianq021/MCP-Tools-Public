import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const directory = path.dirname(fileURLToPath(import.meta.url));

export function runLocal(request, signal) {
  return new Promise(resolve => {
    const python = process.env.DESKTOP_CONTROL_PYTHON || 'C:\\Program Files\\Python310\\python.exe';
    const extraPackages = [
      path.join(directory, 'realtime', 'vendor'),
      path.join(directory, 'realtime', '.venv', 'Lib', 'site-packages'),
    ];
    const child = spawn(python, ['-B', path.join(directory, 'realtime', 'runner.py')], {
      windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONIOENCODING: 'utf-8',
        PYTHONPATH: [...extraPackages, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
    });
    let stdout = '', finished = false, killTimer;
    function cancel() {
      if (finished) return;
      child.stdin.write('cancel\n', () => {});
      killTimer ??= setTimeout(() => child.kill(), 2500);
    }
    const timer = setTimeout(cancel, 32000);
    const finish = result => {
      if (finished) return;
      finished = true;
      clearTimeout(timer); clearTimeout(killTimer);
      signal?.removeEventListener('abort', cancel);
      resolve(result);
    };
    child.stdout.on('data', chunk => {
      stdout += chunk.toString('utf8');
      if (stdout.length > 262144) cancel();
    });
    child.stderr.on('data', () => {});
    child.stdin.on('error', () => {});
    child.on('error', () => finish({ ok: false, applied: false, message: '本地控制运行时未能启动，请检查 Python 与 realtime 依赖。' }));
    child.on('close', code => {
      try {
        const result = JSON.parse(stdout.trim());
        if (typeof result?.ok !== 'boolean' || code !== 0 && result.ok) throw Error();
        finish(result);
      } catch {
        finish({ ok: false, applied: null, message: '本地控制未返回完整结果；请重新观察，不要重放输入。' });
      }
    });
    child.stdin.write(JSON.stringify(request) + '\n');
    signal?.addEventListener('abort', cancel, { once: true });
    if (signal?.aborted) cancel();
  });
}
