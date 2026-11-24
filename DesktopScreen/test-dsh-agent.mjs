import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const directory = path.dirname(fileURLToPath(import.meta.url));
const child = spawn('C:\\nodejs\\node.exe', [
  'F:\\deepseekA-020-test\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js',
  '--profile', 'headless', '--patch', path.join(directory, 'dsh-test-overlay.yml'), '--json',
], {
  cwd: 'F:\\deepseekA',
  env: { ...process.env, DSH_HOME: 'F:\\deepseekA\\dsh-home-020-20261007' },
  windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
});
child.stdin.end('这是一次已获用户授权的飞书发图测试。请只调用一次 mcp__desktop__send_screen_to_feishu，截取当前主屏幕并发送到已经配置好的飞书群。不要读取或打印任何配置文件与密钥，不调用其他发送工具，不改设置，不启动子智能体。成功后用一句话回复结果；如果失败或送达未知就停止，不重试。', 'utf8');
const summary = { sessionId: null, toolCalls: [], toolResults: [], final: '', errors: [], exitCode: null };
const lines = createInterface({ input: child.stdout });
lines.on('line', line => {
  try {
    const event = JSON.parse(line);
    if (event.type === 'session') {
      summary.sessionId = event.sessionId ?? event.id ?? null;
      console.log(JSON.stringify({ type: 'session_started' }));
    } else if (event.type === 'tool_call') {
      const name = event.name ?? event.toolName ?? event.tool ?? null;
      summary.toolCalls.push({ name, keys: Object.keys(event).filter(key => !['arguments','input'].includes(key)) });
      console.log(JSON.stringify({ type: 'tool_call', name }));
    } else if (event.type === 'tool_result') {
      // Never print canonical image bytes or arbitrary tool output.
      let delivery = null;
      try {
        const parsed = JSON.parse(event.result);
        if (typeof parsed?.sent === 'boolean') delivery = { ok: parsed.ok, sent: parsed.sent, message: parsed.message };
      } catch { /* Some result projections also include an image reference. */ }
      summary.toolResults.push({ name: event.name ?? event.toolName ?? event.tool ?? null,
        status: event.status, delivery });
      console.log(JSON.stringify({ type: 'tool_result_received', status: event.status, delivery }));
    } else if (event.type === 'final') {
      summary.final = event.text ?? event.answer ?? event.message ?? '';
      console.log(JSON.stringify({ type: 'final', text: summary.final }));
    } else if (event.type === 'error') {
      summary.errors.push({ code: event.code, message: event.message });
      console.log(JSON.stringify({ type: 'error', code: event.code, message: event.message }));
    }
  } catch { /* Non-event startup diagnostics are not forwarded. */ }
});
let stderr = '';
child.stderr.on('data', data => { stderr = (stderr + data.toString('utf8')).slice(-12000); });
const heartbeat = setInterval(() => console.log(JSON.stringify({ type: 'waiting', elapsedSeconds: Math.round((Date.now() - started) / 1000) })), 30000);
const started = Date.now();
const timer = setTimeout(() => {
  console.log(JSON.stringify({ type: 'timeout', message: 'Stopping this test; no automatic retry.' }));
  child.kill();
}, 240000);
child.on('error', () => console.log(JSON.stringify({ type: 'spawn_error' })));
child.on('close', code => {
  clearInterval(heartbeat);
  clearTimeout(timer);
  summary.exitCode = code;
  // Only a bounded, scrubbed diagnostic tail is included on a failed run.
  if (code !== 0) summary.diagnostic = stderr
    .replace(/https:\/\/open\.feishu\.cn\/open-apis\/bot\/v2\/hook\/[^\s"']+/g, '[REDACTED_WEBHOOK]')
    .replace(/(Bearer\s+)[A-Za-z0-9._-]+/gi, '$1[REDACTED]')
    .slice(-2500);
  console.log(JSON.stringify({ type: 'test_summary', ...summary }));
  process.exitCode = code === 0 ? 0 : 1;
});
