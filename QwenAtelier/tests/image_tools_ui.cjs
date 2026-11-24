/*
 * 放大 / 扩图独立模块的隔离界面检查。
 *
 * 只加载 web/image-tools.js + web/image-tools.css 和 tests/ 下的纯内存 mock：
 * 没有真实 /api、没有模型、没有 DeepSeek 请求。
 * 复用项目现有做法：Chrome headless + CDP over WebSocket，无第三方依赖。
 *
 * 运行：node tests\image_tools_ui.cjs
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {spawn} = require('node:child_process');

const root = path.resolve(__dirname, '..');
const artifacts = path.join(__dirname, 'artifacts', 'image-tools-ui');
const samplePng = path.join(artifacts, 'upload-sample.png');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

const CHROME_CANDIDATES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'
];

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png'
};

// 1×1 透明 PNG，仅用于触发上传流程；mock 不解析像素。
const SAMPLE_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+MgAAAAEAQH/1n0ZAAAAAElFTkSuQmCC';

function serve() {
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    const allowed =
      url.pathname === '/tests/image_tools_mock.html' ||
      url.pathname === '/tests/image_tools_mock.js' ||
      url.pathname === '/web/image-tools.js' ||
      url.pathname === '/web/image-tools.css';
    if (!allowed) {
      response.writeHead(404, {'Content-Type': 'text/plain'});
      response.end('not found');
      return;
    }
    const file = path.join(root, url.pathname.replace(/^\//, ''));
    const ext = path.extname(file);
    response.writeHead(200, {'Content-Type': MIME[ext] || 'application/octet-stream'});
    response.end(fs.readFileSync(file));
  });
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server)));
}

async function main() {
  fs.mkdirSync(artifacts, {recursive: true});
  fs.writeFileSync(samplePng, Buffer.from(SAMPLE_PNG_BASE64, 'base64'));

  const chrome = CHROME_CANDIDATES.find(candidate => fs.existsSync(candidate));
  if (!chrome) throw new Error('未找到 Chrome / Edge，可执行文件均不存在');

  const server = await serve();
  const base = `http://127.0.0.1:${server.address().port}/tests/image_tools_mock.html`;
  const profile = fs.mkdtempSync(path.join(root, '.tmp', 'image-tools-ui-'));
  const browser = spawn(chrome, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-extensions', '--remote-debugging-port=0',
    '--hide-scrollbars',
    `--user-data-dir=${profile}`, 'about:blank'
  ], {windowsHide: true, stdio: ['ignore', 'ignore', 'pipe']});

  let browserLog = '';
  browser.stderr.on('data', chunk => { browserLog += chunk; });

  const steps = [];
  const screenshots = [];
  const pageErrors = [];
  let ws;
  let call;
  let evaluate;
  let shot;
  let waitFor;

  try {
    const portFile = path.join(profile, 'DevToolsActivePort');
    for (let i = 0; i < 120 && !fs.existsSync(portFile); i++) await delay(100);
    if (!fs.existsSync(portFile)) throw new Error(`Headless browser 未启动：${browserLog}`);

    const port = fs.readFileSync(portFile, 'utf8').trim().split('\n')[0];
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });

    let serial = 0;
    const pending = new Map();
    ws.onmessage = event => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const entry = pending.get(message.id);
        pending.delete(message.id);
        if (entry) message.error ? entry.reject(message.error) : entry.resolve(message.result);
      } else if (message.method === 'Runtime.exceptionThrown') {
        const detail = message.params.exceptionDetails;
        pageErrors.push(detail.exception?.description || detail.text || 'unknown exception');
      } else if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
        pageErrors.push(message.params.args.map(arg => arg.value ?? arg.description ?? '').join(' '));
      }
    };
    call = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++serial;
      pending.set(id, {resolve, reject});
      ws.send(JSON.stringify({id, method, params}));
    });

    evaluate = async expression => {
      const result = await call('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
      if (result.exceptionDetails) {
        throw new Error('页面脚本异常：' + JSON.stringify(result.exceptionDetails.exception?.description || result.exceptionDetails.text));
      }
      return result.result.value;
    };

    shot = async name => {
      const result = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
      const file = path.join(artifacts, name.endsWith('.png') ? name : name + '.png');
      fs.writeFileSync(file, Buffer.from(result.data, 'base64'));
      screenshots.push(path.basename(file));
      return file;
    };

    waitFor = async (expression, timeout = 8000, label = expression) => {
      const started = Date.now();
      while (Date.now() - started < timeout) {
        if (await evaluate(expression)) return true;
        await delay(120);
      }
      throw new Error(`等待超时：${label}${pageErrors.length ? '；页面异常：' + pageErrors.join(' | ') : ''}`);
    };

    const click = selector => evaluate(
      `(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el)return false;el.click();return true;})()`
    );
    const q = selector => `document.querySelector(${JSON.stringify(selector)})`;
    const countSubstr = async (text) => evaluate(
      `(()=>{const el=${q('#it-root')};if(!el)return -1;return (el.textContent.match(/${text}/g)||[]).length;})()`
    );

    const setFile = async file => {
      const doc = await call('DOM.getDocument', {depth: -1});
      const found = await call('DOM.querySelector', {nodeId: doc.root.nodeId, selector: '#it-root input[type=file]'});
      if (!found.nodeId) throw new Error('未找到文件输入');
      await call('DOM.setFileInputFiles', {files: [file], nodeId: found.nodeId});
      await delay(300);
      // 某些版本不会自动派发 change，兜底一次（只在还没产生上传请求时）。
      const posted = await evaluate(`MockHarness.countCalls('/image-tools/inputs','POST')`);
      if (posted === 0) {
        await evaluate(`(()=>{const el=${q('#it-root input[type=file]')};el.dispatchEvent(new Event('change',{bubbles:true}));return true;})()`);
      }
    };

    const mountScenario = async (scenario, mode = 'upscale') => {
      await evaluate(`MockHarness.setMode(${JSON.stringify(mode)});MockHarness.setScenario(${JSON.stringify(scenario)});true`);
      await waitFor(`document.getElementById('it-root').classList.contains('image-tools')`);
      await waitFor(`(()=>{const b=${q('#it-root .it-head-badge')};return b&&b.textContent!=='能力待加载';})()`, 6000, '能力标签加载');
    };

    const pickFromGallery = async () => {
      await click('#it-root [data-role="open-gallery"]');
      await waitFor(`document.querySelectorAll('#it-root [data-image-id]').length>0`, 5000, '图库列表');
      await click('#it-root [data-image-id]');
      await waitFor(`!${q('#it-root .it-input-card')}.hidden`, 5000, '输入图卡片');
    };

    const fillOutpaintPrompt = async text => evaluate(
      `(()=>{const el=document.querySelectorAll('#it-root .it-textarea')[0];el.value=${JSON.stringify(text)};` +
      `el.dispatchEvent(new Event('input',{bubbles:true}));return true;})()`
    );

    const pressKey = async (key, code, vk, text) => {
      await call('Input.dispatchKeyEvent', {
        type: text ? 'keyDown' : 'rawKeyDown',
        key, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk,
        ...(text ? {text, unmodifiedText: text} : {})
      });
      await call('Input.dispatchKeyEvent', {type: 'keyUp', key, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk});
      await delay(120);
    };

    const step = async (name, fn) => {
      const record = {name, ok: false, detail: null, ms: 0};
      const started = Date.now();
      try {
        const detail = await fn();
        record.ok = true;
        record.detail = detail === undefined ? null : detail;
      } catch (error) {
        record.detail = error.message;
        try { await shot('failed-' + name.replace(/[^a-z0-9一-龥]+/gi, '-').slice(0, 40)); } catch (ignored) { /* 截图失败不掩盖原始错误 */ }
      }
      record.ms = Date.now() - started;
      steps.push(record);
      if (!record.ok) process.stdout.write(`FAIL ${name}: ${record.detail}\n`);
      else process.stdout.write(`ok   ${name} (${record.ms} ms)\n`);
      return record.ok;
    };

    // ------------------------------------------------------------------ 启动页面
    await call('Runtime.enable');
    await call('Page.enable');
    await call('DOM.enable');
    await call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false});
    await call('Page.navigate', {url: base});
    await waitFor(`typeof MockHarness==='object'`);
    try {
      await waitFor(`document.getElementById('it-root').classList.contains('image-tools')`, 5000);
    } catch (error) {
      const info = await evaluate(`({
        tools: typeof window.QwenImageTools,
        harness: typeof window.MockHarness,
        root: (document.getElementById('it-root') || {}).outerHTML ? document.getElementById('it-root').outerHTML.slice(0, 240) : 'no-root',
        cls: (document.getElementById('it-root') || {}).className
      })`);
      throw new Error(error.message + ' / ' + JSON.stringify(info));
    }

    // 1. 能力可用 + 初始阻断
    await step('初始状态：能力可用、未选图时禁止提交', async () => {
      await waitFor(`${q('#it-root .it-head-badge')}.textContent!=='能力待加载'`, 5000, '能力标签加载');
      const snapshot = await evaluate(`({
        title: ${q('#it-root .it-title')}.textContent,
        badge: ${q('#it-root .it-head-badge')}.textContent,
        startDisabled: ${q('#it-root [data-role="start-job"]')}.disabled,
        blocked: ${q('#it-root .it-blocked')}.textContent,
        models: [...document.querySelectorAll('#it-root select option')].map(o=>({v:o.value,t:o.textContent})),
        staticFormat: ${q('#it-root .it-panel .it-static-value')}.textContent
      })`);
      assert.equal(snapshot.title, 'AI 放大');
      assert.match(snapshot.badge, /本机已验证/);
      assert.equal(snapshot.startDisabled, true);
      assert.match(snapshot.blocked, /请先选择一张图片/);
      assert.equal(snapshot.models.filter(model => model.v === 'realesrgan_anime_6b').length, 1);
      assert.equal(snapshot.models.some(model => model.v === 'pending_upstream'), false, '未验证模型不应出现在下拉框');
      assert.match(snapshot.staticFormat, /8 bit/);
      await shot('upscale-empty-dark-1440');
      return snapshot;
    });

    // 2. 能力未验证
    await step('能力未验证：提交禁用并显示原因', async () => {
      await mountScenario('unverified');
      const snapshot = await evaluate(`({
        badge: ${q('#it-root .it-head-badge')}.textContent,
        startDisabled: ${q('#it-root [data-role="start-job"]')}.disabled,
        blocked: ${q('#it-root .it-blocked')}.textContent
      })`);
      assert.match(snapshot.badge, /待本机验证/);
      assert.equal(snapshot.startDisabled, true);
      assert.match(snapshot.blocked, /待本机验证/);
      return snapshot;
    });

    // 3. 能力接口失败
    await step('能力接口失败：不禁用页面，但禁止提交并显示错误', async () => {
      await evaluate(`MockHarness.setScenario('capability-fail');true`);
      await delay(600);
      const snapshot = await evaluate(`({
        note: ${q('#it-root .it-note')}.textContent,
        startDisabled: ${q('#it-root [data-role="start-job"]')}.disabled,
        badge: ${q('#it-root .it-head-badge')}.textContent
      })`);
      assert.match(snapshot.note, /模拟能力接口失败/);
      assert.equal(snapshot.startDisabled, true);
      return snapshot;
    });

    // 4. 模型列表为空
    await step('模型列表为空：下拉框禁用并给出提示', async () => {
      await mountScenario('empty-models');
      const snapshot = await evaluate(`({
        disabled: ${q('#it-root select')}.disabled,
        option: ${q('#it-root select option')}.textContent,
        startDisabled: ${q('#it-root [data-role="start-job"]')}.disabled,
        blocked: ${q('#it-root .it-blocked')}.textContent
      })`);
      assert.equal(snapshot.disabled, true);
      assert.match(snapshot.option, /暂无已验证的放大模型/);
      assert.equal(snapshot.startDisabled, true);
      assert.match(snapshot.blocked, /暂无可用的已验证放大模型/);
      return snapshot;
    });

    // 5. 上传失败：清理旧选择
    await step('上传失败：清空旧选择并报错（不残留预览）', async () => {
      await mountScenario('upload-fail');
      await setFile(samplePng);
      await waitFor(`MockHarness.notifyLog().some(m=>m.includes('模拟上传失败'))`, 5000, '上传失败提示');
      const snapshot = await evaluate(`({
        cardHidden: ${q('#it-root .it-input-card')}.hidden,
        thumbSrc: ${q('#it-root .it-thumb-img')}.getAttribute('src'),
        note: ${q('#it-root .it-note')}.textContent,
        startDisabled: ${q('#it-root [data-role="start-job"]')}.disabled
      })`);
      assert.equal(snapshot.cardHidden, true);
      assert.equal(snapshot.thumbSrc, null);
      assert.match(snapshot.note, /模拟上传失败/);
      assert.equal(snapshot.startDisabled, true);
      return snapshot;
    });

    // 6. 图库选择 + 键盘
    await step('从图库选择：排除已删除图片，显示名称/尺寸/大小', async () => {
      await mountScenario('stable');
      await click('#it-root [data-role="open-gallery"]');
      await waitFor(`document.querySelectorAll('#it-root [data-image-id]').length>0`, 5000, '图库列表');
      const list = await evaluate(`([...document.querySelectorAll('#it-root [data-image-id]')].map(b=>b.getAttribute('data-image-id')))`);
      assert.deepEqual(list, ['img_mock_001', 'img_mock_002']);
      assert.equal(list.includes('img_mock_deleted'), false);
      await click('#it-root [data-image-id]');
      await waitFor(`!${q('#it-root .it-input-card')}.hidden`, 5000, '输入卡片');
      const snapshot = await evaluate(`({
        name: ${q('#it-root .it-input-name')}.textContent,
        meta: ${q('#it-root .it-input-meta')}.textContent,
        bits: ${q('#it-root .it-input-bits')}.textContent,
        thumb: ${q('#it-root .it-thumb-img')}.getAttribute('src').slice(0,15),
        galleryHidden: ${q('#it-root .it-gallery')}.hidden
      })`);
      assert.equal(snapshot.name, '合成示例-风景.png');
      assert.match(snapshot.meta, /512 × 512/);
      assert.match(snapshot.meta, /KB/);
      assert.match(snapshot.bits, /8 bit/);
      assert.equal(snapshot.thumb, 'data:image/svg+');
      assert.equal(snapshot.galleryHidden, true);
      return snapshot;
    });

    await step('键盘：Tab 可达、Enter 打开图库并选择图片、Esc 收起', async () => {
      await evaluate(`(()=>{const b=${q('#it-root [data-role="open-gallery"]')};b.focus();return document.activeElement===b;})()`);
      assert.equal(await evaluate(`document.activeElement.getAttribute('data-role')`), 'open-gallery');
      await pressKey('Enter', 'Enter', 13, '\r');
      await waitFor(`!${q('#it-root .it-gallery')}.hidden`, 4000, '回车打开图库');
      await evaluate(`(()=>{const item=${q('#it-root [data-image-id]')};item.focus();return document.activeElement===item;})()`);
      await pressKey('Enter', 'Enter', 13, '\r');
      await waitFor(`!${q('#it-root .it-input-card')}.hidden`, 5000, '回车选择图片');
      // Esc 收起（重新打开后测试）
      await click('#it-root [data-role="open-gallery"]');
      await waitFor(`!${q('#it-root .it-gallery')}.hidden`, 4000, '图库再次打开');
      await evaluate(`(()=>{${q('#it-root [data-role="open-gallery"]')}.focus();return true})()`);
      await pressKey('Escape', 'Escape', 27);
      await delay(200);
      const state = await evaluate(`({hidden:${q('#it-root .it-gallery')}.hidden,focus:document.activeElement.getAttribute('data-role')})`);
      assert.equal(state.hidden, true);
      assert.equal(state.focus, 'open-gallery');
      return state;
    });

    // 7. 放大参数与估算
    await step('放大参数：倍率来自服务端、估算标注、非法 tile 阻断', async () => {
      const tips = await evaluate(`(()=>[...document.querySelectorAll('#it-root .it-help')].map(h=>({focusable:h.tabIndex===0,tip:h.querySelector('.it-tip')?.textContent||''})))()`);
      assert.equal(tips.length, 2);
      assert.match(tips[0].tip, /32 的倍数/);
      assert.match(tips[1].tip, /16×/);
      await evaluate(`${q('#it-root .it-help')}.focus();true`);
      await delay(80);
      const tipShown = await evaluate(`getComputedStyle(${q('#it-root .it-help .it-tip')}).display`);
      assert.equal(tipShown, 'block');
      await evaluate(`document.activeElement.blur();true`);
      const estimate = await evaluate(`${q('#it-root .it-estimate')}.textContent`);
      assert.match(estimate, /2048 × 2048/);
      assert.match(estimate, /估算/);
      assert.match(estimate, /显存约/);
      assert.match(estimate, /内存约/);
      await evaluate(`(()=>{const el=${q('#it-root input[type=number]')};el.value='100';el.dispatchEvent(new Event('input',{bubbles:true}));return true;})()`);
      await delay(120);
      const invalid = await evaluate(`({disabled:${q('#it-root [data-role="start-job"]')}.disabled,blocked:${q('#it-root .it-blocked')}.textContent})`);
      assert.equal(invalid.disabled, true);
      assert.match(invalid.blocked, /32 的倍数/);
      await evaluate(`(()=>{const el=${q('#it-root input[type=number]')};el.value='128';el.dispatchEvent(new Event('input',{bubbles:true}));return true;})()`);
      await delay(120);
      const valid = await evaluate(`({disabled:${q('#it-root [data-role="start-job"]')}.disabled,blocked:${q('#it-root .it-blocked')}.textContent})`);
      assert.equal(valid.disabled, false);
      assert.match(valid.blocked, /可以提交/);
      await shot('upscale-ready-dark-1440');
      return {estimate, invalid, valid};
    });

    // 8. 重复提交
    await step('重复提交：连点两次只发一个任务', async () => {
      await evaluate(`MockHarness.clearCalls();(()=>{const b=${q('#it-root [data-role="start-job"]')};b.click();b.click();return true;})()`);
      await delay(600);
      const posted = await evaluate(`MockHarness.countCalls('/image-tools/jobs','POST')`);
      assert.equal(posted, 1);
      const badge = await evaluate(`${q('#it-root .it-job-panel .it-badge')}.textContent`);
      assert.match(badge, /排队中|运行中/);
      return {posted, badge};
    });

    // 9. 提交失败
    await step('请求失败：提交失败后按钮恢复并显示原因', async () => {
      await mountScenario('submit-fail');
      await pickFromGallery();
      await evaluate(`MockHarness.clearCalls();${q('#it-root [data-role="start-job"]')}.click();true`);
      await waitFor(`MockHarness.notifyLog().some(m=>m.includes('模拟提交失败'))`, 5000, '提交失败提示');
      const snapshot = await evaluate(`({
        disabled: ${q('#it-root [data-role="start-job"]')}.disabled,
        note: ${q('#it-root .it-note')}.textContent,
        badge: ${q('#it-root .it-job-panel .it-badge')}.textContent
      })`);
      assert.equal(snapshot.disabled, false);
      assert.match(snapshot.note, /模拟提交失败/);
      assert.equal(snapshot.badge, '未提交');
      return snapshot;
    });

    // 10. 排队中取消
    await step('排队中取消：状态以服务端返回为准（已取消）', async () => {
      await mountScenario('queued-cancel');
      await pickFromGallery();
      await click('#it-root [data-role="start-job"]');
      await waitFor(`${q('#it-root .it-job-panel .it-badge')}.textContent.indexOf('排队中')>=0`, 5000, '排队状态');
      await click('#it-root [data-role="cancel-job"]');
      await waitFor(`${q('#it-root .it-job-panel .it-badge')}.textContent.indexOf('已取消')>=0`, 5000, '取消完成');
      const snapshot = await evaluate(`({
        badge: ${q('#it-root .it-job-panel .it-badge')}.textContent,
        cancelHidden: ${q('#it-root [data-role="cancel-job"]')}.hidden,
        calls: MockHarness.calls().filter(c=>c.path.includes('/cancel')).length
      })`);
      assert.equal(snapshot.badge, '已取消');
      assert.equal(snapshot.cancelHidden, true);
      assert.equal(snapshot.calls, 1);
      return snapshot;
    });

    // 11. 运行中取消仍等待
    await step('运行中取消：保持运行 + 取消请求中，不自行改成已取消', async () => {
      await mountScenario('running-cancel');
      await pickFromGallery();
      await click('#it-root [data-role="start-job"]');
      await waitFor(`${q('#it-root .it-job-panel .it-badge')}.textContent.indexOf('运行中')>=0`, 6000, '运行状态');
      const progress = await evaluate(`${q('#it-root .it-progress')}.hidden?'':document.querySelector('#it-root .it-job-panel .it-mini').textContent`);
      assert.match(progress, /进度 40%/);
      await click('#it-root [data-role="cancel-job"]');
      await waitFor(`${q('#it-root .it-job-panel .it-badge')}.textContent.indexOf('取消请求中')>=0`, 6000, '取消请求中');
      const after = await evaluate(`({
        badge: ${q('#it-root .it-job-panel .it-badge')}.textContent,
        stage: ${q('#it-root .it-job-stage')}.textContent,
        cancelDisabled: ${q('#it-root [data-role="cancel-job"]')}.disabled
      })`);
      assert.match(after.stage, /等待|取消请求已提交/);
      assert.equal(after.cancelDisabled, true);
      await delay(3200);
      const still = await evaluate(`${q('#it-root .it-job-panel .it-badge')}.textContent`);
      assert.equal(still, '取消请求中', '不能自行标记为已取消');
      return {after, still};
    });

    // 12. 失败任务：无下载 + HTML 注入
    await step('失败任务：无下载入口，错误文本按纯文本显示', async () => {
      await mountScenario('failed-job');
      await pickFromGallery();
      await click('#it-root [data-role="start-job"]');
      await waitFor(`${q('#it-root .it-job-panel .it-badge')}.textContent.indexOf('失败')>=0`, 9000, '失败状态');
      const snapshot = await evaluate(`({
        resultHidden: ${q('#it-root .it-result')}.hidden,
        links: [...document.querySelectorAll('#it-root .it-result a')].filter(a=>!a.hidden).length,
        error: ${q('#it-root .it-job .it-error')}.textContent,
        injectedImg: document.querySelectorAll('#it-root img[src="x"]').length,
        xss: typeof window.__xss,
        errorHtml: ${q('#it-root .it-job .it-error')}.children.length
      })`);
      assert.equal(snapshot.resultHidden, true);
      assert.equal(snapshot.links, 0);
      assert.match(snapshot.error, /<img src=x/);
      assert.equal(snapshot.injectedImg, 0);
      assert.equal(snapshot.xss, 'undefined');
      assert.equal(snapshot.errorHtml, 0);
      return snapshot;
    });

    // 13. 扩图：几何预览 + 默认严格保留 + 完成下载
    await step('扩图：服务端几何预览、严格原图保护、完成可下载', async () => {
      await mountScenario('completed-job', 'outpaint');
      await pickFromGallery();
      await fillOutpaintPrompt('延伸两侧的建筑与天空，保持主体不变');
      await waitFor(`${q('#it-root .it-preview-panel .it-panel-head .it-mini')}.textContent==='几何已确认'`, 6000, '几何预览');
      const geometry = await evaluate(`${q('#it-root .it-preview-panel p.it-estimate')}.textContent`);
      assert.match(geometry, /目标画布 768 × 512/);
      assert.match(geometry, /偏移 x 128/);
      const beforeConfirm = await evaluate(`({
        disabled: ${q('#it-root [data-role="start-job"]')}.disabled,
        blocked: ${q('#it-root .it-blocked')}.textContent,
        keepDisabled: document.querySelectorAll('#it-root input[type=checkbox]')[0].disabled,
        keepChecked: document.querySelectorAll('#it-root input[type=checkbox]')[0].checked
      })`);
      assert.equal(beforeConfirm.disabled, true);
      assert.match(beforeConfirm.blocked, /内容确认/);
      assert.equal(beforeConfirm.keepDisabled, true);
      assert.equal(beforeConfirm.keepChecked, true);
      await evaluate(`(()=>{const c=${q('#it-root .it-actions input[type=checkbox]')};c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}));return true;})()`);
      await delay(150);
      assert.equal(await evaluate(`${q('#it-root [data-role="start-job"]')}.disabled`), false);
      await click('#it-root [data-role="start-job"]');
      await waitFor(`${q('#it-root .it-job-panel .it-badge')}.textContent.indexOf('已完成')>=0`, 12000, '扩图完成');
      const snapshot = await evaluate(`({
        resultHidden: ${q('#it-root .it-result')}.hidden,
        preservation: ${q('#it-root .it-preservation')}.textContent,
        downloads: [...document.querySelectorAll('#it-root .it-result a')].map(a=>({text:a.textContent,href:a.getAttribute('href'),hidden:a.hidden})),
        compare: document.querySelectorAll('#it-root .it-compare img').length
      })`);
      assert.equal(snapshot.resultHidden, false);
      assert.match(snapshot.preservation, /原图像素已保护/);
      assert.equal(snapshot.compare, 2);
      const visible = snapshot.downloads.filter(link => !link.hidden);
      assert.equal(visible.length, 3);
      visible.forEach(link => assert.match(link.href, /^\/api\//, `${link.text} 必须是本机 /api/ 路径`));
      const manifest = visible.find(link => link.text.includes('run.json'));
      assert.match(manifest.href, /manifest$/);
      await shot('outpaint-completed-dark-1440');
      return snapshot;
    });

    // 14. 原图保护未通过
    await step('原图保护未通过：不得显示"已保护"', async () => {
      await mountScenario('unverified-preservation', 'outpaint');
      await pickFromGallery();
      await fillOutpaintPrompt('延伸两侧建筑');
      await waitFor(`${q('#it-root .it-preview-panel .it-panel-head .it-mini')}.textContent==='几何已确认'`, 6000, '几何预览');
      await evaluate(`(()=>{const c=${q('#it-root .it-actions input[type=checkbox]')};c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
      await click('#it-root [data-role="start-job"]');
      await waitFor(`${q('#it-root .it-job-panel .it-badge')}.textContent.indexOf('已完成')>=0`, 12000, '任务完成');
      const text = await evaluate(`${q('#it-root .it-preservation')}.textContent`);
      assert.doesNotMatch(text, /原图像素已保护/);
      assert.match(text, /差异像素 37/);
      return {preservation: text};
    });

    // 15. 预览乱序
    await step('预览乱序：只接纳最后一轮响应，旧预览不能提交', async () => {
      await mountScenario('preview-race', 'outpaint');
      await pickFromGallery();
      await fillOutpaintPrompt('乱序测试');
      const leftInput = `document.querySelectorAll('#it-root .it-four input')[0]`;
      await evaluate(`(()=>{const el=${leftInput};el.value='256';el.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
      await delay(340);
      await evaluate(`(()=>{const el=${leftInput};el.value='32';el.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
      const pending = await evaluate(`({disabled:${q('#it-root [data-role="start-job"]')}.disabled,blocked:${q('#it-root .it-blocked')}.textContent,state:${q('#it-root .it-preview-panel .it-panel-head .it-mini')}.textContent})`);
      assert.equal(pending.disabled, true);
      assert.match(pending.blocked, /画布预览/);
      await waitFor(`${q('#it-root .it-preview-panel .it-panel-head .it-mini')}.textContent==='几何已确认'`, 6000, '几何确认');
      const first = await evaluate(`${q('#it-root .it-preview-panel .it-estimate')}.textContent`);
      assert.match(first, /偏移 x 32/);
      await delay(900); // 让被淘汰的第一轮响应到达
      const second = await evaluate(`${q('#it-root .it-preview-panel .it-estimate')}.textContent`);
      assert.match(second, /偏移 x 32/);
      assert.doesNotMatch(second, /偏移 x 256/);
      const after = await evaluate(`({disabled:${q('#it-root [data-role="start-job"]')}.disabled,blocked:${q('#it-root .it-blocked')}.textContent})`);
      assert.match(after.blocked, /内容确认/);
      return {pending, first, second, after};
    });

    // 16. destroy 后停止轮询
    await step('destroy：清理 DOM 与计时器，之后不再轮询', async () => {
      await mountScenario('running-cancel');
      await pickFromGallery();
      await click('#it-root [data-role="start-job"]');
      await waitFor(`${q('#it-root .it-job-panel .it-badge')}.textContent.indexOf('运行中')>=0`, 6000, '运行状态');
      const before = await evaluate(`MockHarness.countCalls('/image-tools/jobs','GET')`);
      await evaluate(`MockHarness.destroy();true`);
      const emptied = await evaluate(`({children:${q('#it-root')}.children.length,cls:${q('#it-root')}.className,api:document.getElementById('it-root').classList.contains('image-tools')})`);
      assert.equal(emptied.children, 0);
      assert.equal(emptied.api, false);
      const callsAtDestroy = await evaluate(`MockHarness.calls().length`);
      await delay(3200);
      const after = await evaluate(`MockHarness.countCalls('/image-tools/jobs','GET')`);
      assert.equal(after, before, 'destroy 后不应继续轮询');
      const totalNow = await evaluate(`MockHarness.calls().length`);
      assert.equal(totalNow, callsAtDestroy);
      return {before, after, callsAtDestroy};
    });

    // 17. 375px 与浅色主题
    await step('375px 布局与浅色主题：无横向溢出、关键控件可见', async () => {
      await mountScenario('stable', 'outpaint');
      await pickFromGallery();
      await fillOutpaintPrompt('手机布局检查');
      await call('Emulation.setDeviceMetricsOverride', {width: 375, height: 812, deviceScaleFactor: 2, mobile: true});
      await delay(400);
      const layout = await evaluate(`({
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
        submitVisible: !!document.querySelector('#it-root [data-role="start-job"]') && document.querySelector('#it-root [data-role="start-job"]').getBoundingClientRect().height>0,
        promptWidth: document.querySelectorAll('#it-root .it-textarea')[0].getBoundingClientRect().width,
        gridColumns: getComputedStyle(document.querySelector('#it-root .it-grid')).gridTemplateColumns.split(' ').length
      })`);
      assert.ok(layout.scrollWidth <= 375, `出现横向溢出：${layout.scrollWidth} > 375`);
      assert.equal(layout.submitVisible, true);
      assert.ok(layout.promptWidth <= 343, `提示词框超出视口：${layout.promptWidth}`);
      assert.equal(layout.gridColumns, 1);
      await shot('outpaint-mobile-375');
      await evaluate(`document.body.classList.add('light');true`);
      await delay(250);
      await shot('upscale-mobile-375-light');
      const light = await evaluate(`({bg:getComputedStyle(document.querySelector('#it-root .it-panel')).backgroundColor,text:getComputedStyle(document.querySelector('#it-root')).color})`);
      return {layout, light};
    });

    // 18. 过滤其它工作流的任务
    await step('历史隔离：只显示当前工作流的任务', async () => {
      await mountScenario('stable', 'upscale');
      await evaluate(`MockHarness.state.jobs=[{
        id:'job_other_type',task_type:'outpaint',status:'completed',stage:'已完成',progress:1,
        created:Date.now()/1000,started:null,finished:Date.now()/1000,error:null,
        cancellation_requested:false,
        input:{id:'other-in',name:'other.png',width:512,height:512,preview_url:'/api/images/other-in/file'},
        params:{},result:{image_id:'other-out',width:768,height:512,
          preview_url:'/api/images/other-out/file',download_url:'/api/images/other-out/file?download=true',
          manifest_url:'/api/image-tools/jobs/job_other_type/manifest',preservation:null}
      }];true`);
      const before = await evaluate(`MockHarness.countCalls('/image-tools/jobs','GET')`);
      await click('#it-root [data-role="refresh-history"]');
      await waitFor(`MockHarness.countCalls('/image-tools/jobs','GET')>${before}`, 5000, '刷新任务列表');
      await delay(200);
      const snapshot = await evaluate(`({
        history: ${q('#it-root .it-history')}.textContent,
        resultHidden: ${q('#it-root .it-result')}.hidden,
        badge: ${q('#it-root .it-job-panel .it-badge')}.textContent
      })`);
      assert.match(snapshot.history, /还没有任务记录/);
      assert.equal(snapshot.resultHidden, true);
      assert.equal(snapshot.badge, '未提交');
      return snapshot;
    });

    // 20. 历史结果不被当前表单图片冒充
    await step('历史结果：完成 A 后选 B，查看 A 仍用 A 的原图与下载地址', async () => {
      await mountScenario('completed-job', 'outpaint');
      await pickFromGallery(); // A = 512×512 风景
      await fillOutpaintPrompt('历史 A 的提示词');
      await waitFor(`${q('#it-root .it-preview-panel .it-panel-head .it-mini')}.textContent==='几何已确认'`, 6000, '几何预览');
      await evaluate(`(()=>{const c=${q('#it-root .it-actions input[type=checkbox]')};c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
      await click('#it-root [data-role="start-job"]');
      await waitFor(`${q('#it-root .it-job-panel .it-badge')}.textContent.indexOf('已完成')>=0`, 12000, '任务 A 完成');
      const jobA = await evaluate(`(()=>{const j=MockHarness.state.jobs[0];return {
        id:j.id, preview:j.input.preview_url, download:j.input.download_url, name:j.input.name};})()`);

      // 表单改选 B（384×640 竖图），不再动任务 A
      await click('#it-root [data-role="open-gallery"]');
      await waitFor(`document.querySelectorAll('#it-root [data-image-id]').length>0`, 5000, '图库列表');
      await evaluate(`(()=>{document.querySelectorAll('#it-root [data-image-id]')[1].click();return true})()`);
      await waitFor(`${q('#it-root .it-input-name')}.textContent.indexOf('竖图')>=0`, 5000, '切换为图片 B');

      // 回到历史任务 A
      await evaluate(`(()=>{document.querySelectorAll('#it-root .it-history-item')[0].click();return true})()`);
      await delay(250);
      const shown = await evaluate(`({
        formName: ${q('#it-root .it-input-name')}.textContent,
        resultSrc: document.querySelectorAll('#it-root .it-compare img')[0].getAttribute('src'),
        originalHref: document.querySelectorAll('#it-root .it-result a')[0].getAttribute('href'),
        resultHidden: ${q('#it-root .it-result')}.hidden
      })`);
      assert.match(shown.formName, /竖图/, '表单应仍是用户新选的 B');
      assert.equal(shown.resultHidden, false);
      assert.equal(shown.resultSrc, jobA.preview, '结果区必须显示任务 A 的原图');
      assert.equal(shown.originalHref, jobA.download, '原图下载必须是任务 A 的地址');
      return {jobA, shown};
    });

    // 21. 原图保护严格判定
    await step('原图保护：null / 空字符串 / 缺字段 / 字符串零 一律未验证', async () => {
      await mountScenario('stable', 'outpaint');
      await evaluate(`(()=>{
        const base=(id,preservation)=>({id,task_type:'outpaint',status:'completed',stage:'已完成',progress:1,
          created:Date.now()/1000,started:null,finished:Date.now()/1000,error:null,cancellation_requested:false,
          input:{id:'in-'+id,name:id+'.png',width:512,height:512,
            preview_url:'/api/images/in-'+id+'/file',download_url:'/api/image-tools/inputs/in-'+id+'/download'},
          params:{},result:{image_id:'out-'+id,width:768,height:512,
            preview_url:'/api/images/out-'+id+'/file',download_url:'/api/images/out-'+id+'/file?download=true',
            manifest_url:'/api/image-tools/jobs/'+id+'/manifest',preservation}});
        MockHarness.state.jobs=[
          base('p_null',{verified:true,different_pixels:null}),
          base('p_empty',{verified:true,different_pixels:''}),
          base('p_missing',{verified:true}),
          base('p_strzero',{verified:true,different_pixels:'0'})
        ];
        return true;})()`);
      const before = await evaluate(`MockHarness.countCalls('/image-tools/jobs','GET')`);
      await click('#it-root [data-role="refresh-history"]');
      await waitFor(`MockHarness.countCalls('/image-tools/jobs','GET')>${before}`, 5000, '刷新任务列表');
      await delay(200);
      const texts = {};
      for (const id of ['p_null', 'p_empty', 'p_missing', 'p_strzero']) {
        await evaluate(`(()=>{document.querySelector('#it-root .it-history-item[data-job-id="${id}"]').click();return true})()`);
        await delay(120);
        texts[id] = await evaluate(`${q('#it-root .it-preservation')}.textContent`);
        assert.doesNotMatch(texts[id], /原图像素已保护/, `${id} 不能显示已保护`);
        assert.match(texts[id], /未验证/, `${id} 应显示未验证`);
      }
      return texts;
    });

    // 22. 原图下载缺失必须隐藏，不能用预览替代
    await step('原图下载：仅用 job.input.download_url，缺失则隐藏', async () => {
      await evaluate(`(()=>{
        MockHarness.state.jobs=[{id:'p_nodl',task_type:'outpaint',status:'completed',stage:'已完成',progress:1,
          created:Date.now()/1000,started:null,finished:Date.now()/1000,error:null,cancellation_requested:false,
          input:{id:'in-nodl',name:'无下载地址.png',width:512,height:512,
            preview_url:'/api/images/in-nodl/file'},
          params:{},result:{image_id:'out-nodl',width:768,height:512,
            preview_url:'/api/images/out-nodl/file',download_url:'/api/images/out-nodl/file?download=true',
            manifest_url:'/api/image-tools/jobs/p_nodl/manifest',
            preservation:{verified:true,different_pixels:0}}}];
        return true;})()`);
      const before = await evaluate(`MockHarness.countCalls('/image-tools/jobs','GET')`);
      await click('#it-root [data-role="refresh-history"]');
      await waitFor(`MockHarness.countCalls('/image-tools/jobs','GET')>${before}`, 5000, '刷新任务列表');
      await delay(200);
      await evaluate(`(()=>{document.querySelector('#it-root .it-history-item[data-job-id="p_nodl"]').click();return true})()`);
      await delay(150);
      const snapshot = await evaluate(`(()=>{
        const links=[...document.querySelectorAll('#it-root .it-result a')];
        const original=links[0];
        return {originalHidden:original.hidden, originalHref:original.getAttribute('href'),
          visible:links.filter(a=>!a.hidden).map(a=>a.textContent), preservation:${q('#it-root .it-preservation')}.textContent};
      })()`);
      assert.equal(snapshot.originalHidden, true);
      assert.equal(snapshot.originalHref, null, '缺少 download_url 时不能回落到预览地址');
      assert.equal(snapshot.visible.length, 2);
      assert.match(snapshot.preservation, /原图像素已保护/);
      return snapshot;
    });

    // 23. 保存默认参数保留严格模式 + 内容确认文案一致
    await step('保存默认参数：保留 keep_original_pixels，且不触发任务', async () => {
      await mountScenario('stable', 'outpaint');
      const copy = await evaluate(`${q('#it-root .it-actions .it-check strong')}.textContent`);
      assert.equal(copy, '遵守内容约定：不生成极端血腥或用于欺骗他人的内容。');
      await click('#it-root [data-role="save-defaults"]');
      await waitFor(`MockHarness.calls().some(c=>c.path==='/image-tools/defaults/outpaint'&&c.method==='PUT')`, 5000, '保存默认参数请求');
      const snapshot = await evaluate(`(()=>{
        const puts=MockHarness.calls().filter(c=>c.path==='/image-tools/defaults/outpaint'&&c.method==='PUT');
        return {body:puts[puts.length-1].body, posts:MockHarness.countCalls('/image-tools/jobs','POST')};
      })()`);
      assert.equal(snapshot.body.keep_original_pixels, true);
      assert.equal(Object.prototype.hasOwnProperty.call(snapshot.body, 'output_format'), true);
      assert.equal(snapshot.posts, 0);
      return {copy, ...snapshot};
    });

    // 24. 16 bit 输入阻断与恢复
    await step('16 bit 输入：禁用提交，换成 8 bit 后恢复', async () => {
      await mountScenario('sixteen-bit-input', 'upscale');
      await click('#it-root [data-role="open-gallery"]');
      await waitFor(`document.querySelectorAll('#it-root [data-image-id]').length>0`, 5000, '图库列表');
      await click('#it-root [data-image-id="img_mock_16bit"]');
      await waitFor(`!${q('#it-root .it-input-card')}.hidden`, 5000, '16 bit 输入卡片');
      const blocked = await evaluate(`({
        bits: ${q('#it-root .it-input-bits')}.textContent,
        disabled: ${q('#it-root [data-role="start-job"]')}.disabled,
        reason: ${q('#it-root .it-blocked')}.textContent,
        title: ${q('#it-root [data-role="start-job"]')}.title
      })`);
      assert.match(blocked.bits, /16 bit/);
      assert.equal(blocked.disabled, true);
      assert.match(blocked.reason, /只支持 8 bit 输入/);
      assert.match(blocked.title, /只支持 8 bit 输入/);

      await click('#it-root [data-role="open-gallery"]');
      await waitFor(`document.querySelectorAll('#it-root [data-image-id]').length>0`, 5000, '图库列表');
      await click('#it-root [data-image-id="img_mock_001"]');
      await waitFor(`${q('#it-root .it-input-bits')}.textContent.indexOf('8 bit')>=0`, 5000, '切换为 8 bit');
      await delay(200);
      const recovered = await evaluate(`({
        disabled: ${q('#it-root [data-role="start-job"]')}.disabled,
        reason: ${q('#it-root .it-blocked')}.textContent
      })`);
      assert.equal(recovered.disabled, false);
      assert.match(recovered.reason, /可以提交/);
      return {blocked, recovered};
    });

    // 25. 透明输入不再被拒绝：提示展平策略且允许提交
    await step('透明输入：显示展平说明，不再禁用提交', async () => {
      await mountScenario('alpha-input', 'upscale');
      await click('#it-root [data-role="open-gallery"]');
      await waitFor(`document.querySelectorAll('#it-root [data-image-id]').length>0`, 5000, '图库列表');
      await click('#it-root [data-image-id="img_mock_alpha"]');
      await waitFor(`!${q('#it-root .it-input-card')}.hidden`, 5000, '透明输入卡片');
      const noise = await evaluate(`({
        hint: ${q('#it-root [data-role="input-hint"]')}.textContent,
        disabled: ${q('#it-root [data-role="start-job"]')}.disabled,
        reason: ${q('#it-root .it-blocked')}.textContent
      })`);
      assert.match(noise.hint, /丢弃 alpha/);
      assert.equal(noise.disabled, false);
      assert.match(noise.reason, /可以提交/);

      await click('#it-root [data-role="open-gallery"]');
      await waitFor(`document.querySelectorAll('#it-root [data-image-id]').length>0`, 5000, '图库列表');
      await click('#it-root [data-image-id="img_mock_cutout"]');
      await waitFor(`${q('#it-root [data-role="input-hint"]')}.textContent.indexOf('白色背景')>=0`, 5000, '透明合成提示');
      const cutout = await evaluate(`({
        hint: ${q('#it-root [data-role="input-hint"]')}.textContent,
        disabled: ${q('#it-root [data-role="start-job"]')}.disabled
      })`);
      assert.match(cutout.hint, /合成到白色背景/);
      assert.equal(cutout.disabled, false);
      return {noise, cutout};
    });

    await step('模型可手动加载、释放，页面刷新不重放加载', async () => {
      await mountScenario('stable', 'upscale');
      await waitFor(`!${q('#it-root [data-role="model-load"]')}.disabled`, 5000, '模型加载按钮');
      await click('#it-root [data-role="model-load"]');
      await waitFor(`!${q('#it-root [data-role="model-release"]')}.disabled`, 5000, '模型已加载');
      assert.equal(await evaluate(`MockHarness.countCalls('/image-tools/models/upscale/load','POST')`),1);
      await evaluate('MockHarness.mount()');
      await waitFor(`!${q('#it-root [data-role="model-release"]')}.disabled`,5000,'重挂载读取状态');
      assert.equal(await evaluate(`MockHarness.countCalls('/image-tools/models/upscale/load','POST')`),1);
      await click('#it-root [data-role="model-release"]');
      await waitFor(`${q('#it-root [data-role="model-release"]')}.disabled && !${q('#it-root [data-role="model-load"]')}.disabled`,5000,'释放完成');
      assert.equal(await evaluate(`MockHarness.countCalls('/image-tools/models/upscale/release','POST')`),1);
    });

    await step('资源不足时不加载模型、不提交任务，并可取消提示', async () => {
      await mountScenario('stable','upscale');
      await evaluate(`MockHarness.state.scenario='resource-low'`);
      await waitFor(`!${q('#it-root [data-role="model-load"]')}.disabled`,5000,'加载按钮');
      await click('#it-root [data-role="model-load"]');
      await waitFor(`!${q('#it-root [data-role="resource-cancel"]')}.hidden`,5000,'取消入口');
      assert.equal(await evaluate(`MockHarness.countCalls('/image-tools/models/upscale/load','POST')`),0);
      assert.equal(await evaluate(`MockHarness.countCalls('/image-tools/jobs','POST')`),0);
      await click('#it-root [data-role="resource-cancel"]');
      assert.equal(await evaluate(`${q('#it-root [data-role="resource-cancel"]')}.hidden`),true);
    });

    await step('资源不足仅提示，扩图仍可直接提交', async () => {
      await mountScenario('stable','outpaint');
      await pickFromGallery();
      await fillOutpaintPrompt('extend');
      await evaluate(`(()=>{const c=document.querySelector('#it-root [id$="confirm"]');c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
      await evaluate(`MockHarness.state.scenario='resource-low'`);
      await waitFor(`!${q('#it-root [data-role="start-job"]')}.disabled`,5000,'提交按钮可用');
      await click('#it-root [data-role="start-job"]');
      await waitFor(`MockHarness.state.jobs.length>0`,5000,'资源提示后仍提交');
      const lowParams=await evaluate('MockHarness.state.jobs[0].params');
      assert.equal(lowParams.force_resources,undefined);
      const lowMessage=await evaluate(`${q('#it-root [data-role="resource-message"]')}.textContent`);
      assert.match(lowMessage,/资源提示/);
      return {lowParams, lowMessage};
    });

    // ------------------------------------------------------------------ 汇总
    await step('AI强化未验证：禁用且不提交伪任务', async () => {
      await mountScenario('stable');
      const state = await evaluate(`(()=>{const b=document.querySelector('[data-role="enhance"]');b.click();return {disabled:b.disabled,title:b.title,calls:MockHarness.countCalls('/image-tools/jobs','POST')}})()`);
      assert.equal(state.disabled,true);
      assert.match(state.title,/尚未完成本机推理验证/);
      assert.equal(state.calls,0);
      return state;
    });
    await step('无缝参数传递、实际边距与核心保护文案', async () => {
      await mountScenario('completed-job','outpaint');
      await pickFromGallery();
      await fillOutpaintPrompt('extend');
      await evaluate(`(()=>{const s=document.querySelector('[id$="seam_mode"]');s.value='feather';s.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
      await waitFor(`${q('#it-root .it-preview-panel p.it-estimate')}.textContent.includes('对齐后实际扩展量')`);
      await evaluate(`(()=>{const c=${q('#it-root .it-actions input[type=checkbox]')};c.checked=true;c.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
      await click('#it-root [data-role="start-job"]');
      await waitFor(`MockHarness.state.jobs.length>0`);
      const params=await evaluate('MockHarness.state.jobs[0].params');
      assert.equal(params.seam_mode,'feather');
      assert.equal(params.feather_px,32);
      assert.equal(params.tone_match,true);
      await waitFor(`${q('#it-root .it-job-panel .it-badge')}.textContent.includes('已完成')`,12000);
      await evaluate(`(()=>{const j=MockHarness.state.jobs[0];j.result.preservation={verified:true,different_pixels:0,scope:'core'};j.result.seam={warning:'需要人工检查',fallback:[]};j.result.strict_url='/api/image-tools/jobs/test/artifacts/strict';j.result.comparison_url='/api/image-tools/jobs/test/artifacts/comparison';return true})()`);
      await click('#it-root [data-role="refresh-history"]');
      await delay(300);
      await evaluate(`document.querySelector('#it-root .it-history-item').click();true`);
      await waitFor(`${q('#it-root .it-preservation')}.textContent.includes('原图核心像素')`);
      await shot('feather-core-protected');
      return params;
    });
    const failed = steps.filter(step => !step.ok);
    const report = {
      generated_at: new Date().toISOString(),
      page: base,
      chrome,
      node: process.version,
      steps,
      screenshots,
      page_errors: pageErrors,
      passed: steps.length - failed.length,
      failed: failed.length,
      notes: [
        '纯内存 mock：未访问真实 /api，未加载模型，未调用 DeepSeek。',
        'Chrome headless + CDP，无第三方依赖；截图在 tests/artifacts/image-tools-ui/。',
        'mock 中的合成图片与预览地址使用 data:image/ 或 /api/ 相对路径，模块会拒绝其它协议。'
      ]
    };
    fs.writeFileSync(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2));
    assert.equal(failed.length, 0, `${failed.length} 个检查未通过：` + failed.map(step => step.name).join('；'));
    assert.deepEqual(pageErrors, [], '页面不应有脚本异常或 console error');
    process.stdout.write(`\n通过 ${report.passed} / ${steps.length} 项，截图 ${screenshots.length} 张，页面异常 ${pageErrors.length} 条\n`);
  } finally {
    try { if (ws) ws.close(); } catch (ignored) { /* 忽略关闭异常 */ }
    try { browser.kill(); } catch (ignored) { /* 进程可能已退出 */ }
    server.close();
  }
}

main().catch(error => {
  process.stderr.write(`\n界面检查失败：${error && error.stack ? error.stack : error}\n`);
  process.exitCode = 1;
});
