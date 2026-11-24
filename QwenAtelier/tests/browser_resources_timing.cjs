// Isolated UI contract check: all API responses are synthetic; no model or assistant requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {spawn} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const artifacts = path.join(__dirname, 'artifacts');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const GiB = 1024 ** 3;

async function main() {
  fs.mkdirSync(artifacts, {recursive:true});
  fs.mkdirSync(path.join(root, '.tmp'), {recursive:true});
  const profile = fs.mkdtempSync(path.join(root, '.tmp', 'browser-resources-'));
  const settings = {diffusion_model:'models/Q4_K.gguf',text_encoder:'models/encoder.gguf',vae:'models/vae.safetensors',vision:'',max_vram:6,text_encoder_cpu:true,vae_cpu:false,keep_loaded:true,eager_load:false,extra_args:'',output_directory:'outputs',port:7860,assistant_url:'https://api.deepseek.com',assistant_model:'deepseek-flash'};
  const config = {width:512,height:512,steps:8,batch_count:2};
  const estimate = {elapsed_seconds:210,estimated_total_seconds:400,remaining_seconds:190,history_samples:2,history_exceeded:false,sampling_remaining_seconds:18,image_index:1,note:'按最近实际步速估算；仅当前图片采样，不含后续解码、保存和其他图片'};
  const jobs = [{id:'sample',status:'running',created:Date.now()/1000,started:Date.now()/1000-210,finished:null,stage:'第 1 / 2 张 · 采样',step:2,total_steps:8,progress:.125,config,image_ids:[],time_estimate:estimate}];
  const status = {gpu:{name:'RTX 4060 Laptop',used:3*GiB,total:8*GiB,free:5*GiB,utilization:12,process_used:null},memory:{total:32*GiB,available:19*GiB,process_rss:8*GiB,process_private:9*GiB},models:{blockers:[],individual_preload_supported:false,components:['diffusion_model','text_encoder','vae','vision'].map((key,i)=>({key,label:['扩散模型','文本编码器','VAE','视觉编码器'][i],name:settings[key]||'未配置',path:settings[key],size:i===3?0:3*GiB,state:i===3?'missing':'available',loaded:i!==3,device:i===1||i===3?'CPU':'CUDA0',optional:i===3,detail:'文件就绪',weight_placement:'CPU',eager_load:false}))},engine:{state:'ready',active_job:null,queued:0,pid:12345,elapsed:0,loaded:true,error:null}};
  const writes = [];
  let vaultStatus = {total:1,secured:0,pending:1,unreadable:0,unreadable_ids:[],password_book_present:true,key_saved:false,password_required:false};
  let vaultFiles = [{id:'l'.repeat(32),created:Date.now()/1000,size:100,state:'legacy'}];
  const sample = {id:'a'.repeat(32),name:'private-test.png',width:64,height:64,size:100,created:Date.now()/1000,sha256:'f'.repeat(64),tag:'favorite',bit_depth:8,config:{prompt:'private sample'},metadata:{}};
  let pictures = [sample];
  let presets = [{id:'preset-one',name:'回归预设',config:{prompt:'preset regression',width:768,height:512,steps:12,cfg:1,seed:42,batch_count:1}}];
  let cancelFails = true;
  const server = http.createServer(async (request,response) => {
    if(request.url.startsWith('/api/')){
      let raw='';for await(const chunk of request)raw+=chunk;
      if(request.method!=='GET')writes.push({url:request.url,body:raw?JSON.parse(raw):null});
      if(request.url==='/api/jobs/sample/cancel'){
        if(cancelFails){cancelFails=false;response.writeHead(500,{'Content-Type':'text/plain'});response.end('Internal Server Error');return;}
        jobs[0].cancellation_requested=true;
      }
      if(request.url==='/api/presets/preset-one'&&request.method==='DELETE')presets=[];
      if(request.url==='/api/vault/migrate'&&request.method==='POST'){
        vaultStatus={...vaultStatus,secured:1,pending:0,password_book_present:false};
        vaultFiles=[{id:'v'.repeat(32),created:sample.created,size:100,state:'secure'}];
      }
      if(request.url==='/api/vault/key'&&request.method==='PUT'){vaultStatus={...vaultStatus,key_saved:true};}
      if(request.url===`/api/images/${sample.id}/encrypt`)pictures=[];
      if(request.url==='/api/models/unload'){
        const last_release=status.engine.pid?{pid:status.engine.pid,timestamp:Date.now()/1000,process_rss:status.memory.process_rss,process_private:status.memory.process_private,process_gpu:status.gpu.process_used}:status.engine.last_release;
        status.engine={...status.engine,state:'idle',pid:null,loaded:false,last_release};
        status.memory.process_rss=0;status.memory.process_private=0;status.gpu.process_used=0;
        status.models.components.forEach(m=>m.loaded=false);
      }
      if(request.url==='/api/models/load'){
        Object.assign(settings,JSON.parse(raw));status.engine={...status.engine,state:'loading',pid:12345,elapsed:3};
      }
      if(request.url.startsWith('/api/models/components/')){
        const [key,action]=request.url.split('/').slice(-2);
        status.models.individual_preload_supported=true;
        status.models.components.forEach(m=>{
          if(!m.residency)m.residency={supported:true,tensors:m.optional?0:10,loaded_tensors:0,weight_bytes:GiB,loaded_weight_bytes:0,host_buffer_bytes:0,device_buffer_bytes:0};
          if(m.key===key)Object.assign(m.residency,{loaded_tensors:action==='load'?10:0,loaded_weight_bytes:action==='load'?GiB:0,host_buffer_bytes:action==='load'?GiB:0});
        });
        status.engine={...status.engine,state:'ready',pid:12345,loaded:true};
      }
      const replies = {'/api/session':{token:'isolated-test'},'/api/settings':settings,'/api/status':status,'/api/jobs':jobs,'/api/assistant/config':{has_key:false,base_url:settings.assistant_url,model:settings.assistant_model,saved:false},'/api/gallery/scan':{count:0,errors:[]},'/api/images':[],'/api/presets':[],'/api/profiles':[],'/api/models/load':{state:'loading'},'/api/models/unload':{state:'idle'}};
      replies['/api/vault/status']=vaultStatus;
      replies['/api/vault/key']={configured:vaultStatus.key_saved};
      replies['/api/vault/migrate']={migrated:1,kept:[],...vaultStatus};
      replies['/api/presets']=presets;
      replies['/api/images']=pictures;
      replies[`/api/images/${sample.id}`]=sample;
      if(request.url.startsWith('/api/images?'))replies[request.url]=pictures;
      replies[`/api/images/${sample.id}/encrypt`]={id:'v'.repeat(32),archived:true,original_retained:false};
      replies['/api/vault']=vaultFiles;
      replies[`/api/vault/${'v'.repeat(32)}/unlock`]={image:'',record:sample};
      response.writeHead(200,{'Content-Type':'application/json'});response.end(JSON.stringify(replies[request.url]??[]));return;
    }
    const file={'/':'index.html','/static/app.js':'app.js','/static/style.css':'style.css'}[request.url];
    if(!file){response.writeHead(404);response.end();return;}
    response.writeHead(200,{'Content-Type':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});
    response.end(fs.readFileSync(path.join(root,'web',file)));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',[
    '--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
    '--disable-background-networking','--disable-extensions','--remote-debugging-port=0',
    `--user-data-dir=${profile}`,'about:blank'],{windowsHide:true,stdio:['ignore','ignore','pipe']});
  let browserLog='';browser.stderr.on('data',chunk=>browserLog+=chunk);
  let ws,call;const errors=[];const checks={};
  try{
    const portFile=path.join(profile,'DevToolsActivePort');
    for(let i=0;i<100&&!fs.existsSync(portFile);i++)await delay(100);
    if(!fs.existsSync(portFile))throw new Error(`Headless browser did not start (exit ${browser.exitCode}): ${browserLog}`);
    const port=fs.readFileSync(portFile,'utf8').trim().split('\n')[0];
    const targets=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
    let serial=0;const pending=new Map();
    ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);if(p)m.error?p.reject(m.error):p.resolve(m.result);}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);};
    call=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
    const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
    const shot=async name=>{const r=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(artifacts,name),Buffer.from(r.data,'base64'));};
    await call('Runtime.enable');await call('Page.enable');
    await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
    await call('Page.navigate',{url:`http://127.0.0.1:${server.address().port}`});
    for(let i=0;i<60;i++){if(await evaluate(`typeof state!=='undefined'&&state.status!==null&&state.jobs.length>0`))break;await delay(100);}
    checks.initial=await evaluate(`({estimate:document.querySelector('#jobs').textContent,overflow:document.documentElement.scrollWidth>innerWidth})`);
    assert.match(checks.initial.estimate,/预计总耗时约/);assert.match(checks.initial.estimate,/预计采样剩余约/);assert.equal(checks.initial.overflow,false);
    checks.studio=await evaluate(`({memory:$('#studio-memory-value').textContent,gpu:$('#studio-gpu-value').textContent,loadDisabled:$('#studio-model-load').disabled,releaseDisabled:$('#studio-model-release').disabled,notice:$('.studio-resources').textContent})`);
    assert.equal(checks.studio.memory,'13.00 / 32.00 GiB');assert.equal(checks.studio.gpu,'3.00 / 8.00 GiB');
    assert.equal(checks.studio.loadDisabled,true);assert.equal(checks.studio.releaseDisabled,false);assert.match(checks.studio.notice,/关闭或刷新网页/);
    await evaluate(`$('.studio-resources').scrollIntoView({block:'center',behavior:'instant'})`);await shot('easy-plan-studio-desktop.png');
    await evaluate(`document.querySelector('#jobs').scrollIntoView({block:'center',behavior:'instant'})`);await shot('timing-desktop.png');
    checks.edgeCases=await evaluate(`(()=>{const j=structuredClone(state.jobs[0]);return {queued:jobEstimateMarkup({...j,status:'queued'}),completed:jobEstimateMarkup({...j,status:'completed'}),unknown:jobEstimateMarkup({...j,time_estimate:null}),overrun:jobEstimateMarkup({...j,time_estimate:{...j.time_estimate,remaining_seconds:null,history_exceeded:true,sampling_remaining_seconds:null,note:'步速发生变化，等待新进度重新估算'}})};})()`);
    assert.match(checks.edgeCases.queued,/排队等待另计/);assert.equal(checks.edgeCases.completed,'');assert.match(checks.edgeCases.overrun,/超出参考耗时/);assert.doesNotMatch(checks.edgeCases.overrun,/剩余约 0/);
    await evaluate(`navigate('models');document.querySelector('.resource-panel').scrollIntoView({block:'center',behavior:'instant'})`);
    checks.resources=await evaluate(`({memory:document.querySelector('#resource-process-memory').textContent,gpu:document.querySelector('#resource-process-gpu').textContent,note:document.querySelector('#resource-gpu-note').textContent,unsupported:document.querySelector('#model-cards').textContent.includes('独立加载 / 卸载暂不支持'),eager:!!document.querySelector('#load-form [name=eager_load]')})`);
    assert.equal(checks.resources.memory,'8.00 GiB');assert.equal(checks.resources.gpu,'未能读取');assert.match(checks.resources.note,/WDDM/);assert.ok(checks.resources.unsupported&&checks.resources.eager);
    await shot('resources-desktop.png');
    await evaluate(`document.querySelector('#model-toggle').click()`);await delay(250);
    assert.equal(writes.at(-1).url,'/api/models/unload');
    assert.equal(await evaluate(`document.querySelector('#resource-process-memory').textContent`),'0.00 GiB');
    checks.release=await evaluate(`$('#studio-release-note').textContent`);
    assert.match(checks.release,/物理内存 8.00 GiB/);assert.match(checks.release,/显存 未能读取/);
    await evaluate(`navigate('studio');$('#studio-model-load').click()`);await delay(250);
    assert.equal(writes.at(-1).url,'/api/models/load');assert.equal(writes.at(-1).body.eager_load,true);
    assert.equal(await evaluate(`$('#studio-model-release').textContent`),'取消加载并释放');
    await evaluate(`$('#studio-model-release').click()`);await delay(250);
    assert.equal(writes.at(-1).url,'/api/models/unload');
    await evaluate(`navigate('models')`);
    await evaluate(`document.querySelector('#load-form [name=eager_load]').checked=true;document.querySelector('#model-toggle').click()`);await delay(250);
    assert.equal(writes.at(-1).url,'/api/models/load');assert.equal(writes.at(-1).body.eager_load,true);
    checks.loading=await evaluate(`({text:document.querySelector('#model-toggle').textContent,disabled:document.querySelector('#model-toggle').disabled})`);
    assert.match(checks.loading.text,/取消加载并释放/);assert.equal(checks.loading.disabled,false);
    await evaluate(`document.querySelector('#model-toggle').click()`);await delay(250);assert.equal(writes.at(-1).url,'/api/models/unload');
    // The sidebar keeps an unload entry on every page, independent of the models view.
    checks.sidebarUnload=await evaluate(`(()=>{const original=state.status;navigate('gallery');renderStatus({...original,engine:{...original.engine,state:'ready',pid:12345,loaded:true,adopted:true}});const shown={text:$('#engine-mini-state').textContent,disabled:$('#engine-unload').disabled,visible:$('#engine-mini').getBoundingClientRect().height>0};renderStatus({...original,engine:{...original.engine,state:'idle',pid:null,loaded:false}});const idle={disabled:$('#engine-unload').disabled};renderStatus(original);navigate('studio');return {shown,idle};})()`);
    assert.match(checks.sidebarUnload.shown.text,/PID 12345/);assert.equal(checks.sidebarUnload.shown.disabled,false);assert.equal(checks.sidebarUnload.shown.visible,true);assert.equal(checks.sidebarUnload.idle.disabled,true);
    checks.busy=await evaluate(`(()=>{const original=state.status;renderStatus({...original,engine:{...original.engine,active_job:'test'}});const result={model:$('#model-toggle').disabled,load:$('#studio-model-load').disabled,release:$('#studio-model-release').disabled};renderStatus(original);return result;})()`);assert.deepEqual(checks.busy,{model:true,load:true,release:true});
    checks.monitorFailure=await evaluate(`(()=>{const original=state.status;renderStatus({...original,gpu:null,memory:{}});const result={memory:$('#studio-memory-value').textContent,gpu:$('#studio-gpu-value').textContent,mainBar:$('#studio-gpu-bar').style.width,sideBar:$('#gpu-bar').style.width};renderStatus(original);return result;})()`);
    assert.deepEqual(checks.monitorFailure,{memory:'未能读取',gpu:'未能读取',mainBar:'0%',sideBar:'0%'});
    checks.componentCards=await evaluate(`(()=>{const original=state.status;renderStatus({...original,models:{...original.models,individual_preload_supported:true,components:original.models.components.map(m=>({...m,residency:{supported:true,tensors:10,loaded_tensors:5,weight_bytes:2*1024**3,loaded_weight_bytes:1024**3,host_buffer_bytes:1024**3,device_buffer_bytes:0}}))}});return {text:$('#model-cards').textContent,buttons:document.querySelectorAll('.component-actions button').length};})()`);
    assert.match(checks.componentCards.text,/部分权重已加载/);assert.match(checks.componentCards.text,/权重缓冲区内存 1.00 GiB/);assert.equal(checks.componentCards.buttons,16);
    await evaluate(`navigate('studio')`);
    checks.studioComponents=await evaluate(`({visible:$('#studio-components').getBoundingClientRect().height>0,count:$$('#studio-components button').length})`);
    assert.deepEqual(checks.studioComponents,{visible:true,count:8});
    await evaluate(`document.querySelector('[data-action="component-load"][data-key="vae"]').click()`);await delay(250);
    assert.equal(writes.at(-1).url,'/api/models/components/vae/load');
    await evaluate(`document.querySelector('[data-action="component-release"][data-key="vae"]').click()`);await delay(250);
    assert.equal(writes.at(-1).url,'/api/models/components/vae/release');
    checks.powerProtection=await evaluate(`(()=>{const original=state.status;renderStatus({...original,engine:{...original.engine,power:{ac_connected:false,on_battery:true,battery_percent:60,protection_enabled:true,blocked:true,last_event:{timestamp:Date.now()/1000,reason:'电池供电保护'}}}});const result={text:$('#power-status').textContent,generate:$('#generate-button').disabled,load:$('#studio-model-load').disabled,component:document.querySelector('[data-action="component-load"][data-key="vae"]').disabled};renderStatus(original);return result;})()`);
    assert.match(checks.powerProtection.text,/电池供电/);assert.match(checks.powerProtection.text,/手动重新生成/);assert.equal(checks.powerProtection.generate,true);assert.equal(checks.powerProtection.load,true);assert.equal(checks.powerProtection.component,true);
    await evaluate(`navigate('models');$('#model-cards').scrollIntoView({block:'start',behavior:'instant'})`);await shot('components-desktop.png');
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await evaluate(`navigate('models');document.querySelector('.resource-panel').scrollIntoView({block:'center',behavior:'instant'})`);await delay(100);
    checks.mobileResources=await evaluate(`document.documentElement.scrollWidth<=innerWidth`);assert.equal(checks.mobileResources,true);await shot('resources-mobile.png');
    await evaluate(`$('#model-cards').scrollIntoView({block:'start',behavior:'instant'})`);await shot('components-mobile.png');
    await evaluate(`navigate('studio');$('.studio-resources').scrollIntoView({block:'center',behavior:'instant'})`);await delay(100);
    checks.mobileStudio=await evaluate(`document.documentElement.scrollWidth<=innerWidth`);assert.equal(checks.mobileStudio,true);await shot('easy-plan-studio-mobile.png');
    await evaluate(`navigate('studio');document.querySelector('#jobs').scrollIntoView({block:'center',behavior:'instant'})`);await delay(100);
    checks.mobileTiming=await evaluate(`document.documentElement.scrollWidth<=innerWidth`);assert.equal(checks.mobileTiming,true);await shot('timing-mobile.png');
    await evaluate(`navigate('vault')`);await delay(150);
    // Legacy library: nothing may be unlocked or newly encrypted before the one-time re-key.
    assert.equal(await evaluate(`$('#vault-migration-panel').classList.contains('hidden')`),false);
    assert.equal(await evaluate(`$('#vault-grid [data-action="unlock"]').disabled`),true);
    await evaluate(`detail('${sample.id}')`);
    await evaluate(`$('#image-dialog [data-action="encrypt"]').click()`);await delay(150);
    assert.equal(await evaluate(`$('#password-dialog').open`),false);
    assert.equal(writes.some(w=>w.url.endsWith('/encrypt')),false);
    await evaluate(`$('#image-dialog').close()`);
    await evaluate(`$('#vault-new-password').value='new-vault-password-123';$('#vault-new-confirm').value='new-vault-password-123';$('#vault-password-kept').checked=true;$('#vault-keep-unreadable').checked=true;$('#vault-migration-form').requestSubmit()`);await delay(300);
    const migration=writes.find(w=>w.url==='/api/vault/migrate');
    assert.equal(migration.body.keep_unreadable,true);
    assert.equal(vaultStatus.pending,0);
    assert.equal(await evaluate(`$('#vault-new-password').value`),'');
    assert.equal(await evaluate(`$('#vault-migration-panel').classList.contains('hidden')`),true);
    await evaluate(`navigate('studio');preview('${sample.id}')`);
    await evaluate(`detail('${sample.id}')`);
    await evaluate(`$('#image-dialog [data-action="encrypt"]').click()`);await delay(150);
    // No password saved yet: the dialog asks once, and saves it for the next time.
    assert.equal(await evaluate(`$('#password-dialog').open`),true);
    assert.match(await evaluate(`$('#password-title').textContent`),/设置/);
    assert.equal(writes.some(w=>w.url.endsWith('/encrypt')),false);
    await evaluate(`$('#vault-password').value='new-vault-password-123';$('#vault-password-confirm').value='new-vault-password-123';$('#password-form').requestSubmit()`);await delay(500);
    assert.ok(writes.some(w=>w.url==='/api/vault/key'&&w.body.password==='new-vault-password-123'));
    checks.oneClickEncryption=await evaluate(`({passwordDialog:$('#password-dialog').open,preview:state.preview,canvas:$('#canvas').textContent,recent:$('#recent-grid').textContent,detail:$('#image-detail').textContent,gallery:$('#gallery-grid').textContent,favorites:$('#favorite-grid').textContent})`);
    assert.equal(checks.oneClickEncryption.passwordDialog,false);
    assert.equal(checks.oneClickEncryption.preview,null);
    for(const field of ['canvas','recent','detail','gallery','favorites'])assert.doesNotMatch(checks.oneClickEncryption[field],/private-test|private sample/);
    const encryption=writes.find(w=>w.url.endsWith('/encrypt'));
    assert.deepEqual(encryption.body,{});
    assert.equal(vaultStatus.key_saved,true);
    await evaluate(`navigate('vault')`);await delay(150);await shot('vault-mobile.png');
    assert.equal(await evaluate(`$$('#vault-grid [data-action="unlock"]').length`),1);
    assert.equal(await evaluate(`$('#vault-grid [data-action="unlock"]').disabled`),false);
    await evaluate(`$('#vault-grid [data-action="unlock"]').click()`);await delay(250);
    // The saved password opens it directly: no prompt at all.
    assert.equal(await evaluate(`$('#password-dialog').open`),false);
    assert.equal(await evaluate(`$('#private-dialog').open`),true);
    assert.ok(writes.some(w=>w.url.endsWith('/unlock')));
    await evaluate(`$('#private-dialog').close()`);await delay(50);
    assert.equal(await evaluate(`$('#private-preview').textContent`),'');
    await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
    await evaluate(`navigate('studio');window.scrollTo(0,0)`);await delay(100);await shot('studio-components-vault.png');
    // Preset actions are inside the generation form: none may submit a job.
    const submittedBefore=writes.filter(w=>w.url==='/api/jobs').length;
    await evaluate(`$('#content_confirmed').checked=true;$('#open-presets').click()`);await delay(150);
    assert.equal(writes.filter(w=>w.url==='/api/jobs').length,submittedBefore);
    await evaluate(`$('#preset-menu [data-action="use-preset"]').click()`);await delay(150);
    assert.equal(await evaluate(`$('#prompt').value`),'preset regression');
    assert.equal(await evaluate(`$('#steps').value`),'12');
    assert.equal(writes.filter(w=>w.url==='/api/jobs').length,submittedBefore);
    await evaluate(`$('#content_confirmed').checked=true;$('#open-presets').click()`);await delay(150);
    await evaluate(`window.confirm=()=>true;$('#preset-menu [data-action="delete-preset"]').click()`);await delay(150);
    assert.equal(presets.length,0);
    assert.equal(writes.filter(w=>w.url==='/api/jobs').length,submittedBefore);
    await evaluate(`$('#generation-form').requestSubmit()`);await delay(100);
    assert.equal(writes.filter(w=>w.url==='/api/jobs').length,submittedBefore);
    await evaluate(`$('#jobs [data-action="cancel"]').click()`);await delay(150);
    checks.cancelPlainError=await evaluate(`$('#toast').textContent`);
    assert.match(checks.cancelPlainError,/HTTP 500/);
    assert.doesNotMatch(checks.cancelPlainError,/Unexpected token/);
    assert.equal(await evaluate(`$('#jobs [data-action="cancel"]').disabled`),false);
    await evaluate(`$('#jobs [data-action="cancel"]').click()`);await delay(150);
    assert.equal(await evaluate(`$('#jobs [data-action="cancel"]').disabled`),true);
    assert.equal(await evaluate(`$('#jobs [data-action="cancel"]').textContent`),'正在取消…');
    checks.presetActionsDoNotGenerate=true;
    // Refresh restores drafts but never replays model mutations or generation.
    status.engine={...status.engine,state:'ready',active_job:null,queued:0,pid:12345,component_operation:null};
    status.models.individual_preload_supported=true;
    status.models.components.forEach((m,i)=>{
      m.state='available';
      m.residency={supported:true,tensors:10,loaded_tensors:i===0?0:10,weight_bytes:GiB,loaded_weight_bytes:i===0?0:GiB,host_buffer_bytes:i===0?0:GiB,device_buffer_bytes:0};
    });
    await evaluate(`$('#prompt').value='refresh draft';$('#negative_prompt').value='blur';$('#width').value='1000';$('#height').value='768';$('#steps').value='23';$('#seed').value='987';$('#output-bit-depth').value='16';$('#content_confirmed').checked=true;state.input='b'.repeat(32);$('#input-label').textContent='reference.png';$('#advanced-settings').open=true;$('#steps').dispatchEvent(new Event('input',{bubbles:true}))`);
    const mutationsBeforeReload=writes.filter(w=>w.url.startsWith('/api/models/')||w.url==='/api/jobs').length;
    await call('Page.reload',{ignoreCache:true});
    for(let i=0;i<80;i++){if(await evaluate(`typeof state!=='undefined'&&state.status?.engine?.pid===12345&&state.connected`))break;await delay(100);}
    checks.refreshDraft=await evaluate(`({prompt:$('#prompt').value,width:$('#width').value,steps:$('#steps').value,seed:$('#seed').value,depth:$('#output-bit-depth').value,input:state.input,confirmed:$('#content_confirmed').checked,advanced:$('#advanced-settings').open})`);
    assert.deepEqual(checks.refreshDraft,{prompt:'refresh draft',width:'1000',steps:'23',seed:'987',depth:'16',input:'b'.repeat(32),confirmed:true,advanced:true});
    assert.equal(writes.filter(w=>w.url.startsWith('/api/models/')||w.url==='/api/jobs').length,mutationsBeforeReload);
    assert.equal(await evaluate(`$('#studio-components [data-key="diffusion_model"][data-action="component-load"]').disabled`),false);
    assert.equal(await evaluate(`$('#studio-components [data-key="vae"][data-action="component-release"]').disabled`),false);
    status.engine.component_operation={key:'diffusion_model',action:'load'};
    await call('Page.reload',{ignoreCache:true});
    for(let i=0;i<80;i++){if(await evaluate(`typeof state!=='undefined'&&state.status?.engine?.component_operation?.key==='diffusion_model'`))break;await delay(100);}
    assert.equal(await evaluate(`$('#studio-components [data-key="vae"][data-action="component-release"]').disabled`),true);
    assert.match(await evaluate(`$('#studio-model-operation').textContent`),/正在加载扩散模型/);
    status.engine.component_operation=null;
    await evaluate(`refreshStatus()`);
    assert.equal(await evaluate(`$('#studio-components [data-key="vae"][data-action="component-release"]').disabled`),false);
    checks.statusRaces=await evaluate(`(async()=>{const originalApi=api;const current=structuredClone(state.status);let resolveOld;try{api=path=>path==='/status'?new Promise(resolve=>resolveOld=resolve):originalApi(path);const old=refreshStatus();api=async()=>current;await refreshStatus();resolveOld({...current,engine:{...current.engine,state:'loading',component_operation:{key:'vae',action:'release'}}});await old;const staleIgnored=state.status.engine.component_operation===null;api=async path=>{if(path==='/jobs')throw new Error('temporary job list failure');return current;};await poll();const jobsIndependent=state.connected&&!$('#studio-components [data-key="vae"][data-action="component-release"]').disabled;api=async()=>{throw new Error('offline');};await refreshStatus().catch(()=>{});const offlineDisabled=$('#studio-components [data-key="vae"][data-action="component-release"]').disabled;api=async()=>current;await refreshStatus();return {staleIgnored,jobsIndependent,offlineDisabled,recovered:!$('#studio-components [data-key="vae"][data-action="component-release"]').disabled};}finally{api=originalApi;}})()`);
    assert.deepEqual(checks.statusRaces,{staleIgnored:true,jobsIndependent:true,offlineDisabled:true,recovered:true});
    checks.exceptions=errors;assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(artifacts,'resources-timing-browser.json'),JSON.stringify(checks,null,2));
    console.log(JSON.stringify({passed:true,mobileResources:checks.mobileResources,mobileTiming:checks.mobileTiming,exceptions:errors.length}));
  }finally{
    if(call)await Promise.race([call('Browser.close').catch(()=>{}),delay(1000)]);
    if(ws)ws.close();browser.kill();await new Promise(resolve=>server.close(resolve));
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
