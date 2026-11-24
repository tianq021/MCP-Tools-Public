import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const directory = path.dirname(fileURLToPath(import.meta.url));
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function call(request) {
  const started = performance.now();
  const result = await new Promise((resolve, reject) => {
    const child = execFile(path.join(directory, 'DesktopControl.exe'), [], {encoding:'utf8',timeout:22000,windowsHide:true}, (_error,stdout) => {
      try {resolve(JSON.parse(stdout.trim()));} catch {reject(new Error('No helper result'));}
    });
    child.stdin.end(JSON.stringify(request),'utf8');
  });
  result.wall_ms = Math.round(performance.now()-started);
  return result;
}
const initial = await call({action:'list_windows'});
assert.equal(initial.paused,false);
const original = initial.windows.find(window=>window.foreground);
const fixture = spawn(path.join(directory,'ControlTestWindow.exe'),[],{stdio:'ignore'});
let observation;
async function state(predicate=()=>true) {
  for(let n=0;n<80;n++) {
    try {
      const data=JSON.parse(await readFile(path.join(directory,'test-window-state.json'),'utf8'));
      if(data.pid===fixture.pid&&predicate(data))return data;
    }catch{}
    await wait(100);
  }
  throw new Error('Test window state timed out');
}
try {
  const ui=await state(); await wait(250);
  observation=await call({action:'inspect_controls'});
  assert.equal(observation.ok,true,observation.message);
  assert.equal(observation.foreground.hwnd,ui.hwnd);
  assert.equal(observation.screenshot_taken,false);
  assert.equal(observation.image,null);
  const inspectMs=observation.wall_ms;
  const edit=observation.controls.find(control=>control.automation_id==='InputText'||control.name==='输入测试框');
  const button=observation.controls.find(control=>control.automation_id==='ClickTest'||control.name==='点击测试');
  assert(edit,'Input control must be exposed by UI Automation');
  assert(button,'Button must be exposed by UI Automation');
  const firstId=observation.observation_id;
  const typed=await call({action:'control_action',observation_id:firstId,control_id:edit.control_id,operation:'set_value',text:'Windows 原生控件快速通道'});
  assert.equal(typed.ok,true,typed.message);
  assert.equal(typed.verified,true);
  assert.equal(typed.screenshot_taken,false);
  await state(value=>value.text==='Windows 原生控件快速通道');
  const stale=await call({action:'control_action',observation_id:firstId,control_id:edit.control_id,operation:'set_value',text:'MUST_NOT_APPEAR'});
  assert.equal(stale.ok,false);assert.equal(stale.applied,false);
  observation=typed;
  const clicked=await call({action:'control_action',observation_id:observation.observation_id,control_id:button.control_id,operation:'invoke'});
  assert.equal(clicked.ok,true,clicked.message);
  assert.equal(clicked.screenshot_taken,false);
  const final=await state(value=>value.clicks===1);
  observation=clicked;
  const image=await call({action:'observe',include_screenshot:true});
  assert.equal(image.ok,true,image.message);observation=image;
  console.log(JSON.stringify({ok:true,checks:['native UI Automation inspection','direct Unicode ValuePattern set with readback','native InvokePattern button call','stale observation rejected'],fastPathScreenshots:0,finalText:final.text,clickCount:final.clicks,wallMs:{inspect:inspectMs,setValue:typed.wall_ms,invoke:clicked.wall_ms,screenshot:image.wall_ms},image:image.image.path}));
}finally{
  const current=await call({action:'list_windows'});
  const active=current.windows?.find(window=>window.foreground);
  const ui=await state().catch(()=>null);
  if(original&&ui&&active?.hwnd===ui.hwnd){
    const fresh=await call({action:'observe',include_screenshot:false});
    if(fresh.ok)await call({action:'focus_window',observation_id:fresh.observation_id,hwnd:String(original.hwnd)});
  }
  fixture.kill();
}
