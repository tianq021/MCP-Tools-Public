const fs = require('node:fs');
const path = require('node:path');
const delay = ms => new Promise(r=>setTimeout(r,ms));
async function main(){
  const [port] = fs.readFileSync('tests/artifacts/browser-profile/DevToolsActivePort','utf8').trim().split('\n');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target=targets.find(t=>t.type==='page');
  const ws=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  let serial=0;const pending=new Map();const exceptions=[];
  ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}else if(m.method==='Runtime.exceptionThrown')exceptions.push(m.params.exceptionDetails);};
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  const shot=async name=>{const r=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join('tests/artifacts',name),Buffer.from(r.data,'base64'));};
  await call('Runtime.enable');await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1512,height:1100,deviceScaleFactor:1,mobile:false});
  const appUrl=fs.readFileSync('data/server-url.txt','utf8').trim();
  await call('Page.navigate',{url:appUrl});
  let ready=false;
  for(let i=0;i<45;i++){await delay(500);ready=await evaluate(`typeof state !== 'undefined' && state.images.length > 0 && state.status !== null`);if(ready)break;}
  if(!ready)throw new Error('App did not finish loading');
  const checks={};
  checks.initial=await evaluate(`({title:document.title,images:state.images.length,generateDisabled:document.querySelector('#generate-button').disabled,blockers:state.status.models.blockers,overflow:document.documentElement.scrollWidth>innerWidth})`);
  await shot('web-studio-desktop.png');
  await evaluate(`document.querySelector('#assistant-config-toggle').click()`);await delay(300);
  checks.assistantConfig=await evaluate(`({open:!document.querySelector('#assistant-config').classList.contains('hidden'),url:document.querySelector('#assistant-url').value,model:document.querySelector('#assistant-model').value,sendDisabled:document.querySelector('#assistant-send').disabled,helperHidden:!document.querySelector('#advanced-settings').open})`);
  if(checks.assistantConfig.sendDisabled){
    await evaluate(`document.querySelector('#assistant-input').value='测试一句';document.querySelector('#assistant-input').dispatchEvent(new Event('input'));document.querySelector('#assistant-form').requestSubmit()`);await delay(400);
    checks.assistantGuard=await evaluate(`({toast:document.querySelector('#toast').textContent,kept:document.querySelector('#assistant-input').value,messages:document.querySelectorAll('#assistant-log .msg').length,counter:document.querySelector('#assistant-counter').textContent})`);
  }else{
    checks.assistantGuard={skipped:'already configured, no real request sent'};
  }
  await shot('web-assistant-desktop.png');
  await evaluate(`document.querySelector('#assistant-prompt-toggle').click()`);await delay(500);
  checks.assistantPrompt=await evaluate(`({info:document.querySelector('#assistant-prompt-info').textContent,visible:!document.querySelector('#assistant-prompt-text').classList.contains('hidden'),chars:document.querySelector('#assistant-prompt-text').textContent.length,hasParams:document.querySelector('#assistant-prompt-text').textContent.includes('当前工作台参数')})`);
  await evaluate(`document.querySelector('#assistant-prompt-text').scrollIntoView({block:'center'})`);await delay(400);
  await shot('web-assistant-prompt.png');
  await evaluate(`document.querySelector('#assistant-prompt-toggle').click();window.scrollTo(0,0)`);await delay(150);
  await evaluate(`(async()=>{const blob=await (await fetch('/api/images/'+state.images[0].id+'/file')).blob();const file=new File([blob],'attach-check.png',{type:'image/png'});const dt=new DataTransfer();dt.items.add(file);const input=document.querySelector('#assistant-file');input.files=dt.files;input.dispatchEvent(new Event('change'));})()`);
  await delay(1500);
  checks.attachment=await evaluate(`({visible:!document.querySelector('#assistant-attachment').classList.contains('hidden'),name:document.querySelector('#assistant-attachment-name').textContent,hasId:!!assistant.attachment?.id,thumb:document.querySelector('#assistant-attachment-thumb').src.startsWith('blob:')})`);
  await evaluate(`document.querySelector('#assistant-attachment').scrollIntoView({block:'center'})`);await delay(350);
  await shot('web-assistant-attachment.png');
  await evaluate(`document.querySelector('#assistant-attachment-remove').click()`);await delay(200);
  checks.attachmentCleared=await evaluate(`({hidden:document.querySelector('#assistant-attachment').classList.contains('hidden'),empty:!assistant.attachment})`);
  checks.assistantScroll=await evaluate(`(()=>{const log=document.querySelector('#assistant-log');const v=createMessage('assistant');renderRichText(v.body,Array.from({length:40},(_,i)=>'- 第'+i+'行：用来撑高对话区的测试文字，检查滚动条是否生效。').join('\\n'));const metrics={scrollable:log.scrollHeight>log.clientHeight+10,clientHeight:log.clientHeight,scrollHeight:log.scrollHeight,scrollbarWidth:log.offsetWidth-log.clientWidth};log.scrollTop=0;assistant.following=false;updateJump();metrics.jumpVisible=!document.querySelector('#assistant-jump').classList.contains('hidden');return metrics;})()`);
  await delay(250);
  await shot('web-assistant-scroll.png');
  await evaluate(`document.querySelector('#assistant-jump').click()`);await delay(250);
  Object.assign(checks.assistantScroll,await evaluate(`(()=>{const log=document.querySelector('#assistant-log');const metrics={backToBottom:log.scrollHeight-log.scrollTop-log.clientHeight<90,jumpHidden:document.querySelector('#assistant-jump').classList.contains('hidden')};assistant.messages.push({role:'user',content:'占位'.repeat(600)});updateContextRing();metrics.ring=document.querySelector('#assistant-context-label').textContent;metrics.ringOffset=document.querySelector('#assistant-context-arc').style.strokeDashoffset;return metrics;})()`));
  await evaluate(`assistant.messages.pop();updateContextRing();document.querySelector('#assistant-log').innerHTML='';updateJump()`);
  await evaluate(`document.querySelector('#assistant-config-toggle').click();document.querySelector('#assistant-input').value='';updateAssistantCount()`);await delay(200);
  await evaluate(`document.querySelector('#advanced-settings').open=true`);await delay(200);
  await evaluate(`document.querySelector('#width').scrollIntoView({block:'center'})`);await delay(250);await shot('web-advanced-desktop.png');
  await evaluate(`document.querySelector('#advanced-settings').open=false`);await delay(150);
  await evaluate(`document.querySelector('[data-view="gallery"]').click()`);await delay(600);
  checks.gallery=await evaluate(`({cards:document.querySelectorAll('#gallery-grid .image-card').length,brokenImages:[...document.querySelectorAll('#gallery-grid img')].filter(x=>x.complete&&!x.naturalWidth).length})`);
  await shot('web-gallery-desktop.png');
  await evaluate(`document.querySelector('#gallery-grid [data-action="detail"]').click()`);await delay(500);
  checks.detail=await evaluate(`({open:document.querySelector('#image-dialog').open,hasHash:document.querySelector('#image-detail').textContent.includes('SHA256'),prompt:document.querySelector('#image-detail .prompt-text').textContent.length})`);
  await shot('web-detail-desktop.png');
  await evaluate(`document.querySelector('#image-dialog').close();state.selected=new Set(state.images.slice(0,2).map(x=>x.id));updateCompare();compare()`);await delay(300);
  checks.compare=await evaluate(`document.querySelectorAll('.compare-column').length===2 && document.querySelector('#compare-dialog').open`);
  await evaluate(`document.querySelector('#compare-dialog').close();state.selected.clear();updateCompare();navigate('models')`);await delay(300);
  await shot('web-models-desktop.png');
  checks.models=await evaluate(`({cards:document.querySelectorAll('.model-card').length,toggleDisabled:document.querySelector('#model-toggle').disabled,toggleMode:document.querySelector('#model-toggle').dataset.mode,toggleLabel:document.querySelector('#model-toggle').textContent,quickReleaseHidden:document.querySelector('#quick-release').classList.contains('hidden'),util:document.querySelector('#gpu-util').textContent,hasProfiles:!!document.querySelector('#profile-select'),hasFlags:!!document.querySelector('#load-form').elements.extra_args,devices:[...document.querySelectorAll('.model-card')].map(c=>c.textContent.includes('运行设备')),loadForm:{vae:document.querySelector('#load-form').elements.vae.value.slice(-20),maxVram:document.querySelector('#load-form').elements.max_vram.value},gpu:document.querySelector('#gpu-full-name').textContent})`);;
  await evaluate(`window.confirm=()=>true`);
  if(await evaluate(`document.querySelector('#model-toggle').dataset.mode==='release'`)){
    await evaluate(`document.querySelector('#quick-release').click()`);
    for(let i=0;i<15;i++){await delay(1000);if(await evaluate(`document.querySelector('#model-toggle').dataset.mode==='load'`))break;}
  }
  await evaluate(`document.querySelector('#model-toggle').click()`);
  let modelLoaded=false;
  for(let i=0;i<45;i++){await delay(2000);modelLoaded=await evaluate(`document.querySelector('#model-toggle').dataset.mode==='release'`);if(modelLoaded)break;}
  checks.loadToggle=await evaluate(`({mode:document.querySelector('#model-toggle').dataset.mode,label:document.querySelector('#model-toggle').textContent.trim(),quickVisible:!document.querySelector('#quick-release').classList.contains('hidden'),engine:document.querySelector('#engine-state').textContent})`);
  await shot('web-models-loaded.png');
  await evaluate(`document.querySelector('#model-toggle').click()`);
  let modelReleased=false;
  for(let i=0;i<20;i++){await delay(1000);modelReleased=await evaluate(`document.querySelector('#model-toggle').dataset.mode==='load'`);if(modelReleased)break;}
  checks.releaseToggle=await evaluate(`({mode:document.querySelector('#model-toggle').dataset.mode,label:document.querySelector('#model-toggle').textContent.trim(),quickHidden:document.querySelector('#quick-release').classList.contains('hidden'),engine:document.querySelector('#engine-state').textContent})`);
  await evaluate(`navigate('studio');document.querySelector('#prompt').value='一座清晨的山间小屋';document.querySelector('#prompt').dispatchEvent(new Event('input'));document.querySelector('[data-ratio="768,1280"]').click();window.prompt=()=> '__browser_check_preset__';document.querySelector('#save-preset').click()`);
  await delay(500);
  checks.preset=await evaluate(`(async()=>{const presets=await api('/presets');const p=presets.find(p=>p.name==='__browser_check_preset__');if(!p)return false;const valid=p.config.prompt==='一座清晨的山间小屋' && p.config.width===768 && p.config.height===1280;await api('/presets/'+p.id,{method:'DELETE'});return valid;})()`);
  checks.canvasNav=await evaluate(`(async()=>{await preview(state.images[0].id);const nav=document.querySelector('#canvas-nav');const index=document.querySelector('#canvas-index').textContent;const before=state.preview.id;document.querySelector('#canvas-next').click();await new Promise(r=>setTimeout(r,700));const after=state.preview.id;const index2=document.querySelector('#canvas-index').textContent;document.querySelector('#canvas-prev').click();await new Promise(r=>setTimeout(r,700));return {visible:!nav.classList.contains('hidden'),index,index2,changed:before!==after,back:state.preview.id===before};})()`);
  await shot('web-canvas-nav.png');
  checks.canvasProps=await evaluate(`(()=>{const button=document.querySelector('#canvas-props');const visible=!button.classList.contains('hidden');button.click();const panel=document.querySelector('#canvas-props-panel');const opened=!panel.classList.contains('hidden');const sections=[...document.querySelectorAll('#canvas-props-body .props-section')].map(s=>s.querySelector('h4').textContent);const rows=document.querySelectorAll('#canvas-props-body .props-row').length;const text=propsPlainText(state.preview);const bodyText=document.querySelector('#canvas-props-body').textContent;const p=panel.getBoundingClientRect(),frame=document.querySelector('.canvas-panel').getBoundingClientRect(),body=document.querySelector('#canvas-props-body');return {visible,opened,sections,rows,hasSize:bodyText.includes(String(state.preview.width)+' × '+String(state.preview.height)),hasSeed:text.includes('种子'),hasSteps:text.includes('步数'),expanded:button.getAttribute('aria-expanded'),withinFrame:p.top>=Math.round(frame.top)&&p.bottom<=Math.round(frame.bottom)+1,bodyScrollable:body.scrollHeight>body.clientHeight+4&&body.clientHeight>0,scrollbar:body.offsetWidth-body.clientWidth};})()`);
  await delay(250);
  await shot('web-canvas-props.png');
  checks.canvasPropsClosed=await evaluate(`(()=>{document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));return {hidden:document.querySelector('#canvas-props-panel').classList.contains('hidden'),expanded:document.querySelector('#canvas-props').getAttribute('aria-expanded')};})()`);
  checks.customSize=await evaluate(`(()=>{const prompt=document.querySelector('#prompt'),width=document.querySelector('#width');document.querySelector('#ratio-custom').click();const advancedOpen=document.querySelector('#advanced-settings').open;const focused=document.activeElement===width;width.value='1003';width.dispatchEvent(new Event('change',{bubbles:true}));const aligned=width.value;const hint=document.querySelector('.dimension-hint').textContent;const customActive=document.querySelector('#ratio-custom').classList.contains('active');width.value='1024';width.dispatchEvent(new Event('change',{bubbles:true}));return {promptOptional:prompt.required===false,advancedOpen,focused,aligned,customActive,hintHasEight:hint.includes('8 的倍数'),step:width.getAttribute('step')};})()`);
  await delay(150);
  await evaluate(`(async()=>{const cfg={...formConfig(),prompt:'界面测试提示词',steps:22};await api('/presets',{method:'POST',body:{name:'__ui_preset__',config:cfg}});})()`);
  await evaluate(`document.querySelector('#open-presets').click()`);await delay(600);
  checks.presetMenu=await evaluate(`({open:!document.querySelector('#preset-menu').classList.contains('hidden'),items:document.querySelectorAll('.preset-menu-item').length,count:document.querySelector('#preset-count').textContent})`);
  await shot('web-preset-menu.png');
  await evaluate(`document.querySelector('#preset-menu .preset-menu-item [data-action="use-preset"]').click()`);await delay(700);
  checks.presetLoad=await evaluate(`({prompt:document.querySelector('#prompt').value,steps:document.querySelector('#steps').value,closed:document.querySelector('#preset-menu').classList.contains('hidden')})`);
  await evaluate(`(async()=>{const presets=await api('/presets');for(const item of presets){if(item.name==='__ui_preset__')await api('/presets/'+item.id,{method:'DELETE'});}})()`);
  await evaluate(`navigate('gallery');`);await delay(400);
  await evaluate(`const box=document.querySelector('#gallery-grid [data-select]');box.click()`);
  checks.selection=await evaluate(`state.selected.size===1 && !document.querySelector('#compare-bar').classList.contains('hidden')`);
  await evaluate(`document.querySelector('#clear-compare').click();document.querySelector('#theme-toggle').click()`);
  checks.theme=await evaluate(`document.body.classList.contains('light')`);
  await evaluate(`document.querySelector('#theme-toggle').click();document.querySelector('#generation-form').reset();updatePromptCount();updateRatio();`);
  for(const view of ['favorites','vault','settings']){await evaluate(`navigate('${view}')`);await delay(200);checks[view]=await evaluate(`document.querySelector('#view-${view}').classList.contains('active')`);}
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await evaluate(`navigate('studio')`);await delay(200);await shot('web-studio-mobile.png');
  checks.mobile=await evaluate(`({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,overflow:document.documentElement.scrollWidth>innerWidth})`);
  checks.exceptions=exceptions;
  fs.writeFileSync('tests/artifacts/browser-check.json',JSON.stringify(checks,null,2));
  console.log(JSON.stringify(checks,null,2));ws.close();
  if(checks.initial.overflow||checks.mobile.overflow||exceptions.length||checks.gallery.brokenImages||!checks.preset||!checks.selection||!checks.theme||!checks.assistantConfig.open||(!checks.assistantGuard.skipped&&!checks.assistantConfig.sendDisabled)||(!checks.assistantGuard.skipped&&checks.assistantGuard.kept!=='测试一句')||(!checks.assistantGuard.skipped&&!!checks.assistantGuard.messages)||checks.loadToggle.mode!=='release'||!checks.loadToggle.quickVisible||!checks.models.hasProfiles||!checks.models.hasFlags||checks.releaseToggle.mode!=='load'||!checks.releaseToggle.quickHidden||!checks.assistantPrompt.visible||checks.assistantPrompt.chars<400||!checks.assistantPrompt.hasParams||!checks.canvasNav.visible||!checks.canvasNav.changed||!checks.canvasNav.back||!checks.presetMenu.open||checks.presetMenu.items<1||checks.presetLoad.prompt!=='界面测试提示词'||checks.presetLoad.steps!=='22'||!checks.presetLoad.closed||!checks.attachment.visible||!checks.attachment.hasId||!checks.attachmentCleared.hidden||!checks.assistantScroll.scrollable||!checks.assistantScroll.jumpVisible||!checks.assistantScroll.backToBottom||checks.assistantScroll.scrollbarWidth<1||checks.assistantScroll.ring!=='2%'||!checks.canvasProps.visible||!checks.canvasProps.opened||checks.canvasProps.sections.length<5||checks.canvasProps.rows<10||!checks.canvasProps.hasSize||!checks.canvasProps.hasSeed||!checks.canvasProps.hasSteps||checks.canvasProps.expanded!=='true'||!checks.canvasProps.withinFrame||!checks.canvasProps.bodyScrollable||checks.canvasProps.scrollbar<1||!checks.canvasPropsClosed.hidden||checks.canvasPropsClosed.expanded!=='false'||!checks.customSize.promptOptional||!checks.customSize.advancedOpen||!checks.customSize.focused||checks.customSize.aligned!=='1000'||!checks.customSize.customActive||!checks.customSize.hintHasEight||checks.customSize.step!=='8')process.exitCode=1;
}
main().catch(e=>{console.error(e);process.exitCode=1;});
