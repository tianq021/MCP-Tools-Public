// Headless Chrome checks against the mock API server. No model, no real service.
const fs = require('node:fs');
const path = require('node:path');

const [base, devtoolsPort, outJson, shotDir] = process.argv.slice(2);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function fail(message) { throw new Error(message); }
function check(ok, message) { if (!ok) fail(message); }

async function main() {
  const targets = await (await fetch(`http://127.0.0.1:${devtoolsPort}/json/list`)).json();
  const target = targets.find(t => t.type === 'page');
  if (!target) fail('no page target');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let serial = 0;
  const pending = new Map();
  const exceptions = [];
  ws.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const entry = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) entry.reject(message.error); else entry.resolve(message.result);
    } else if (message.method === 'Runtime.exceptionThrown') {
      exceptions.push(message.params.exceptionDetails);
    }
  };
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++serial;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) fail(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const shot = async name => {
    const result = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    fs.writeFileSync(path.join(shotDir, name), Buffer.from(result.data, 'base64'));
  };
  const waitReady = async () => {
    for (let i = 0; i < 60; i++) {
      try {
        const ready = await evaluate("typeof state !== 'undefined' && !!state.status && state.images.length >= 2");
        if (ready) return;
      } catch (_) { /* execution context not ready yet */ }
      await delay(250);
    }
    fail('frontend did not become ready');
  };
  const load = async mode => {
    await fetch(`${base}/__mode?state=${mode}`);
    await call('Emulation.setDeviceMetricsOverride', {
      width: 1512, height: 1100, deviceScaleFactor: 1, mobile: false,
    });
    await call('Page.navigate', { url: base });
    await waitReady();
    await evaluate("document.querySelector('#advanced-settings').open=true");
    await delay(200);
  };
  const openProps = async () => {
    await evaluate("toggleCanvasProps(true)");
    await delay(150);
  };
  const props = async () => evaluate(`(()=>{
    const panel=document.querySelector('#canvas-props-body');
    return {text:panel.textContent,links:[...panel.querySelectorAll('a')].map(a=>a.getAttribute('href'))};
  })()`);

  await call('Runtime.enable');
  await call('Page.enable');
  const checks = {};

  // 1. capability unknown
  await load('unknown');
  checks.unknown = await evaluate(`(()=>{
    const select=document.querySelector('#output-bit-depth');
    const option=select.querySelector('option[value="16"]');
    return {value:select.value,disabled:option.disabled,label:option.textContent,
            note:document.querySelector('#output-bit-depth-note').textContent};
  })()`);
  check(checks.unknown.value === '8', 'unknown state must default to 8 bit');
  check(!checks.unknown.disabled, '16 bit must be selectable before the engine is loaded');
  check(checks.unknown.label.includes('加载后验证'), 'unknown option label must explain deferred validation');
  check(checks.unknown.note.includes('加载引擎后验证') && checks.unknown.note.includes('不会自动加载'),
        'unknown note must explain no automatic loading');
  await evaluate("restoreConfig({output_bit_depth:16});poll()");
  checks.unknownReuse = await evaluate("({value:$('#output-bit-depth').value,depth:formConfig().output_bit_depth})");
  check(checks.unknownReuse.value === '16' && checks.unknownReuse.depth === 16,
        '16-bit preset and status polling must preserve the request while unloaded');
  await evaluate("preview('img16')");
  await delay(250);
  await openProps();
  checks.img16Unknown = await props();
  checks.img16Unknown.src = await evaluate("document.querySelector('#canvas img').getAttribute('src')");
  checks.img16Unknown.loaded = await evaluate(`(()=>{const img=document.querySelector('#canvas img');
    return !!(img&&img.complete&&img.naturalWidth>0);})()`);
  check(checks.img16Unknown.src.endsWith('/preview'), '16-bit canvas must use /preview');
  check(checks.img16Unknown.loaded, '16-bit preview did not render in the canvas');
  await evaluate("document.querySelector('#output-bit-depth').scrollIntoView({block:'center'})");
  await delay(200);
  await shot('frontend-mock-depth-desktop-unknown.png');
  await evaluate("window.scrollTo(0,0)");

  // 2. capability unsupported
  await load('unsupported');
  checks.unsupported = await evaluate(`(()=>{
    const option=document.querySelector('#output-bit-depth option[value="16"]');
    return {disabled:option.disabled,label:option.textContent,
            note:document.querySelector('#output-bit-depth-note').textContent};
  })()`);
  check(checks.unsupported.disabled, '16 bit must be disabled when unsupported');
  check(checks.unsupported.label.includes('当前引擎不支持'), 'unsupported label mismatch');
  check(checks.unsupported.note.includes('只能生成 8 bit'), 'unsupported note mismatch');

  // 3. capability supported + 16-bit image properties/export links
  await load('supported');
  checks.supported = await evaluate(`(()=>{
    const select=document.querySelector('#output-bit-depth');
    const option=select.querySelector('option[value="16"]');
    return {disabled:option.disabled,label:option.textContent,value:select.value,
            note:document.querySelector('#output-bit-depth-note').textContent};
  })()`);
  check(!checks.supported.disabled, '16 bit must be enabled when supported');
  check(checks.supported.note.includes('已确认支持'), 'supported note mismatch');
  await evaluate("preview('img16')");
  await delay(250);
  await openProps();
  checks.img16 = await props();
  check(checks.img16.text.includes('16 bit / 通道'), 'properties must show actual file depth 16');
  check(checks.img16.text.includes('引擎声明'), 'properties must show engine declaration separately');
  check(checks.img16.text.includes('精度来源'), 'properties must show precision source separately');
  check(checks.img16.text.includes('engine_api_generate_image_u16'), 'precision source value missing');
  check(!checks.img16.text.includes('有效精度'), 'must not call it 16-bit effective precision');
  check(checks.img16.links.some(href => href.includes('/file?download=true')), 'download original link missing');
  check(checks.img16.links.some(href => href.includes('/export?depth=8')), 'export 8-bit link missing');
  checks.img16.loaded = await evaluate(`(()=>{const img=document.querySelector('#canvas img');
    return !!(img&&img.complete&&img.naturalWidth>0);})()`);
  check(checks.img16.loaded, '16-bit preview did not render');
  await shot('frontend-mock-supported.png');
  await evaluate("document.querySelector('#output-bit-depth').scrollIntoView({block:'center'})");
  await delay(200);
  await shot('frontend-mock-depth-desktop-supported.png');
  await evaluate("window.scrollTo(0,0)");

  // 3b. gallery parameter reuse goes through restoreConfig and keeps 16 bit
  await evaluate("detail('img16')");
  await delay(250);
  await evaluate("document.querySelector('#image-dialog [data-action=\"reuse\"]').click()");
  await delay(400);
  checks.galleryReuse = await evaluate(`({value:document.querySelector('#output-bit-depth').value,
    prompt:document.querySelector('#prompt').value})`);
  check(checks.galleryReuse.value === '16', 'gallery reuse must restore 16 bit when supported');
  check(checks.galleryReuse.prompt === '16 bit 渐变测试', 'gallery reuse did not restore parameters');

  // 3c. preset menu click path (not just restoreConfig directly)
  await evaluate("renderPresetMenu()");
  await delay(200);
  await evaluate("document.querySelector('#preset-menu [data-action=\"use-preset\"][data-id=\"preset16\"]').click()");
  await delay(400);
  checks.presetClick16 = await evaluate("document.querySelector('#output-bit-depth').value");
  check(checks.presetClick16 === '16', 'preset menu must restore 16 bit');
  await evaluate("renderPresetMenu()");
  await delay(200);
  await evaluate("document.querySelector('#preset-menu [data-action=\"use-preset\"][data-id=\"preset8\"]').click()");
  await delay(400);
  checks.presetClick8 = await evaluate("document.querySelector('#output-bit-depth').value");
  check(checks.presetClick8 === '8', 'old preset must restore 8 bit');

  // 4. depth selection and 8-bit warning
  await evaluate(`(()=>{
    const select=document.querySelector('#output-bit-depth');
    select.value='16';select.dispatchEvent(new Event('change'));
  })()`);
  checks.sixteen = await evaluate(`({value:document.querySelector('#output-bit-depth').value,
    depth:formConfig().output_bit_depth,note:document.querySelector('#output-bit-depth-note').textContent})`);
  check(checks.sixteen.depth === 16, 'formConfig must send 16 when selected');
  await evaluate(`(()=>{
    const select=document.querySelector('#output-bit-depth');
    select.value='8';select.dispatchEvent(new Event('change'));
  })()`);
  checks.eightWarning = await evaluate("document.querySelector('#output-bit-depth-note').textContent");
  check(checks.eightWarning.includes('需要重新生成') && checks.eightWarning.includes('不能从 8 bit 原图恢复'),
        '8-bit selection must warn that 16-bit needs regeneration');

  // 5. presets, reuse and reset
  await evaluate(`restoreConfig(${JSON.stringify({prompt:'old preset', width:512, height:512, steps:10,
    cfg:1, seed:9, batch_count:1, sampler:'euler', scheduler:'default', vae_tiling:true})})`);
  checks.presetOld = await evaluate("document.querySelector('#output-bit-depth').value");
  check(checks.presetOld === '8', 'old preset without output_bit_depth must default to 8');
  await evaluate(`restoreConfig(${JSON.stringify({prompt:'16 preset', width:1024, height:1024, steps:20,
    cfg:1, seed:42, batch_count:1, sampler:'euler', scheduler:'default', vae_tiling:true,
    output_bit_depth:16})})`);
  checks.preset16 = await evaluate("document.querySelector('#output-bit-depth').value");
  check(checks.preset16 === '16', '16-bit preset must apply when supported');
  await fetch(`${base}/__mode?state=unsupported`);
  await evaluate("poll()");
  await evaluate(`restoreConfig(${JSON.stringify({prompt:'16 preset', width:1024, height:1024, steps:20,
    cfg:1, seed:42, batch_count:1, sampler:'euler', scheduler:'default', vae_tiling:true,
    output_bit_depth:16})})`);
  checks.presetUnsupported = await evaluate(`({value:document.querySelector('#output-bit-depth').value,
    toast:document.querySelector('#toast').textContent})`);
  check(checks.presetUnsupported.value === '16', 'unsupported engine must not downgrade a 16-bit preset');
  check(checks.presetUnsupported.toast.includes('生成会被拒绝'), 'unsupported request must be explained');
  await fetch(`${base}/__mode?state=unknown`);
  await evaluate('poll()');
  checks.unloadedSelection = await evaluate("({value:$('#output-bit-depth').value,depth:formConfig().output_bit_depth})");
  check(checks.unloadedSelection.value === '16' && checks.unloadedSelection.depth === 16,
        'unloading must preserve an existing 16-bit selection');
  await fetch(`${base}/__mode?state=supported`);
  await evaluate("poll()");
  await evaluate(`(()=>{window.confirm=()=>true;
    const select=document.querySelector('#output-bit-depth');
    select.value='16';select.dispatchEvent(new Event('change'));
    document.querySelector('#reset-form').click();})()`);
  checks.reset = await evaluate("document.querySelector('#output-bit-depth').value");
  check(checks.reset === '8', 'reset must return to 8 bit');

  // 6. 8-bit image uses /file and refuses true 16
  await evaluate("preview('img8')");
  await delay(250);
  checks.img8 = await evaluate(`(()=>{
    const src=document.querySelector('#canvas img').getAttribute('src');
    toggleCanvasProps(true);
    const panel=document.querySelector('#canvas-props-body');
    return {src,text:panel.textContent,links:[...panel.querySelectorAll('a')].map(a=>a.getAttribute('href'))};
  })()`);
  check(checks.img8.src.endsWith('/file'), '8-bit canvas must use /file');
  checks.img8.loaded = await evaluate(`(()=>{const img=document.querySelector('#canvas img');
    return !!(img&&img.complete&&img.naturalWidth>0);})()`);
  check(checks.img8.loaded, '8-bit image did not render in the canvas');
  check(checks.img8.text.includes('8 bit / 通道'), '8-bit properties must show file depth 8');
  check(checks.img8.text.includes('无法导出真正的 16 bit'), '8-bit export limitation must be shown');
  check(!checks.img8.links.some(href => href.includes('/export?depth=8')), '8-bit image should not offer 8-bit export');

  // 7. export bytes at the mock API level
  const jobsBefore = (await (await fetch(`${base}/api/jobs`)).json()).length;
  const master = await fetch(`${base}/api/images/img16/file?download=true`);
  const eight = await fetch(`${base}/api/images/img16/export?depth=8`);
  const sixteen = await fetch(`${base}/api/images/img16/export?depth=16`);
  const legacy16 = await fetch(`${base}/api/images/img8/export?depth=16`);
  const legacy8 = await fetch(`${base}/api/images/img8/export?depth=8`);
  const legacyMaster = await fetch(`${base}/api/images/img8/file?download=true`);
  checks.exportBytes = {
    masterStatus: master.status, eightStatus: eight.status, sixteenStatus: sixteen.status,
    legacy16Status: legacy16.status, legacy8Status: legacy8.status,
    masterLength: (await master.arrayBuffer()).byteLength,
    eightLength: (await eight.arrayBuffer()).byteLength,
    sixteenLength: (await sixteen.arrayBuffer()).byteLength,
    legacy8EqualsMaster: Buffer.from(await legacy8.arrayBuffer()).equals(Buffer.from(await legacyMaster.arrayBuffer())),
    jobsBefore,
    jobsAfter: (await (await fetch(`${base}/api/jobs`)).json()).length,
  };
  check(checks.exportBytes.masterStatus === 200 && checks.exportBytes.sixteenStatus === 200,
        '16-bit export must succeed');
  check(checks.exportBytes.eightStatus === 200, '8-bit export must succeed');
  check(checks.exportBytes.legacy16Status === 409, 'legacy 8-bit -> 16 must be refused');
  check(checks.exportBytes.masterLength !== checks.exportBytes.eightLength, 'preview must differ from master');
  check(checks.exportBytes.legacy8EqualsMaster, '8-bit export must equal its master');
  check(checks.exportBytes.jobsBefore === checks.exportBytes.jobsAfter,
        'export/download must not submit generation jobs');

  // 8. mobile layout
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await call('Page.navigate', { url: base });
  await waitReady();
  await evaluate("document.querySelector('#advanced-settings').open=true");
  await delay(200);
  checks.mobile = await evaluate(`(()=>{
    const select=document.querySelector('#output-bit-depth');
    const rect=select.getBoundingClientRect();
    return {overflow:document.documentElement.scrollWidth>innerWidth,
            selectVisible:rect.width>0&&rect.height>0,
            noteVisible:document.querySelector('#output-bit-depth-note').getBoundingClientRect().height>0};
  })()`);
  check(!checks.mobile.overflow, 'mobile layout has horizontal overflow');
  check(checks.mobile.selectVisible && checks.mobile.noteVisible, 'depth control must be visible on mobile');
  await evaluate("document.querySelector('#output-bit-depth').scrollIntoView({block:'center'})");
  await delay(250);
  await shot('frontend-mock-depth-mobile.png');
  await evaluate("window.scrollTo(0,0)");
  await shot('frontend-mock-mobile.png');

  checks.exceptions = exceptions.length;
  checks.noAutoLoad = (await (await fetch(`${base}/__stats`)).json()).load_requests;
  check(checks.noAutoLoad === 0, 'the UI must not auto-load models to probe 16-bit capability');
  fs.writeFileSync(outJson, JSON.stringify(checks, null, 2), 'utf8');
  console.log(JSON.stringify(checks, null, 2));
  if (exceptions.length) {
    console.error(JSON.stringify(exceptions, null, 2));
    fail('uncaught page exceptions');
  }
  ws.close();
}

main().catch(error => { console.error(error.stack || String(error)); process.exit(1); });
