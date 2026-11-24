// Browser checks against an isolated application and temporary image store. No model is loaded.
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

  await call('Runtime.enable');await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:base+'/#gallery'});
  async function waitFor(expression){for(let i=0;i<80;i++){try{if(await evaluate(expression))return;}catch{}await delay(100);}await shot('gallery-failure.png');console.error(await evaluate("({focus:document.activeElement.outerHTML,toast:document.querySelector('#toast').textContent})"));fail('timeout: '+expression);}
  await waitFor("document.querySelectorAll('#gallery-grid .image-card').length===2");
  const quick='#gallery-grid .image-card:first-child .card-quick-actions';
  check(await evaluate(`getComputedStyle(document.querySelector('${quick}')).opacity==='0'`),'toolbar initially hidden');
  const rect=await evaluate(`(()=>{const r=document.querySelector('#gallery-grid .card-image').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',...rect});await delay(300);
  check(await evaluate(`getComputedStyle(document.querySelector('${quick}')).opacity==='1'`),'toolbar appears on hover');
  await shot('gallery-hover.png');
  const id=await evaluate("document.querySelector('#gallery-grid [data-tag-kind=like]').dataset.id");
  async function click(selector){
    await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.focus({preventScroll:true});(e.closest('.image-card')||e).scrollIntoView({block:'center',behavior:'instant'});})()`);
    await delay(300);
    await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'nearest',behavior:'instant'})`);
    const r=await evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
    await call('Input.dispatchMouseEvent',{type:'mouseMoved',...r});
    await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...r});
    await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...r});
  }
  for(const tag of ['like','dislike','favorite']){
    await click(`${quick} [data-tag-kind=${tag}]`);
    await waitFor(`!state.tagPending?.size && document.querySelector('${quick} [data-tag-kind=${tag}]').getAttribute('aria-pressed')==='true'`);
    check(!await evaluate("document.querySelector('#image-dialog').open"),'quick action must not open details');
    check((await(await fetch(base+'/api/images/'+id)).json()).tag===tag,'tag persisted');
  }
  await click(quick+' [data-tag-kind=favorite]');
  await waitFor(`!state.tagPending?.size && document.querySelector('${quick} [data-tag-kind=favorite]').getAttribute('aria-pressed')==='false'`);
  await click(quick+' [data-tag-kind=like]');
  await waitFor("!state.tagPending?.size");
  await click('#gallery-filters [data-filter=like]');
  await waitFor("document.querySelectorAll('#gallery-grid .image-card').length===1");
  await click('#gallery-filters [data-filter=all]');
  await waitFor("document.querySelectorAll('#gallery-grid .image-card').length===2");
  await call('Page.reload',{ignoreCache:true});
  await waitFor(`document.querySelector('${quick} [data-tag-kind=like]')?.getAttribute('aria-pressed')==='true'`);
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:1,y:1});
  await evaluate(`document.querySelector('${quick} [data-tag-kind=like]').focus()`);await delay(300);
  check(await evaluate(`getComputedStyle(document.querySelector('${quick}')).opacity==='1'`),'keyboard focus reveals toolbar');
  // Failure leaves the previous state and allows retry.
  await evaluate(`window.realFetch=window.fetch;window.fetch=(url,options)=>String(url).endsWith('/tag')?Promise.resolve(new Response(JSON.stringify({detail:'模拟保存失败'}),{status:500})):window.realFetch(url,options);document.querySelector('${quick} [data-tag-kind=like]').click()`);
  await waitFor("document.querySelector('#toast').textContent==='模拟保存失败' && !state.tagPending?.size");
  check(await evaluate(`!document.querySelector('${quick} [data-tag-kind=like]').disabled && document.querySelector('${quick} [data-tag-kind=like]').getAttribute('aria-pressed')==='true'`),'failed save preserves state and enables retry');
  await evaluate('window.fetch=window.realFetch');
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await call('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
  await evaluate('document.activeElement.blur();window.scrollTo(0,0)');await delay(300);
  check(await evaluate(`getComputedStyle(document.querySelector('${quick}')).opacity==='1' && getComputedStyle(document.querySelector('${quick}')).position==='static'`),'touch toolbar always visible');
  check(await evaluate('document.documentElement.scrollWidth<=innerWidth'),'mobile no overflow');
  await shot('gallery-mobile.png');
  await evaluate("document.body.classList.add('light')");await shot('gallery-mobile-light.png');
  check(await evaluate("!card(state.images[0],{trash:true}).includes('card-quick-actions')"),'trash has no actions');


  // Every action is directly available on the card; no More dialog or extra click.
  check(await evaluate("!document.querySelector('[data-action=image-actions],#image-actions-dialog')"),'no secondary action page');
  check(await evaluate(`['detail','preview','compare-select','reuse','export','encrypt','trash'].every(action=>document.querySelector('${quick} [data-action="'+action+'"]'))`),'all image actions on card');
  check(await evaluate(`['favorite','like','dislike','blocked'].every(tag=>document.querySelector('${quick} [data-tag-kind="'+tag+'"]'))`),'all tags on card');
  check(await evaluate(`imageActionsMarkup({...state.images[0],bit_depth:16},{compact:true}).includes('/export?depth=8') && !imageActionsMarkup({...state.images[0],bit_depth:8},{compact:true}).includes('/export?depth=8')`),'8 bit export offered only for 16 bit images');
  const link=await evaluate(`document.querySelector('${quick} a').getAttribute('href')`);
  check((await fetch(base+link)).status===200,'download link works');
  await evaluate(`window.savedJSON=null;downloadJSON=(data,name)=>{window.savedJSON={data,name}}`);
  await click(quick+' [data-action=export]');
  await waitFor('window.savedJSON!==null');
  check(await evaluate(`window.savedJSON.data.id===${JSON.stringify(id)} && window.savedJSON.name.endsWith('.json')`),'configuration export uses the correct image');
  await click(quick+' [data-action=compare-select]');
  check(await evaluate(`state.selected.has(${JSON.stringify(id)}) && document.querySelector('#gallery-grid [data-select="${id}"]').checked`),'comparison selection synchronized');
  await click('#clear-compare');
  check(await evaluate(`state.selected.size===0 && document.querySelector('${quick} [data-action=compare-select]').getAttribute('aria-pressed')==='false'`),'comparison button follows clear-selection action');
  await click('#gallery-grid .card-select input');
  check(await evaluate(`document.querySelector('${quick} [data-action=compare-select]').textContent==='取消比较'`),'checkbox updates quick compare button');
  await click(quick+' [data-action=compare-select]');
  check(await evaluate('state.selected.size===0'),'comparison toggle');
  await click(quick+' [data-tag-kind=favorite]');
  await waitFor(`!state.tagPending?.size && document.querySelector('${quick} [data-tag-kind=favorite]').getAttribute('aria-pressed')==='true'`);
  await click(quick+' [data-tag-kind=blocked]');
  await waitFor("!state.tagPending?.size && document.querySelectorAll('#gallery-grid .image-card').length===1");
  await click('#gallery-filters [data-filter=blocked]');
  await waitFor(`document.querySelector('${quick} [data-tag-kind=blocked]')?.textContent==='取消隐藏'`);
  await click(quick+' [data-tag-kind=blocked]');
  await waitFor("!state.tagPending?.size && !document.querySelector('#gallery-grid .image-card')");
  await click('#gallery-filters [data-filter=all]');
  await waitFor("document.querySelectorAll('#gallery-grid .image-card').length===2");
  await click(quick+' [data-action=detail]');
  await waitFor("document.querySelector('#image-dialog').open");
  await click('#image-dialog [data-close]');
  await click(quick+' [data-action=encrypt]');
  await waitFor("document.querySelector('#password-dialog').open");
  await click('#password-dialog [data-close]');
  await evaluate('window.confirm=()=>false');
  await click(quick+' [data-action=trash]');
  check((await(await fetch(base+'/api/images')).json()).length===2,'cancelled trash leaves image intact');
  await evaluate('window.confirm=()=>true');
  await click(quick+' [data-action=trash]');
  await waitFor("document.querySelectorAll('#gallery-grid .image-card').length===1");
  await click('#gallery-filters [data-filter=trash]');
  await waitFor("document.querySelector('#gallery-grid [data-action=restore]')");
  await click('#gallery-grid [data-action=restore]');
  await waitFor("!document.querySelector('#gallery-grid .image-card')");
  await click('#gallery-filters [data-filter=all]');
  await waitFor("document.querySelectorAll('#gallery-grid .image-card').length===2");
  await call('Emulation.setTouchEmulationEnabled',{enabled:false});
  await evaluate("document.body.classList.remove('light');document.querySelector('#toast').classList.remove('visible')");
  const layouts=[];
  for(const width of [1280,1000,800,650,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:1000,deviceScaleFactor:1,mobile:false});
    // Add the extra 16 bit export control to test the tallest possible action panel.
    await evaluate(`document.querySelector('${quick}').innerHTML=imageActionsMarkup({...state.images[0],bit_depth:16},{compact:true});document.querySelector('#gallery-grid .image-card').scrollIntoView({block:'center',behavior:'instant'});document.querySelector('${quick} button').focus({preventScroll:true})`);
    await delay(300);
    const result=await evaluate(`(()=>{
      const panel=document.querySelector('${quick}'),media=panel.closest('.card-media'),r=media.getBoundingClientRect();
      const buttons=[...panel.querySelectorAll('button,a')];
      return {count:buttons.length,width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,
        fits:buttons.every(b=>{const q=b.getBoundingClientRect();return q.left>=r.left-1&&q.right<=r.right+1&&q.top>=r.top-1&&q.bottom<=r.bottom+1&&b.scrollWidth<=b.clientWidth+1}),
        reachable:buttons.every(b=>{const q=b.getBoundingClientRect();const hit=document.elementFromPoint(q.x+q.width/2,q.y+q.height/2);return hit===b||b.contains(hit)})};})()`);
    check(result.count===13&&result.fits&&!result.overflow&&result.reachable,'all controls visible and reachable at '+width+': '+JSON.stringify(result));
    layouts.push(result);
    if(width===1280)await shot('gallery-all-actions-desktop.png');
  }
  await evaluate('refreshGallery()');
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
  await click(quick+' [data-action=preview]');
  await waitFor("state.view==='studio' && state.preview!==null");
  await evaluate("navigate('gallery')");
  await waitFor("document.querySelectorAll('#gallery-grid .image-card').length===2");
  await click(quick+' [data-action=reuse]');
  await waitFor("state.view==='studio'");
  check((await(await fetch(base+'/api/jobs')).json()).length===0,'reuse must not generate');
  check(exceptions.length===0,'no browser exceptions');
  fs.writeFileSync(outJson,JSON.stringify({hover:true,keyboard:true,touch:true,tagPersistence:true,cancel:true,filter:true,errorRetry:true,reuse:true,allActions:true,noSecondaryPage:true,layouts,downloads:true,exportConfig:true,hide:true,compare:true,encryptDialog:true,trashRestore:true,exceptions},null,2));
  console.log('Gallery browser checks passed');ws.close();
}
main().catch(error=>{console.error(error.stack||String(error));process.exit(1)});
