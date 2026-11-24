'use strict';
const $ = (s, base = document) => base.querySelector(s);
const $$ = (s, base = document) => [...base.querySelectorAll(s)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const gib = value => (value / 1024 ** 3).toFixed(2);
const date = value => new Date(value * 1000).toLocaleString('zh-CN', {hour12:false});
const state = {token:'', settings:null, status:null, images:[], jobs:[], selected:new Set(), filter:'all', preview:null, input:null, gpuHistory:[], view:'studio', connected:false, modelActionBusy:false};
const titles = {video:'视频工作台', studio:'创作工作台', upscale:'AI 放大', outpaint:'AI 扩图', gallery:'我的图库', favorites:'收藏与预设', models:'模型与显存', vault:'私密库', settings:'设置'};
const imageToolInstances = {};
let toastTimer, searchTimer, passwordAction;
let previewHidden=false;
let generateBusy=false;
let statusRequestId=0,draftReady=false,draftWarning=false;
const DRAFT_KEY='qwen-generation-draft-v1';
const DRAFT_FIELDS=['prompt','negative_prompt','width','height','steps','cfg','seed','batch_count','sampler','scheduler','vae_tiling','output-bit-depth','content_confirmed'];

function saveDraft() {
  if(!draftReady)return;
  const fields=Object.fromEntries(DRAFT_FIELDS.map(id=>{const el=$('#'+id);return [id,el.type==='checkbox'?el.checked:el.value];}));
  try{localStorage.setItem(DRAFT_KEY,JSON.stringify({fields,input:state.input,inputLabel:state.input?$('#input-label').textContent:null,advanced:$('#advanced-settings').open}));}
  catch{if(!draftWarning){draftWarning=true;toast('浏览器无法保存参数草稿，请使用「保存参数」备份。');}}
}
function restoreDraft() {
  try{
    const draft=JSON.parse(localStorage.getItem(DRAFT_KEY)||'null');
    if(draft?.fields&&typeof draft.fields==='object'){
      for(const id of DRAFT_FIELDS){
        const el=$('#'+id),value=draft.fields[id];
        if(el.type==='checkbox'){if(typeof value==='boolean')el.checked=value;}
        else if(typeof value==='string'&&(el.tagName!=='SELECT'||[...el.options].some(o=>o.value===value)))el.value=value;
      }
      if(typeof draft.input==='string'&&/^[a-f0-9]{32}$/.test(draft.input)){
        state.input=draft.input;$('#input-label').textContent=typeof draft.inputLabel==='string'?draft.inputLabel:'已恢复参考图片';$('#clear-input').classList.remove('hidden');
      }
      $('#advanced-settings').open=draft.advanced===true;
    }
  }catch{toast('参数草稿无法读取，已使用默认参数。');}
  updatePromptCount();updateRatio();updateBitDepth(state.status);draftReady=true;
}

function toast(message) {
  $('#toast').textContent = message; $('#toast').classList.add('visible');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 4500);
}
async function api(path, {method='GET', body} = {}) {
  const headers = {'X-Studio-Token':state.token};
  if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const r = await fetch('/api' + path, {method, headers, ...(method==='GET'&&['/status','/jobs'].includes(path)?{signal:AbortSignal.timeout(10000)}:{}),body:body instanceof FormData ? body : body ? JSON.stringify(body) : undefined});
  const raw = await r.text();
  let data;
  try { data = raw ? JSON.parse(raw) : null; }
  catch {
    throw new Error(r.ok?'服务返回了无法识别的数据，请刷新页面后重试。':`请求失败（HTTP ${r.status}）：服务返回异常，请查看服务日志后重试。`);
  }
  if (!r.ok) {
    const detail = data?.detail;
    throw new Error(Array.isArray(detail) ? detail.map(e => `${e.loc.at(-1)}：${e.msg}`).join('；') : typeof detail==='string'?detail:`请求失败（HTTP ${r.status}）`);
  }
  return data;
}
function safe(action) { return async event => {try {await action(event);} catch(error) {toast(error.message);}}; }

function empty(title, text, icon='◇') {return `<div class="empty-state"><span>${icon}</span><h3>${esc(title)}</h3><p>${esc(text)}</p></div>`;}

function navigate(view) {
  if (!titles[view]) view = 'studio';
  if(view!=='vault'){$('#private-dialog').close();clearVaultPasswords();}
  state.view = view;
  videoPanel.navigate(view === 'video' && !!state.token);
  for (const mode of ['upscale', 'outpaint']) {
    if (view !== mode && imageToolInstances[mode]) {
      imageToolInstances[mode].destroy();
      delete imageToolInstances[mode];
    }
  }
  if (['upscale', 'outpaint'].includes(view) && state.token && !imageToolInstances[view]) {
    imageToolInstances[view] = QwenImageTools.mount({root:$('#' + view + '-tools'), mode:view, api, notify:toast});
  }
  $$('.view').forEach(e => e.classList.toggle('active', e.id === 'view-' + view));
  $$('.nav-item').forEach(e => e.classList.toggle('active', e.dataset.view === view));
  $('#page-title').textContent = titles[view];
  if (location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
  if (view === 'gallery') refreshGallery().catch(e => toast(e.message));
  if (view === 'favorites') refreshFavorites().catch(e => toast(e.message));
  if (view === 'vault') refreshVault().catch(e => toast(e.message));
  if (view === 'settings' && state.settings) fillSettings();
  if (view === 'models' && state.settings) {fillLoadForm();refreshProfiles().catch(error => toast(error.message));}
  window.scrollTo({top:0, behavior:'instant'});
}

function formConfig() {
  return {prompt:$('#prompt').value.trim(), negative_prompt:$('#negative_prompt').value,
    width:+$('#width').value, height:+$('#height').value, steps:+$('#steps').value, cfg:+$('#cfg').value,
    seed:+$('#seed').value, batch_count:+$('#batch_count').value, sampler:$('#sampler').value,
    scheduler:$('#scheduler').value, vae_tiling:$('#vae_tiling').checked,
    output_bit_depth:+($('#output-bit-depth')?.value||8),
    content_confirmed:$('#content_confirmed').checked, input_id:state.input};
}
function restoreConfig(c) {
  const aliases = {...c, sampler:c.sampler || c.sampler_name || 'euler'};
  for (const key of ['prompt','negative_prompt','width','height','steps','cfg','seed','batch_count','sampler','scheduler']) {
    const el = $('#' + key);
    if (aliases[key] !== undefined && aliases[key] !== null) {
      if (el.tagName === 'SELECT' && ![...el.options].some(o => o.value === aliases[key])) continue;
      el.value = aliases[key];
    }
  }
  $('#vae_tiling').checked = aliases.vae_tiling ?? true;
  // Keep the requested depth while unloaded; the backend validates it after loading.
  const depthSelect = $('#output-bit-depth');
  const requestedDepth = c.output_bit_depth === 16 ? 16 : 8;
  depthSelect.value = String(requestedDepth);
  updateBitDepth(state.status);
  // Reusing a preset never silently reuses an unseen reference image.
  clearInput(); $('#content_confirmed').checked = false;
  updatePromptCount(); updateRatio(); updateGenerateAvailability();
  const model = c.model || c.models?.diffusion_model?.path;
  const depthState = state.status?.png16?.state;
  const depthNote = requestedDepth === 16 && depthState !== 'supported'
    ? (depthState === 'unsupported' ? '；已保留 16 bit 参数，但当前引擎不支持，生成会被拒绝；可手动选择 8 bit'
                                    : '；已保留 16 bit 参数，生成时加载引擎后验证支持情况')
    : '';
  toast((model ? '参数已回填；请核对当前模型、参考图与内容约定。' : '参数已回填，请核对后生成。') + depthNote);
  navigate('studio');
}
function updatePromptCount() {$('#prompt-count').textContent = `${$('#prompt').value.length} 字`;saveDraft();}
function updateRatio() {
  const size = `${$('#width').value},${$('#height').value}`;
  let matched = false;
  $$('.ratio[data-ratio]').forEach(b => {const on = b.dataset.ratio === size;if(on)matched = true;b.classList.toggle('selected', on);});
  $('#ratio-custom').classList.toggle('active', !matched);
  saveDraft();
}
// 对齐到 8 的倍数（Qwen Image 的 VAE 以 8 倍下采样），范围 256–4096。
function snapSize(value, fallback = 1024) {
  const n = Math.round(Number(value));
  if (!Number.isFinite(n)) return fallback;
  return Math.min(4096, Math.max(256, n - ((n % 8) + 8) % 8));
}
function clearInput() {state.input=null;$('#reference-file').value='';$('#input-label').innerHTML='添加一张参考图<small>PNG / JPG / WebP · 最大 25 MB</small>';$('#clear-input').classList.add('hidden');saveDraft();updateGenerateAvailability();}

function imageTagButton(image, tag, label, icon, className='button subtle') {
  const active=image.tag===tag;
  return `<button type="button" class="${className}${active?' is-active':''}" data-action="tag" data-id="${image.id}" data-tag="${active?'none':tag}" data-tag-kind="${tag}" aria-pressed="${active}" title="${active?'取消':''}${label}"><span aria-hidden="true">${icon}</span> ${active?'已':''}${label}</button>`;
}
function card(image, {trash=false}={}) {
  return `<article class="image-card ${state.selected.has(image.id)?'selected':''}">
    ${trash?'<div class="trash-placeholder">⌫</div>':`<div class="card-media"><button class="card-image" data-action="detail" data-id="${image.id}" aria-label="查看 ${esc(image.name)}"><img src="/api/images/${image.id}/file?thumb=true" loading="lazy" alt="${esc(image.name)}"></button><label class="card-select"><input type="checkbox" data-select="${image.id}" ${state.selected.has(image.id)?'checked':''}> 比较</label><button class="card-favorite ${image.tag==='favorite'?'is-favorite':''}" data-action="favorite" data-id="${image.id}" data-tag="${image.tag}" title="${image.tag==='favorite'?'取消收藏':'收藏'}" aria-label="${image.tag==='favorite'?'取消收藏':'收藏'}" aria-pressed="${image.tag==='favorite'}">${image.tag==='favorite'?'♥':'♡'}</button><div class="card-quick-actions" role="group" aria-label="图片快捷操作">
      ${imageActionsMarkup(image, {compact:true})}
    </div></div>`}
    <div class="image-card-info"><h3 title="${esc(image.name)}">${esc(image.name)}</h3><div><span>${image.width} × ${image.height}</span><span>${new Date(image.created*1000).toLocaleDateString('zh-CN')}</span></div>${trash?`<button class="button subtle trash-restore" data-action="restore" data-id="${image.id}">恢复文件</button>`:''}</div></article>`;
}
async function refreshImages() {
  state.images = await api('/images');
  if(state.preview&&!state.images.some(im=>im.id===state.preview.id))clearPreview();
  $('#gallery-count').textContent = state.images.length;
  $('#recent-count').textContent = state.images.length;
  updateCanvasNav();
  $('#recent-grid').innerHTML = state.images.slice(0,5).map(im => `<button class="recent-card" data-action="preview" data-id="${im.id}" title="${esc(im.name)}"><img src="/api/images/${im.id}/file?thumb=true" alt="${esc(im.name)}"><span>${im.width} × ${im.height}</span></button>`).join('') || '<div class="muted small">暂无作品，可将图片放入项目的「图片」目录后扫描。</div>';
  if (state.view === 'gallery') await refreshGallery();
  if (state.view === 'favorites') await refreshFavorites();
}
function clearPreview() {
  state.preview=null;
  previewHidden=false;
  $('#canvas').innerHTML=empty('选择一幅作品','私密作品请前往独立的私密库解锁。');
  $('#preview-label').textContent='画布预览';$('#preview-meta').textContent='尚未选择图片';
  $('#preview-details').disabled=true;$('#preview-download').disabled=true;
  $('#canvas-props').classList.add('hidden');$('#canvas-props-panel').classList.add('hidden');
  $('#canvas-props-body').replaceChildren();$('#canvas-props-name').textContent='';
  updateCanvasNav();
  updatePreviewVisibility();
}
async function refreshGallery() {
  const images = await api(`/images?tag=${state.filter}&q=${encodeURIComponent($('#gallery-search').value)}`);
  $('#gallery-grid').innerHTML = images.map(im=>card(im,{trash:state.filter==='trash'})).join('') || empty('这里还没有作品', state.filter==='trash'?'删除的图片会保留在回收站，可随时恢复。':'尝试切换筛选条件，或扫描本地图片。','▦');
}
async function refreshFavorites() {
  const [images,presets] = await Promise.all([api('/images?tag=favorite'),api('/presets')]);
  $('#favorite-grid').innerHTML=images.map(im=>card(im)).join('') || empty('把喜欢的画面留在这里','点击图片上的爱心，即可收藏。','♡');
  $('#preset-grid').innerHTML=presets.map(p=>`<article class="panel preset-card"><h3>${esc(p.name)}</h3><p>${esc(p.config.prompt)}</p><small>${p.config.width} × ${p.config.height} · ${p.config.steps} 步 · CFG ${p.config.cfg}</small><footer><button type="button" class="button subtle" data-action="use-preset" data-id="${p.id}">应用参数 ↗</button><button type="button" class="text-button" data-action="delete-preset" data-id="${p.id}">删除</button></footer></article>`).join('')||empty('收藏一组好参数','在创作设置中点击「保存为预设」。','✦');
}
async function savePreset() {
  const config=formConfig();
  const name=prompt('给这组创作参数起个名字：',config.prompt?config.prompt.slice(0,20):'未命名参数');
  if(!name?.trim())return;
  await api('/presets',{method:'POST',body:{name:name.trim(),config}});
  toast('参数预设已保存');await refreshPresetCount();
  if(!$('#preset-menu').classList.contains('hidden'))await renderPresetMenu();
  if(state.view==='favorites')await refreshFavorites();
}
async function refreshPresetCount() {
  try{const presets=await api('/presets');$('#preset-count').textContent=presets.length;return presets;}catch{return [];}
}
async function renderPresetMenu() {
  const presets=await api('/presets');
  $('#preset-count').textContent=presets.length;
  $('#preset-menu-list').innerHTML=presets.map(p=>`<div class="preset-menu-item"><div><strong>${esc(p.name)}</strong><small>${esc((p.config.prompt||'（无提示词）').slice(0,36))} · ${p.config.width}×${p.config.height} · ${p.config.steps} 步 · CFG ${p.config.cfg} · ${p.config.output_bit_depth===16?'16 bit':'8 bit'}</small></div><button type="button" class="button subtle" data-action="use-preset" data-id="${p.id}">载入</button><button type="button" class="text-button" data-action="delete-preset" data-id="${p.id}">删除</button></div>`).join('')||'<div class="preset-empty">还没有保存过参数。写好后点左侧「保存参数」。</div>';
}

async function preview(identity) {
  const image=await api('/images/'+identity);state.preview=image;
  const source=image.bit_depth===16?`/api/images/${identity}/preview`:`/api/images/${identity}/file`;
  $('#canvas').innerHTML=`<img src="${source}" alt="${esc(image.name)}">`;
  $('#preview-label').textContent=image.config.source==='qwen-studio'?'生成作品':'图库预览 · 历史作品';
  $('#preview-meta').textContent=`${image.width} × ${image.height} · ${image.bit_depth?image.bit_depth+' bit / 通道 · ':''}${(image.size/1024/1024).toFixed(1)} MB · ${image.config.steps?image.config.steps+' 步':'步数未知'}`;
  $('#preview-details').disabled=false;$('#preview-download').disabled=false;
  $('#preview-download').title=image.bit_depth===16?'下载 16 bit 原图':'下载原图';
  $('#canvas-props').classList.remove('hidden');
  if(!$('#canvas-props-panel').classList.contains('hidden'))renderCanvasProps(image);
  updateCanvasNav();
  updatePreviewVisibility();
}
function updatePreviewVisibility() {
  const button=$('#preview-visibility');
  button.disabled=!state.preview;
  button.textContent=previewHidden?'显示图片':'隐藏图片';
  button.setAttribute('aria-pressed',String(previewHidden));
  $('#canvas > img')?.classList.toggle('hidden',previewHidden);
  $('#canvas-hidden-note')?.remove();
  if(previewHidden&&state.preview){
    const note=document.createElement('div');note.id='canvas-hidden-note';note.className='canvas-empty';
    note.textContent='图片已临时隐藏，点击右上角「显示图片」恢复。';
    $('#canvas').append(note);
  }
}
function fileName(path){return String(path||'').split(/[\\/]/).pop();}
function imageProps(image) {
  const c=image.config||{}, models=c.models||{};
  const model=(key,label)=>{
    const m=models[key];
    if(!m||!m.path)return {label,value:'未记录',missing:true};
    return {label,value:`${fileName(m.path)}${m.bytes?` · ${gib(m.bytes)} GiB`:''}`};
  };
  const reference=c.input_id||c.input;
  const groups=[
    {title:'文件',rows:[
      {label:'文件名',value:image.name},
      {label:'尺寸',value:`${image.width} × ${image.height}`},
      {label:'体积',value:image.size>=1048576?`${(image.size/1048576).toFixed(2)} MB`:`${Math.round(image.size/1024)} KB`},
      {label:'文件色深',value:image.bit_depth?`${image.bit_depth} bit / 通道`:'未记录',missing:!image.bit_depth},
      {label:'SHA256',value:`${image.sha256.slice(0,16)}…${image.sha256.slice(-6)}`,full:image.sha256,mono:true}
    ]},
    {title:'提示词',prompt:c.prompt,negative:c.negative_prompt},
    {title:'采样参数',rows:[
      {label:'步数',value:c.steps??'未记录',missing:c.steps==null},
      {label:'CFG',value:c.cfg??'未记录',missing:c.cfg==null},
      {label:'种子',value:c.seed??'未记录',missing:c.seed==null},
      {label:'采样器',value:c.sampler||c.sampler_name||'未记录'},
      {label:'调度器',value:c.scheduler||'未记录'},
      {label:'批量',value:c.batch_count??'未记录'},
      {label:'VAE 分块',value:c.vae_tiling==null?'未记录':(c.vae_tiling?'开':'关')},
      {label:'参考图',value:reference?'已使用参考图':'无'}
    ]},
    {title:'模型',rows:[model('diffusion_model','扩散模型'),model('text_encoder','文本编码器'),model('vae','VAE')]},
    {title:'生成记录',rows:[
      {label:'来源',value:c.source==='qwen-studio'?'本工作台生成':c.source||'历史作品 · 无配置'},
      {label:c.generated_at?'生成时间':'修改时间',value:date(c.generated_at||image.created)},
      {label:'耗时',value:c.elapsed_seconds!=null?`${c.elapsed_seconds.toFixed(1)} 秒`:'未记录'}
    ]}
  ];
  if(image.bit_depth===16){
    groups.splice(1,0,{title:'精度与来源',rows:[
      {label:'引擎声明',value:c.bit_depth_engine_declared!=null?`${c.bit_depth_engine_declared} bit / 通道`:'未记录',missing:c.bit_depth_engine_declared==null},
      {label:'精度来源',value:c.precision_source||'未记录',missing:!c.precision_source},
      {label:'来源校验',value:(c.precision_evidence||[]).join(' · ')||'未记录',missing:!(c.precision_evidence||[]).length},
      {label:'说明',value:'文件色深来自 PNG IHDR，不代表 65536 个有效等级'}
    ]});
  }
  return groups;
}
function propsRow(item) {
  const title=item.full?` title="${esc(item.full)}"`:'';
  return `<div class="props-row"><span>${esc(item.label)}</span><b${title} class="${item.mono?'mono ':''}${item.missing?'props-missing':''}">${esc(item.value)}</b></div>`;
}
function propsPromptRow(label,text) {
  return text?`<div class="props-row"><span>${label}</span><div class="props-prompt">${esc(text)}</div></div>`:'';
}
function renderCanvasProps(image) {
  $('#canvas-props-name').textContent=image.name;
  const actions=image.bit_depth===16
    ? `<div class="props-actions"><a class="button subtle" href="/api/images/${image.id}/file?download=true">下载 16 bit 原图</a><a class="button subtle" href="/api/images/${image.id}/export?depth=8">导出 8 bit</a></div>`
    : `<div class="props-actions"><a class="button subtle" href="/api/images/${image.id}/file?download=true">下载原图</a><span class="small muted">原图为 8 bit，无法导出真正的 16 bit。</span></div>`;
  $('#canvas-props-body').innerHTML=imageProps(image).map(group=>{
    if(group.title==='提示词'){
      const body=propsPromptRow('正向',group.prompt)||`<p class="props-empty">${image.config?.source==='qwen-studio'?'本次生成未填写提示词':'原文件未记录文字提示词'}</p>`;
      return `<section class="props-section"><h4>提示词</h4>${body}${propsPromptRow('反向',group.negative)}</section>`;
    }
    return `<section class="props-section"><h4>${esc(group.title)}</h4>${group.rows.map(propsRow).join('')}</section>`;
  }).join('')+actions;
}
function propsPlainText(image) {
  const lines=[`# ${image.name}`];
  for(const group of imageProps(image)){
    lines.push('',`[${group.title}]`);
    if(group.title==='提示词'){
      if(group.prompt)lines.push(`正向：${group.prompt}`);
      if(group.negative)lines.push(`反向：${group.negative}`);
      if(!group.prompt&&!group.negative)lines.push('未记录');
      continue;
    }
    for(const row of group.rows)lines.push(`${row.label}：${row.full||row.value}`);
  }
  return lines.join('\n');
}
function toggleCanvasProps(open) {
  const panel=$('#canvas-props-panel'),button=$('#canvas-props');
  if(!panel||!button||!state.preview)return;
  const next=open??panel.classList.contains('hidden');
  panel.classList.toggle('hidden',!next);
  button.setAttribute('aria-expanded',next?'true':'false');
  if(next)renderCanvasProps(state.preview);
}
function previewIndex(){return state.images.findIndex(item=>item.id===state.preview?.id);}
function updateCanvasNav() {
  const nav=$('#canvas-nav');if(!nav)return;
  const position=previewIndex(),total=state.images.length;
  const show=!!state.preview&&total>1;
  nav.classList.toggle('hidden',!show);
  if(!show)return;
  $('#canvas-index').textContent=`${position+1} / ${total}`;
  $('#canvas-prev').disabled=position<=0;
  $('#canvas-next').disabled=position>=total-1;
}
async function stepPreview(delta) {
  const position=previewIndex(),next=position+delta;
  if(position<0||next<0||next>=state.images.length)return;
  await preview(state.images[next].id);
}
function imageActionsMarkup(image, {compact=false}={}) {
  const cls=compact?'card-quick-button':'button subtle';
  const button=(action,label,extra='')=>`<button type="button" class="${cls}${action==='trash'?' image-trash-action':''}" data-action="${action}" data-id="${image.id}" ${extra}>${label}</button>`;
  const tags=imageTagButton(image,'favorite','收藏','☆',cls)+imageTagButton(image,'like','喜欢','♡',cls)+imageTagButton(image,'dislike','不喜欢','×',cls);
  const navigation=compact?button('detail','查看详情')+button('preview','画布预览')+button('compare-select',state.selected.has(image.id)?'取消比较':'加入比较',`aria-pressed="${state.selected.has(image.id)}"`):'';
  const download=`<a class="${cls}" href="/api/images/${image.id}/file?download=true" title="${image.bit_depth===16?'下载 16 bit 原图':'下载原图'}">${!compact&&image.bit_depth===16?'下载 16 bit 原图':'下载原图'}</a>`;
  const export8=image.bit_depth===16?`<a class="${cls}${compact?' card-export8':''}" href="/api/images/${image.id}/export?depth=8">导出 8 bit 副本</a>`:'';
  return tags+navigation+button('reuse','复用参数')+download+button('export','导出配置')+button('tag',image.tag==='blocked'?'取消隐藏':'隐藏图片',`data-tag="${image.tag==='blocked'?'none':'blocked'}" data-tag-kind="blocked" aria-pressed="${image.tag==='blocked'}"`)+button('encrypt',compact?'加密':'加密至私密库')+button('trash','移入回收站')+export8;
}
function metadataMarkup(image, actions=true) {
  const c=image.config;
  const fields=[['尺寸',`${image.width} × ${image.height}`],['文件色深',image.bit_depth?`${image.bit_depth} bit / 通道`:'未记录'],['种子',c.seed??'未记录'],['步数',c.steps??'未记录'],['CFG',c.cfg??'未记录'],['采样器',c.sampler||c.sampler_name||'未记录'],['调度器',c.scheduler||'未记录'],['生成耗时',c.elapsed_seconds!=null?c.elapsed_seconds.toFixed(1)+' 秒':'历史图未记录'],[c.generated_at?'生成时间':'文件修改时间',date(c.generated_at||image.created)]];
  if(image.bit_depth===16)fields.push(['引擎声明',c.bit_depth_engine_declared!=null?`${c.bit_depth_engine_declared} bit / 通道`:'未记录'],['精度来源',c.precision_source||'未记录']);
  const depthNote=image.bit_depth===16?'':'<p class="small muted">原图为 8 bit：无法导出真正的 16 bit 文件。</p>';
  return `<div class="detail-info"><h3>画面描述</h3><p class="prompt-text">${esc(c.prompt||'原文件未提供可解析的文字提示词。')}</p>${c.negative_prompt?`<h3>反向提示词</h3><p class="prompt-text">${esc(c.negative_prompt)}</p>`:''}<div class="metadata-grid">${fields.map(([k,v])=>`<div><small>${esc(k)}</small>${esc(v)}</div>`).join('')}</div><h3>模型</h3><p class="small muted">${esc(c.model||c.models?.diffusion_model?.path||'未记录')}</p><h3>SHA256 · 图片文件</h3><div class="hash">${esc(image.sha256)}</div><p class="small muted" style="margin-top:12px">${esc(c.reproducibility||'复用参数不能保证跨软件或跨硬件逐像素复现。')}</p>${actions?`<div class="detail-actions">${imageActionsMarkup(image)}</div>`:''}${depthNote}<details><summary>完整配置与原始元数据</summary><pre>${esc(JSON.stringify({config:c,metadata:image.metadata},null,2))}</pre></details></div>`;
}
async function detail(identity) {
  const image=await api('/images/'+identity);
  const source=image.bit_depth===16?`/api/images/${identity}/preview`:`/api/images/${identity}/file`;
  $('#dialog-title').textContent=image.name;
  $('#image-detail').innerHTML=`<div class="detail-preview"><img src="${source}" alt="${esc(image.name)}"></div>${metadataMarkup(image)}`;
  $('#image-dialog').dataset.id=identity;
  if(!$('#image-dialog').open)$('#image-dialog').showModal();
}
function downloadJSON(data,name) {
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function updateCompare() {
  $('#compare-bar').classList.toggle('hidden',!state.selected.size);
  $('#compare-label').textContent=`已选择 ${state.selected.size} / 4 张`;
  $('#open-compare').disabled=state.selected.size<2;
  $$('[data-select]').forEach(el=>{el.checked=state.selected.has(el.dataset.select);el.closest('.image-card').classList.toggle('selected',el.checked);});
  $$('[data-action="compare-select"]').forEach(el=>{const selected=state.selected.has(el.dataset.id);el.setAttribute('aria-pressed',String(selected));el.textContent=selected?'取消比较':'加入比较';});
}
async function compare() {
  const images=await Promise.all([...state.selected].map(id=>api('/images/'+id)));
  $('#compare-content').innerHTML=images.map(im=>`<article class="compare-column"><img src="/api/images/${im.id}/file" alt="${esc(im.name)}"><h3 class="small" style="margin-top:10px">${esc(im.name)}</h3>${metadataMarkup(im,false)}</article>`).join('');
  $('#compare-dialog').showModal();
}

function updateBitDepth(status) {
  const select=$('#output-bit-depth'),note=$('#output-bit-depth-note');
  if(!select||!note)return;
  const info=(status&&status.png16)||{state:'unknown'};
  const option=select.querySelector('option[value="16"]');
  const supported=info.state==='supported';
  option.disabled=info.state==='unsupported';
  option.textContent=supported?'16 bit / 通道 · 真 16 位输出':info.state==='unsupported'?'16 bit / 通道 · 当前引擎不支持':'16 bit / 通道 · 加载后验证';
  const base=supported?'当前引擎已确认支持 16 bit：保存 16 bit 原图 + 8 bit 预览。':info.state==='unsupported'?'当前引擎未声明 16 bit 输出，只能生成 8 bit；已选的 16 bit 参数会保留，提交时明确拒绝，不会自动降级。':'可提前选择 16 bit；生成时加载引擎后验证，不支持就明确报错，不会降级为 8 bit。仅选择色深不会自动加载模型。';
  const eight=select.value==='8'?' 8 bit 生成只保存 8 bit 原图；之后切换 16 bit 需要重新生成，不能从 8 bit 原图恢复。':'';
  note.textContent=base+eight;
  select.title=base+(info.detail?' '+info.detail:'');
  updateGenerateAvailability();
}

function generateBlockers() {
  const reasons=[];
  if(!state.connected) reasons.push('本地服务未连接');
  if(!state.status){reasons.push('尚未读取服务状态');return reasons;}
  const data=state.status, engine=data.engine||{};
  if(state.modelActionBusy) reasons.push('正在执行模型操作');
  const missing=data.models?.blockers||[];
  reasons.push(...missing);
  if(engine.state==='loading') reasons.push('模型加载中，请等待完成');
  if(engine.component_operation) reasons.push('模型组件操作中，请等待完成');
  if(engine.power?.blocked) reasons.push('电池保护已阻止生成；插电后请手动重试');
  if(engine.draining) reasons.push('正在等待上次推理结束，请稍后');
  if(engine.engine_lost) reasons.push('休眠唤醒后推理引擎失效，请重新加载模型');
  if(Number(engine.queued)>=8) reasons.push('队列已满（8 个任务），请等待现有任务完成');
  if(generateBusy) reasons.push('正在提交生成任务…');
  const width=+$('#width').value, height=+$('#height').value;
  if(!Number.isFinite(width)||width<256||width>4096||!Number.isFinite(height)||height<256||height>4096) reasons.push('宽度和高度需在 256–4096 像素之间');
  const steps=+$('#steps').value, cfg=+$('#cfg').value;
  if(!(steps>=1&&steps<=100)) reasons.push('步数需在 1–100 之间');
  if(!(cfg>=0&&cfg<=30)) reasons.push('CFG 需在 0–30 之间');
  if(/肢解|血腥内脏|伪造证件|冒充真人|\b(dismemberment)\b/i.test($('#prompt').value)) reasons.push('提示词触发已约定的内容限制');
  if(+($('#output-bit-depth')?.value||8)===16 && engine.state==='ready' && data.png16?.state!=='supported') reasons.push('当前引擎不支持 16 bit 输出，请改为 8 bit 或重新加载模型');
  if(state.input && String(state.settings?.text_encoder||'').toLowerCase().endsWith('.gguf') && !state.settings?.vision) reasons.push('参考图编辑需要配置视觉编码器（mmproj）');
  if(!$('#content_confirmed').checked) reasons.push('请先勾选遵守内容约定');
  return reasons;
}
function updateGenerateAvailability(){
  const button=$('#generate-button');
  if(!button)return;
  const reasons=generateBlockers();
  button.disabled=reasons.length>0;
  button.title=reasons.length?'暂不能生成：'+reasons.join('；'):'';
}
function renderStatus(data) {
  state.status=data;
  $('#connection').innerHTML=state.connected?'<i class="status-dot"></i> 本地连接':'本地连接中断';
  const gpu=data.gpu;
  if(gpu) {
    const percent=gpu.used/gpu.total*100;
    $('#gpu-label').textContent=gpu.name.includes('4060')?'RTX 4060 Laptop':gpu.name;
    $('#gpu-memory').textContent=`${gib(gpu.used)} / ${gib(gpu.total)} GB 已使用`;
    $('#gpu-bar').style.width=percent+'%';$('#gpu-big-bar').style.width=percent+'%';
    $('#gpu-full-name').textContent=gpu.name;$('#gpu-used').textContent=gib(gpu.used);$('#gpu-total').textContent=`/ ${gib(gpu.total)} GiB`;
    $('#gpu-utilization').textContent=`GPU 利用率 ${gpu.utilization}%`;
    $('#gpu-util-bar').style.width=gpu.utilization+'%';$('#gpu-util').textContent=gpu.utilization+'%';
    state.gpuHistory.push(percent);if(state.gpuHistory.length>60)state.gpuHistory.shift();
    $('#memory-history').innerHTML=state.gpuHistory.map(v=>`<i style="height:${v}%"></i>`).join('');
  } else {$('#gpu-label').textContent='显卡监控不可用';$('#gpu-memory').textContent='检查 NVIDIA 驱动与 NVML';$('#gpu-util-bar').style.width='0%';$('#gpu-util').textContent='—';$('#gpu-bar').style.width='0%';$('#gpu-big-bar').style.width='0%';$('#gpu-used').textContent='—';$('#gpu-total').textContent='/ — GiB';$('#gpu-utilization').textContent='';$('#gpu-full-name').textContent='未能读取';}
  const missing=data.models.blockers;
  $('#model-status-label').textContent=missing.length?'模型待配置':'模型已就绪';
  updateGenerateAvailability();
  const readiness=$('#readiness');
  readiness.innerHTML=`<span class="notice-icon">${missing.length?'i':'✓'}</span><div><strong>${missing.length?'完成模型配置，即可开始创作':'模型文件已就绪'}</strong><p>${esc(missing.length?missing.join('；'):'首次生成会加载模型并验证兼容性。')}</p></div><button class="text-button" data-go="models">管理模型 →</button>`;
  const labels={idle:'引擎待命',loading:'模型加载中',ready:'模型上下文就绪',error:'引擎异常'};
  $('#engine-state').textContent=labels[data.engine.state]||data.engine.state;
  const componentCards=data.models.components.map((m,i)=>{
    const r=m.residency;
    const badge=r?(r.loaded_tensors===0?'权重未加载':r.loaded_tensors===r.tensors?'权重已加载':'部分权重已加载'):m.loaded?(m.eager_load?'权重已预加载':'上下文就绪'):m.state==='available'?'文件就绪':m.state==='incompatible'?'格式不兼容':m.optional?'可选 · 未配置':'文件缺失';
    const tone=m.loaded||m.state==='available'?'ok':'warning';
    const placement=m.weight_placement==='custom'?'自定义权重分配（以运行日志为准）':'权重放在系统内存，GPU 计算时按需传输';
    const independent=data.models.individual_preload_supported;
    const busy=state.modelActionBusy||!state.connected||!!data.engine.active_job||!!data.engine.queued||data.engine.state==='loading'||!!data.engine.component_operation;
    const loadDisabled=busy||!!data.engine.power?.blocked||m.state!=='available'||r?.supported===false||!!(r&&r.tensors>0&&r.loaded_tensors===r.tensors);
    const releaseDisabled=busy||!r?.loaded_tensors||r?.supported===false;
    const residency=r?`已读入权重 ${gib(r.loaded_weight_bytes)} / ${gib(r.weight_bytes)} GiB · 权重缓冲区内存 ${gib(r.host_buffer_bytes)} GiB / 显存 ${gib(r.device_buffer_bytes)} GiB`:'尚未读取组件占用；首次操作会创建组合上下文，仅加载所选权重。';
    const controls=independent?`<p class="small muted">${residency}</p>${r?.supported===false?'<p class="small muted">当前 mmap 模式不支持独立组件操作。</p>':''}<div class="component-actions"><button type="button" class="button subtle" data-action="component-load" data-key="${m.key}" ${loadDisabled?'disabled':''}>加载此组件权重</button><button type="button" class="button subtle" data-action="component-release" data-key="${m.key}" ${releaseDisabled?'disabled':''}>释放此组件权重</button></div>`:'<p class="small muted">单组件实际内存 / 显存：引擎未提供统计。独立加载 / 卸载暂不支持。</p>';
    return {full:`<article class="panel model-card"><div class="model-card-top"><div class="model-card-icon">${['⬡','≋','◈','▧'][i]}</div><span class="badge ${tone}">${badge}</span></div><h3>${esc(m.label)}</h3><p class="filename">${esc(m.name)}</p><p class="path">${esc(m.path)}</p><p>${esc(m.detail)}</p><p class="small">权重文件 ${m.size?gib(m.size)+' GiB':'—'} · 运行设备 ${esc(m.device)}</p><p class="small muted">${placement}</p>${controls}</article>`,compact:`<article class="studio-component"><h3>${esc(m.label)}</h3><span class="badge ${tone}">${badge}</span>${controls}</article>`};
  });
  $('#model-cards').innerHTML=componentCards.map(c=>c.full).join('');
  $('#studio-components').innerHTML=componentCards.map(c=>c.compact).join('');
  const memory=data.memory||{},measure=value=>Number.isFinite(value)?`${gib(value)} GiB`:'未能读取';
  $('#resource-process-memory').textContent=measure(memory.process_rss);
  $('#resource-private-memory').textContent=measure(memory.process_private);
  $('#resource-system-memory').textContent=`${measure(memory.available)} / ${measure(memory.total)}`;
  $('#resource-process-gpu').textContent=measure(gpu?.process_used);
  $('#resource-pid').textContent=data.engine.pid?`推理进程 PID ${data.engine.pid}`:'推理进程未启动';
  $('#resource-gpu-note').textContent=data.engine.pid&&gpu?.process_used==null?'驱动未提供进程显存数据（Windows WDDM 常见限制）；下方仍显示整张显卡的实测占用。':'进程数据只统计本工作台启动的推理服务；整卡显存还包含其他程序。';
  renderResourceSummary(data);
  renderPowerStatus(data.engine.power);
  updateModelToggle(data);
  $('#lifecycle-info').textContent=data.engine.error|| (data.engine.state==='loading'?`正在加载，已用时 ${data.engine.elapsed} 秒，可取消并释放。` :data.engine.state==='ready'?'模型上下文已就绪。卸载会退出本工作台的推理进程，释放其内存和显存。':'勾选「预加载权重到内存」后加载整套模型，减少首次使用时读取权重的等待。');
  updateBitDepth(data);
}
function updateModelToggle(data) {
  const button=$('#model-toggle');if(!button)return;
  const engine=data.engine.component_operation?'loading':data.engine.state,blockers=data.models.blockers;
  const busy=state.modelActionBusy||!state.connected||!!data.engine.active_job||!!data.engine.queued;
  const releasable=engine==='ready'||engine==='loading'||!!data.engine.pid;
  button.dataset.mode=releasable?'release':'load';
  button.classList.toggle('release',releasable);
  button.disabled=busy||(!releasable&&(!!blockers.length||!!data.engine.power?.blocked));
  button.textContent=engine==='loading'?'取消加载并释放':releasable?'卸载整套模型 · 释放内存 / 显存':'加载这套组合';
  $('#load-hint').textContent=engine==='ready'?'可卸载以减少资源占用':engine==='loading'?`加载中，已用 ${data.engine.elapsed||0} 秒`:engine==='error'?'上次加载失败，请查看 logs/engine.log':blockers.length?'先解决缺项再加载':'未加载';
  $('#quick-release').classList.toggle('hidden',!releasable||busy);
  $('#quick-release').disabled=busy;
  $('#studio-model-load').disabled=busy||releasable||!!blockers.length||!!data.engine.power?.blocked;
  $('#studio-model-release').disabled=busy||!releasable;
  $('#studio-model-release').textContent=engine==='loading'?'取消加载并释放':'卸载整套模型';
  const mini=$('#engine-mini-state'),dot=$('#engine-dot'),unloadButton=$('#engine-unload');
  if(mini){
    const stateLabel=engine==='loading'?'模型加载中':data.engine.draining?'取消中 · 等待采样结束':releasable?(data.engine.adopted?'常驻引擎 · 已加载':'已加载'):'引擎未加载';
    mini.textContent=`${stateLabel}${data.engine.pid?` · PID ${data.engine.pid}`:''}`;
    dot.className='status-dot'+(data.engine.pid?'':' muted-dot');
    unloadButton.disabled=busy||!releasable;
    unloadButton.textContent=engine==='loading'?'取消并释放':'卸载模型';
  }
  const custom=data.models.components.some(m=>m.weight_placement==='custom');
  const componentNote=data.models.individual_preload_supported?'下方可单独加载或释放组件，临时腾出内存 / 显存；其他组件保持不变。下次生成时按需重新加载。':'当前运行库不支持独立组件操作，请使用整套卸载。';
  $('#studio-model-note').textContent=componentNote+(custom?' 当前使用自定义权重分配。':' 默认权重放在系统内存，GPU 按需计算。');
  const operation=data.engine.component_operation;
  const component=data.models.components.find(m=>m.key===operation?.key)?.label||'组件';
  const reason=!state.connected?'连接中断，正在重新同步；暂不能操作模型':state.modelActionBusy?'正在发送模型操作，请稍候':data.engine.active_job?'正在生成，完成或取消任务后可操作模型':data.engine.queued?'有任务排队，请等待完成或取消任务':operation?`正在${operation.action==='release'?'释放':'加载'}${component}，已用 ${data.engine.elapsed||0} 秒；完成后按钮自动恢复`:engine==='loading'?`正在加载模型，已用 ${data.engine.elapsed||0} 秒；可取消加载并释放`:data.engine.error?`上次操作失败：${data.engine.error}`:'模型状态已同步，可按组件加载或释放';
  $('#studio-model-operation').textContent=`${reason} · 当前服务 ${location.host}`;
}

function renderPowerStatus(power) {
  const element=$('#power-status');
  if(!power){element.textContent='电源状态尚未读取';return;}
  const source=power.ac_connected===true?'外接电源':power.on_battery?'电池供电':'电源状态未知';
  const percent=Number.isFinite(power.battery_percent)?` · 电量 ${power.battery_percent}%`:'';
  const protection=power.stopping?'正在停止任务并释放模型，请稍候':power.blocked?'电池保护已阻止加载与生成；插电后请手动重新生成':power.protection_enabled?'电池保护已开启':'电池保护已关闭';
  element.textContent=`${source}${percent} · ${protection}`;
  element.classList.toggle('power-blocked',!!power.blocked);
  $('#power-event').textContent=power.last_event?`上次保护：${date(power.last_event.timestamp)} · ${power.last_event.reason}`:'';
  $('#power-event').classList.toggle('hidden',!power.last_event);
}

function renderResourceSummary(data) {
  const memory=data.memory||{},gpu=data.gpu;
  const meter=(name,used,total)=>{
    const known=Number.isFinite(used)&&Number.isFinite(total)&&total>0;
    $(`#studio-${name}-value`).textContent=known?`${gib(used)} / ${gib(total)} GiB`:'未能读取';
    const bar=$(`#studio-${name}-bar`),percent=known?Math.max(0,Math.min(100,used/total*100)):0;
    bar.style.width=percent+'%';
    bar.parentElement.setAttribute('aria-valuenow',Math.round(percent));
    bar.parentElement.setAttribute('aria-valuetext',known?`${Math.round(percent)}%`:'未能读取');
  };
  meter('memory',Number.isFinite(memory.total)&&Number.isFinite(memory.available)?memory.total-memory.available:null,memory.total);
  meter('gpu',gpu?.used,gpu?.total);
  const release=data.engine.last_release;
  const measure=value=>Number.isFinite(value)?`${gib(value)} GiB`:'未能读取';
  const text=release?`上次卸载（${date(release.timestamp)}）：推理进程已退出。退出前物理内存 ${measure(release.process_rss)}，显存 ${measure(release.process_gpu)}。系统可用量可能受缓存及其他程序影响。`:'';
  for(const selector of ['#studio-release-note','#resource-release-note']){
    $(selector).textContent=text;
    $(selector).classList.toggle('hidden',!release);
  }
}

async function modelAction(action) {
  if(state.modelActionBusy||!state.connected)return;
  state.modelActionBusy=true;
  ++statusRequestId;
  if(state.status)renderStatus(state.status);
  try {await action();}
  finally {state.modelActionBusy=false;if(state.status)renderStatus(state.status);}
}

async function releaseModels() {
  await api('/models/unload',{method:'POST'});
  await refreshStatus();
  toast('已卸载全部模型权重并关闭推理引擎，显存已释放；重新加载需要再次等待');
}

function jobStatus(job) {if(job.status==='running'&&job.cancellation_requested)return '正在取消';return {queued:'排队中',running:'生成中',completed:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'}[job.status]||job.status;}

function durationText(seconds, estimated=false) {
  if(!Number.isFinite(seconds)||seconds<0)return '—';
  const value=estimated?Math.max(5,Math.ceil(seconds/5)*5):Math.floor(seconds);
  const hours=Math.floor(value/3600),minutes=Math.floor(value%3600/60),rest=value%60;
  return [hours?`${hours} 小时`:'',minutes?`${minutes} 分`:'',rest||(!hours&&!minutes)?`${rest} 秒`:''].filter(Boolean).join(' ');
}

function jobEstimateMarkup(job) {
  if(job.status==='queued')return '<p class="job-estimate muted">预计生成时间：开始执行后估算，排队等待另计</p>';
  if(job.status!=='running')return '';
  const estimate=job.time_estimate;
  if(!estimate)return '<p class="job-estimate muted">预计生成时间：正在收集耗时数据</p>';
  const lines=[];
  if(Number.isFinite(estimate.estimated_total_seconds)){
    const remaining=Number.isFinite(estimate.remaining_seconds)?` · 预计剩余约 ${durationText(estimate.remaining_seconds,true)}`:' · 已超出参考耗时，等待重新估算';
    lines.push(`<strong>预计总耗时约 ${durationText(estimate.estimated_total_seconds,true)}${remaining}</strong><span class="muted small">相同配置最近 ${estimate.history_samples} 次成功记录，仅供参考</span>`);
  }else{
    lines.push('<span class="muted">预计总耗时：暂无相同配置的历史记录</span>');
  }
  if(Number.isFinite(estimate.sampling_remaining_seconds)){
    lines.push(`<strong>第 ${estimate.image_index} 张 · 预计采样剩余约 ${durationText(estimate.sampling_remaining_seconds,true)}</strong>`);
  }
  lines.push(`<span class="muted small">${esc(estimate.note)}</span>`);
  return `<div class="job-estimate">${lines.join('')}</div>`;
}

function renderJobs(jobs) {
  const newlyCompleted=jobs.find(j=>j.status==='completed'&&!state.jobs.some(old=>old.id===j.id&&old.status==='completed'));
  state.jobs=jobs;
  $('#job-count').textContent=jobs.length||'';
  $('#jobs').innerHTML=jobs.slice(0,5).map(j=>`<div class="job"><div class="job-head"><strong>${esc(jobStatus(j))} <span class="muted small">${j.config.width} × ${j.config.height} · ${j.config.steps} 步</span></strong><span class="small muted">${j.started?'已用 '+durationText(j.time_estimate?.elapsed_seconds??Math.max(0,(j.finished||Date.now()/1000)-j.started)):''} ${['queued','running'].includes(j.status)?`<button type="button" class="text-button" data-action="cancel" data-id="${j.id}" ${j.cancellation_requested?'disabled':''}>${j.cancellation_requested?'正在取消…':'取消'}</button>`:''}</span></div><p>${esc(j.stage||'')} ${j.step?`· ${j.step}/${j.total_steps} 步`:''}</p>${['running','queued'].includes(j.status)?`<div class="memory-track"><i style="width:${Math.round(j.progress*100)}%"></i></div>`:''}${jobEstimateMarkup(j)}${j.error?`<p class="job-error">${esc(j.error)}</p>`:''}${j.image_ids?.length?`<button class="text-button" data-action="preview" data-id="${j.image_ids[0]}">查看生成结果 ↗</button>`:''}</div>`).join('')||'<div class="empty-small">还没有生成任务。灵感准备好了就开始吧。</div>';
  $('#benchmark-table').innerHTML=jobs.length?`<table class="benchmark-table"><thead><tr><th>分辨率</th><th>数量 / 步数</th><th>结果</th><th>总耗时</th></tr></thead><tbody>${jobs.slice(0,12).map(j=>`<tr><td>${j.config.width} × ${j.config.height}</td><td>${j.config.batch_count} 张 / ${j.config.steps} 步</td><td>${esc(jobStatus(j))}</td><td>${j.finished&&j.started?(j.finished-j.started).toFixed(1)+' s':'—'}</td></tr>`).join('')}</tbody></table>`:'<div class="empty-small">尚无实测记录。模型齐备后，从低分辨率、单张开始逐级验证。</div>';
  if(newlyCompleted){refreshImages().catch(e=>toast(e.message));if(newlyCompleted.image_ids?.length)preview(newlyCompleted.image_ids[0]).catch(e=>toast(e.message));}
}

function fillSettings() {
  const form=$('#settings-form');
  Object.entries(state.settings).forEach(([key,value])=>{const el=form.elements.namedItem(key);if(el){if(el.type==='checkbox')el.checked=value;else el.value=value;}});
  const m=state.settings.diffusion_model.match(/Q[46]_K/i)?.[0];
  $('#model-choice').value=m||'Q6_K';
  fillLoadForm();
}
function fillLoadForm() {
  const form=$('#load-form');if(!form||!state.settings)return;
  for(const key of ['diffusion_model','text_encoder','vae'])form.elements[key].value=state.settings[key]||'';
  form.elements.max_vram.value=state.settings.max_vram;
  form.elements.extra_args.value=state.settings.extra_args||'';
  form.elements.vae_cpu.checked=!!state.settings.vae_cpu;
  form.elements.text_encoder_cpu.checked=!!state.settings.text_encoder_cpu;
  form.elements.keep_loaded.checked=!!state.settings.keep_loaded;
  form.elements.eager_load.checked=!!state.settings.eager_load;
}
function loadFormSettings() {
  const form=$('#load-form');
  return {...state.settings,
    diffusion_model:form.elements.diffusion_model.value.trim(),
    text_encoder:form.elements.text_encoder.value.trim(),
    vae:form.elements.vae.value.trim(),
    max_vram:+form.elements.max_vram.value,
    extra_args:form.elements.extra_args.value.trim(),
    vae_cpu:form.elements.vae_cpu.checked,
    text_encoder_cpu:form.elements.text_encoder_cpu.checked,
    keep_loaded:form.elements.keep_loaded.checked,
    eager_load:form.elements.eager_load.checked};
}
async function refreshProfiles() {
  const select=$('#profile-select');if(!select)return;
  const items=await api('/profiles');
  const current=select.value;
  select.innerHTML=items.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')||'<option value="">还没有保存过组合</option>';
  if(items.some(p=>p.id===current))select.value=current;
  $('#profile-apply').disabled=!items.length;
  $('#profile-delete').disabled=!items.length;
}
function clearVaultPasswords() {
  for(const id of ['vault-new-password','vault-new-confirm','vault-legacy-password','vault-password','vault-password-confirm'])$('#'+id).value='';
}
async function refreshVault() {
  const [files,status]=await Promise.all([api('/vault'),api('/vault/status')]);
  const pending=status.pending||status.password_book_present;
  const kept=status.unreadable?`，${status.unreadable} 个旧文件无法解锁（保持加密、不可查看）`:'';
  $('#vault-migration-panel').classList.toggle('hidden',!pending);
  if(!state.vaultMigrationBusy)$('#vault-migration-status').textContent=pending?`共 ${status.total} 个加密文件，${status.pending} 个待升级${kept}。全部验证成功后才开始清理明文文件。`:`共 ${status.total} 个加密文件${kept}。`;
  $('#vault-key-status').textContent=status.key_saved
    ?'已保存一条密码：加密和查看都只需点一下。密码用当前 Windows 账户加密保存在本机，把 .qvault 拷到别的电脑或别的账户仍然打不开。'
    :'还没有保存密码：下一次加密或查看时输入一次，并顺手保存下来即可。';
  $('#vault-key-clear').classList.toggle('hidden',!status.key_saved);
  $('#vault-key-change').textContent=status.key_saved?'更换密码':'设置密码';
  state.vaultKeySaved=!!status.key_saved;
  $('#vault-grid').innerHTML=files.map(f=>{
    const state=f.state||'legacy';
    const note=state==='secure'?(status.key_saved?'已保存密码 · 点击即可查看':'需要输入密码查看'):state==='unreadable'?'旧文件无法解锁 · 仅保留加密文件':'等待升级为统一密码';
    const label=state==='secure'?(status.key_saved?'查看':'输入密码查看'):state==='unreadable'?'无法解锁':'等待升级';
    return `<article class="panel vault-card"><span>◇</span><h3>加密作品 · ${f.id.slice(0,8)}</h3><p>${date(f.created)}<br>${(f.size/1024/1024).toFixed(1)} MiB · ${note}</p><button class="button subtle" data-action="unlock" data-id="${f.id}" ${state==='secure'?'':'disabled'}>${label}</button></article>`;
  }).join('')||empty('私密文件夹还是空的','在图片上点击「加密」并设置密码。','◇');
}
$('#vault-key-change').addEventListener('click',()=>passwordDialog(state.vaultKeySaved?'rekey':'setup'));
$('#vault-key-clear').addEventListener('click',safe(async()=>{
  if(!confirm('清除保存在本机的私密库密码？之后加密和查看都需要手动输入。'))return;
  await api('/vault/key',{method:'DELETE'});
  await refreshVault();
  toast('已清除保存的密码');
}));
async function finishEncryption(id) {
  state.selected.delete(id);updateCompare();
  $('#image-dialog').close();$('#image-detail').replaceChildren();
  $('#compare-dialog').close();$('#compare-content').replaceChildren();
  if(state.preview?.id===id)clearPreview();
  await refreshImages();
  await refreshGallery();await refreshFavorites();
  renderJobs(await api('/jobs'));
  localStorage.removeItem(DRAFT_KEY);
  toast('已验证加密文件并移除明文原图和缓存；查看时需要密码');
  if(state.view==='vault')await refreshVault();
}
function privatePreview(result) {
  if(document.hidden)return;
  $('#private-preview').innerHTML=`<img alt="已解锁的私密图片" src="data:image/png;base64,${result.image}"><pre>${esc(JSON.stringify(result.record.config,null,2))}</pre>`;
  $('#private-dialog').showModal();
}
function passwordDialog(action,id) {
  passwordAction={action,id};clearVaultPasswords();$('#password-error').textContent='';
  const confirmNeeded=action!=='unlock';
  const titles={encrypt:'加密并移入私密库',setup:'设置私密库密码',rekey:'更换私密库密码',unlock:'输入密码查看'};
  const notes={
    setup:'只需设置一次。密码用当前 Windows 账户的 DPAPI 加密保存在本机，之后加密和查看都不再询问；忘记密码无法恢复。',
    encrypt:'验证加密文件后移除明文原图、缓存和关联记录。',
    rekey:'全部私密文件会用新密码重新加密。预检不通过或中途失败都不会改动任何文件，保存的密码也会同步更新。',
    unlock:'保存的密码打不开这个文件，请输入这个文件原来的密码。密码只用于本次解锁。'};
  $('#password-title').textContent=titles[action]||titles.unlock;
  $('#password-note').textContent=notes[action]||notes.unlock;
  $('#vault-password').minLength=action==='unlock'?10:12;
  $('#vault-password').autocomplete=confirmNeeded?'new-password':'off';
  $('#vault-password').placeholder=action==='rekey'?'新密码':'';
  $('#vault-confirm-label').classList.toggle('hidden',!confirmNeeded);
  $('#vault-password-confirm').required=confirmNeeded;
  $('#password-submit').textContent={setup:'保存并使用',rekey:'更换密码',encrypt:'加密',unlock:'解锁'}[action]||'确认';
  $('#password-dialog').showModal();
}
async function runEncrypt(id) {
  await api(`/images/${id}/encrypt`,{method:'POST',body:{}});
  await finishEncryption(id);
}

// ── 创作助手 ──────────────────────────────────────────────
const assistant={config:null,messages:[],controller:null,busy:false,attachment:null,following:true};
const CONTEXT_LIMIT=60000; // 与后端 ChatRequest 的字符上限保持一致
const ASSISTANT_EMPTY='让它帮你拆解画面、扩写提示词，或者聊聊光影和构图。<br><small>助手看不到你的本地图片，只看到这里的文字。</small>';

function syncAssistant() {
  const status=$('#assistant-status'),config=assistant.config;
  status.className='';
  if(!config)status.textContent='正在读取配置…';
  else if(!config.has_key){status.textContent='尚未填写 API Key，助手暂不可用';status.classList.add('warn');}
  else{status.textContent=`${config.model} · ${config.saved?'密钥已加密保存':'密钥仅在内存中'}`;status.classList.add('ok');}
  $('#assistant-send').disabled=assistant.busy||!config?.has_key;
  $('#assistant-send').textContent=assistant.busy?'回复中…':'发送 ↑';
  $('#assistant-stop').classList.toggle('hidden',!assistant.busy);
  $('#assistant-pick').disabled=assistant.busy;
}
function clearAttachment() {
  assistant.attachment=null;
  $('#assistant-attachment').classList.add('hidden');
  $('#assistant-attachment-thumb').removeAttribute('src');
  $('#assistant-attachment-name').textContent='';
  $('#assistant-file').value='';
}
async function loadAssistant() {
  assistant.config=await api('/assistant/config');
  $('#assistant-url').value=assistant.config.base_url;
  $('#assistant-model').value=assistant.config.model;
  $('#assistant-remember').checked=!!assistant.config.saved;
  const prompt=assistant.config.system_prompt;
  if(prompt)$('#assistant-prompt-info').textContent=`系统提示词：${prompt.path}${prompt.custom?'':'（默认）'} · ${prompt.chars} 字 · 每次请求自动附加`;
  syncAssistant();
}
function toggleAssistantConfig(open) {
  const form=$('#assistant-config');
  const show=open ?? form.classList.contains('hidden');
  form.classList.toggle('hidden',!show);
  $('#assistant-config-toggle').setAttribute('aria-expanded',String(show));
  if(show)$('#assistant-url').focus();
}
function updateAssistantCount() {$('#assistant-counter').textContent=`${$('#assistant-input').value.length} / 4000`;}
function nearBottom(){const log=$('#assistant-log');return log.scrollHeight-log.scrollTop-log.clientHeight<90;}
function updateJump(){const log=$('#assistant-log'),button=$('#assistant-jump');if(!log||!button)return;button.classList.toggle('hidden',nearBottom()||log.scrollHeight<=log.clientHeight+4);}
function scrollAssistant(force=false){const log=$('#assistant-log');if(force||assistant.following)log.scrollTop=log.scrollHeight;updateJump();}
function estimateTokens(text){let cjk=0,other=0;for(const ch of text){if(/[\u3400-\u9fff\uf900-\ufaff]/.test(ch))cjk++;else other++;}return Math.round(cjk*0.6+other/4);}
function updateContextRing(){
  const arc=$('#assistant-context-arc'),ring=$('#assistant-context');if(!arc||!ring)return;
  const text=assistant.messages.map(m=>m.content||'').join('\n');
  const chars=text.length,tokens=estimateTokens(text),ratio=Math.min(1,chars/CONTEXT_LIMIT);
  arc.style.strokeDashoffset=String(81.68*(1-ratio));
  $('#assistant-context-label').textContent=chars?Math.round(ratio*100)+'%':'0%';
  ring.classList.toggle('warn',ratio>=0.6&&ratio<0.85);
  ring.classList.toggle('full',ratio>=0.85);
  ring.title=`上下文估算：${chars} 字 · 约 ${tokens} tokens\n本地单次请求上限 ${CONTEXT_LIMIT} 字；DeepSeek 模型上下文约 1M tokens`;
}

function renderRichText(target,text) {
  target.replaceChildren();
  const fence=/```[^\n`]*\n?([\s\S]*?)```/g;
  let last=0,match;
  const addText=chunk=>{
    if(!chunk.trim())return;
    const node=document.createElement('div');node.textContent=chunk.trim();target.append(node);
  };
  while((match=fence.exec(text))){
    addText(text.slice(last,match.index));
    const block=document.createElement('div');block.className='msg-code';
    const pre=document.createElement('pre');pre.textContent=match[1].trim();
    const footer=document.createElement('footer');
    const use=document.createElement('button');use.type='button';use.className='text-button';use.dataset.usePrompt='1';use.textContent='用作画面描述';
    footer.append(use);block.append(pre,footer);target.append(block);
    last=fence.lastIndex;
  }
  addText(text.slice(last));
  if(!target.childElementCount)target.textContent=text;
}
function createMessage(role,text='') {
  $('#assistant-log').querySelector('.assistant-empty')?.remove();
  const root=document.createElement('article');root.className='msg '+role;
  const body=document.createElement('div');body.className='msg-body';
  const reasoning=document.createElement('div');reasoning.className='msg-reasoning hidden';
  const actions=document.createElement('div');actions.className='msg-actions';
  const note=document.createElement('div');note.className='msg-note';
  if(role==='user'){body.textContent=text;root.append(body);}
  else root.append(reasoning,body,actions,note);
  $('#assistant-log').append(root);scrollAssistant();
  return {root,body,reasoning,actions,note};
}
async function sendAssistant() {
  const input=$('#assistant-input'),text=input.value.trim(),attachment=assistant.attachment;
  if(!text&&!attachment)return;
  if(!assistant.config?.has_key){toggleAssistantConfig(true);throw new Error('请先在「API 配置」中填写 API Key 并应用');}
  input.value='';updateAssistantCount();clearAttachment();
  const message={role:'user',content:text};
  if(attachment)message.image_ids=[attachment.id];
  assistant.messages.push(message);updateContextRing();
  assistant.following=true;
  const userView=createMessage('user','');
  if(attachment){
    const image=document.createElement('img');image.src=attachment.url;image.className='msg-image';image.alt=attachment.name||'附加图片';
    userView.body.append(image);
  }
  if(text){const line=document.createElement('div');line.textContent=text;if(attachment)line.className='msg-text';userView.body.append(line);}
  const view=createMessage('assistant');
  view.body.classList.add('streaming');view.body.textContent='';
  assistant.busy=true;syncAssistant();
  const controller=new AbortController();assistant.controller=controller;
  let answer='',reasoning='',failure='';
  try{
    const messages=assistant.messages.slice(-20).map(m=>({role:m.role,content:m.content.slice(0,12000),...(m.image_ids?.length?{image_ids:m.image_ids}:{})}));
    const response=await fetch('/api/assistant/chat',{method:'POST',signal:controller.signal,
      headers:{'Content-Type':'application/json','X-Studio-Token':state.token},
      body:JSON.stringify({messages,thinking:$('#assistant-thinking').checked})});
    if(!response.ok){
      const data=await response.json().catch(()=>({}));
      const detail=data.detail;
      throw new Error(Array.isArray(detail)?detail.map(e=>`${e.loc?.at(-1)??''}：${e.msg}`).join('；'):detail||`请求失败（HTTP ${response.status}）`);
    }
    const reader=response.body.getReader(),decoder=new TextDecoder();
    let buffer='';
    for(;;){
      const {value,done}=await reader.read();
      if(done)break;
      buffer+=decoder.decode(value,{stream:true});
      let index;
      while((index=buffer.indexOf('\n'))>=0){
        const line=buffer.slice(0,index).trim();buffer=buffer.slice(index+1);
        if(!line)continue;
        let event;try{event=JSON.parse(line);}catch{continue;}
        if(event.type==='content'){answer+=event.text;view.body.textContent=answer;scrollAssistant();}
        else if(event.type==='reasoning'){reasoning+=event.text;view.reasoning.classList.remove('hidden');view.reasoning.textContent=reasoning;scrollAssistant();}
        else if(event.type==='error'){failure=event.message;view.note.classList.add('error');view.note.textContent=failure;}
      }
    }
  }catch(error){
    if(error.name==='AbortError'){failure='已停止生成';view.note.textContent=failure;}
    else{failure=error.message;view.note.classList.add('error');view.note.textContent=failure;}
  }finally{
    assistant.controller=null;assistant.busy=false;syncAssistant();
    view.body.classList.remove('streaming');
    if(answer){
      renderRichText(view.body,answer);
      assistant.messages.push({role:'assistant',content:answer});
      updateContextRing();
      const use=document.createElement('button');use.type='button';use.className='text-button';
      use.dataset.usePrompt='1';use.textContent='整段用作画面描述';use._prompt=answer;view.actions.append(use);
    }else{
      view.body.textContent=failure&&failure!=='已停止生成'?'（未获得回复）':'（已停止）';
      view.body.classList.add('muted');
    }
    scrollAssistant();
  }
}

document.addEventListener('click',safe(async event=>{
  const nav=event.target.closest('[data-view],[data-go]');if(nav){navigate(nav.dataset.view||nav.dataset.go);return;}
  const close=event.target.closest('[data-close]');if(close){$('#'+close.dataset.close).close();return;}
  const button=event.target.closest('[data-action]');if(!button)return;
  event.preventDefault();
  const {action,id}=button.dataset;
  if(action==='compare-select'){
    if(state.selected.has(id))state.selected.delete(id);
    else{
      if(state.selected.size>=4){toast('最多同时比较 4 张图片');return;}
      state.selected.add(id);
    }
    updateCompare();
    button.setAttribute('aria-pressed',String(state.selected.has(id)));
    button.textContent=state.selected.has(id)?'取消比较':'加入比较';
    toast(state.selected.has(id)?'已加入比较，选好后点击底部「并排比较」':'已取消比较');
    return;
  }
  if(action==='component-load'||action==='component-release'){
    const key=button.dataset.key,operation=action==='component-load'?'load':'release';
    await modelAction(async()=>{
      await api(`/models/components/${key}/${operation}`,{method:'POST'});
      await refreshStatus();
      toast(operation==='load'?'开始加载所选组件权重':'开始释放所选组件权重');
    });
  }
  if(action==='detail')await detail(id);
  if(action==='preview'){navigate('studio');await preview(id);}
  if(action==='favorite'||action==='tag'){
    if(state.tagPending?.has(id))return;
    (state.tagPending??=new Set()).add(id);
    const restoreFocus=document.activeElement===button;
    const controls=$$('[data-action="favorite"],[data-action="tag"]').filter(el=>el.dataset.id===id);
    controls.forEach(el=>el.disabled=true);
    const tag=action==='favorite'?(button.dataset.tag==='favorite'?'none':'favorite'):button.dataset.tag;
    try{
      await api(`/images/${id}/tag`,{method:'PUT',body:{tag}});await refreshImages();
      if($('#image-dialog').open&&$('#image-dialog').dataset.id===id)await detail(id);
      if(restoreFocus){
        const focusRoot=document.querySelector('dialog[open]')||document.querySelector('.view.active');
        const replacement=$$('[data-action]',focusRoot).find(el=>el.dataset.id===id&&el.dataset.action===action&&el.dataset.tagKind===button.dataset.tagKind);
        replacement?.focus({preventScroll:true});
      }
      toast(tag==='none'?'已取消图片标记':'已标记为'+({favorite:'收藏',like:'喜欢',dislike:'不喜欢',blocked:'隐藏'}[tag]||tag));
    }finally{state.tagPending.delete(id);controls.forEach(el=>el.disabled=false);}
  }
  if(action==='reuse'){const im=await api('/images/'+id);$('#image-dialog').close();restoreConfig(im.config);}
  if(action==='export'){const im=await api('/images/'+id);downloadJSON(im,im.name+'.json');}
  if(action==='trash'){
    if(!confirm('将图片移入项目回收站？可以在图库的「回收站」中恢复。'))return;
    await api('/images/'+id,{method:'DELETE'});$('#image-dialog').close();state.selected.delete(id);updateCompare();await refreshImages();toast('已移入回收站，可恢复');
  }
  if(action==='restore'){await api(`/images/${id}/restore`,{method:'POST'});await refreshImages();toast('图片已恢复');}
  if(action==='use-preset'){const presets=await api('/presets');const preset=presets.find(p=>p.id===id);if(!preset)throw new Error('该预设已不存在');restoreConfig(preset.config);$('#preset-menu').classList.add('hidden');}
  if(action==='delete-preset'){if(!confirm('删除这组参数预设？'))return;await api('/presets/'+id,{method:'DELETE'});await refreshPresetCount();await renderPresetMenu();if(state.view==='favorites')await refreshFavorites();}
  if(action==='cancel'){
    button.disabled=true;button.textContent='正在取消…';
    try{await api(`/jobs/${id}/cancel`,{method:'POST'});renderJobs(await api('/jobs'));await refreshStatus();toast('已取消任务；模型保持加载，已保存的图片保留');}
    catch(error){button.disabled=false;button.textContent='取消';throw error;}
  }
  if(action==='encrypt'){
    const status=await api('/vault/status');
    if(status.pending||status.password_book_present){navigate('vault');toast('请先升级旧私密库，再加密新图片');return;}
    // One saved password: no dialog unless it was never set.
    if(!status.key_saved){passwordDialog('setup',id);return;}
    await runEncrypt(id);
  }
  if(action==='unlock'){
    const status=await api('/vault/status');
    if(status.key_saved){
      try{privatePreview(await api(`/vault/${id}/unlock`,{method:'POST',body:{}}));await refreshImages();return;}
      catch(error){toast('保存的密码打不开这个文件，请输入这个文件原来的密码');}
    }
    passwordDialog('unlock',id);
  }

}));
document.addEventListener('change',event=>{
  const checkbox=event.target.closest('[data-select]');if(!checkbox)return;
  if(checkbox.checked){if(state.selected.size>=4){checkbox.checked=false;toast('最多同时比较 4 张图片');return;}state.selected.add(checkbox.dataset.select);}else state.selected.delete(checkbox.dataset.select);
  updateCompare();
});
$('#generation-form').addEventListener('submit',safe(async event=>{
  event.preventDefault();const button=$('#generate-button');
  if(event.submitter!==button)return;
  generateBusy=true;button.disabled=true;
  try{await api('/jobs',{method:'POST',body:formConfig()});toast('任务已加入队列');renderJobs(await api('/jobs'));}
  finally{generateBusy=false;if(state.status)renderStatus(state.status);}
}));
$('#prompt').addEventListener('input',updatePromptCount);
$('#generation-form').addEventListener('input',updateGenerateAvailability);
$('#generation-form').addEventListener('change',updateGenerateAvailability);
$('#output-bit-depth').addEventListener('change',()=>updateBitDepth(state.status));
$$('.ratio[data-ratio]').forEach(b=>b.addEventListener('click',()=>{const [w,h]=b.dataset.ratio.split(',');$('#width').value=w;$('#height').value=h;updateRatio();}));
$('#ratio-custom').addEventListener('click',()=>{
  $('#advanced-settings').open=true;
  const width=$('#width');width.focus();width.select();
  toast('在「宽度 / 高度」里输入任意尺寸，建议使用 8 的倍数');
});
['width','height'].forEach(id=>{
  const el=$('#'+id);
  el.addEventListener('input',updateRatio);
  el.addEventListener('change',()=>{
    const before=Number(el.value),aligned=snapSize(el.value);
    if(before!==aligned){el.value=aligned;toast(`尺寸已向下对齐到 ${aligned}（8 的倍数）`);}
    updateRatio();
  });
});
$('#reset-form').addEventListener('click',()=>{if(!confirm('重置当前输入和生成参数？'))return;$('#generation-form').reset();clearInput();updatePromptCount();updateRatio();updateBitDepth(state.status);});
$('#save-preset').addEventListener('click',safe(savePreset));$('#save-preset-here').addEventListener('click',safe(savePreset));
$('#open-presets').addEventListener('click',safe(async()=>{
  const menu=$('#preset-menu');
  if(!menu.classList.contains('hidden')){menu.classList.add('hidden');return;}
  await renderPresetMenu();menu.classList.remove('hidden');
}));
$('#close-presets').addEventListener('click',()=>$('#preset-menu').classList.add('hidden'));
document.addEventListener('click',event=>{
  const menu=$('#preset-menu');
  if(menu.classList.contains('hidden'))return;
  if(event.target.closest('#preset-menu')||event.target.closest('#open-presets'))return;
  menu.classList.add('hidden');
});
document.addEventListener('click',event=>{
  const panel=$('#canvas-props-panel');
  if(!panel||panel.classList.contains('hidden'))return;
  if(event.target.closest('#canvas-props-panel')||event.target.closest('#canvas-props'))return;
  toggleCanvasProps(false);
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'){$('#preset-menu').classList.add('hidden');toggleCanvasProps(false);}
  if(event.key!=='ArrowLeft'&&event.key!=='ArrowRight')return;
  if(document.querySelector('dialog[open]')||event.target.closest('input,textarea,select'))return;
  if(state.view!=='studio'||!state.preview)return;
  event.preventDefault();
  stepPreview(event.key==='ArrowLeft'?-1:1).catch(error=>toast(error.message));
});
$('#canvas-prev').addEventListener('click',safe(()=>stepPreview(-1)));
$('#canvas-next').addEventListener('click',safe(()=>stepPreview(1)));
$('#reference-file').addEventListener('change',safe(async()=>{
  const file=$('#reference-file').files[0];if(!file)return;
  const form=new FormData();form.append('file',file);$('#input-label').textContent='正在上传…';
  try{const result=await api('/inputs',{method:'POST',body:form});state.input=result.id;$('#input-label').textContent=result.name;$('#clear-input').classList.remove('hidden');saveDraft();updateGenerateAvailability();toast('参考图已保存到本机');}catch(e){clearInput();throw e;}
}));
$('#clear-input').addEventListener('click',clearInput);
$('#model-choice').addEventListener('change',safe(async()=>{
  const old=state.settings.diffusion_model;
  if(!/Q[46]_K/i.test(old))throw new Error('自定义模型请在设置中修改完整路径。');
  const settings={...state.settings,diffusion_model:old.replace(/Q[46]_K/i,$('#model-choice').value)};
  await api('/settings',{method:'PUT',body:settings});state.settings=settings;await refreshStatus();toast('已切换模型路径');
}));
$('#preview-visibility').addEventListener('click',()=>{
  if(!state.preview)return;
  previewHidden=!previewHidden;
  if(previewHidden)toggleCanvasProps(false);
  updatePreviewVisibility();
});
$('#preview-details').addEventListener('click',safe(()=>detail(state.preview.id)));
$('#canvas-props').addEventListener('click',()=>toggleCanvasProps());
$('#canvas-props-close').addEventListener('click',()=>toggleCanvasProps(false));
$('#canvas-props-more').addEventListener('click',safe(()=>{toggleCanvasProps(false);return detail(state.preview.id);}));
$('#canvas-props-copy').addEventListener('click',safe(async()=>{
  const text=propsPlainText(state.preview);
  try{await navigator.clipboard.writeText(text);}
  catch{
    const area=document.createElement('textarea');area.value=text;area.style.position='fixed';area.style.opacity='0';
    document.body.appendChild(area);area.select();document.execCommand('copy');area.remove();
  }
  toast('当前图片参数已复制到剪贴板');
}));
$('#preview-download').addEventListener('click',()=>{if(state.preview){const a=document.createElement('a');a.href=`/api/images/${state.preview.id}/file?download=true`;a.click();}});
$('#rescan').addEventListener('click',safe(async()=>{const b=$('#rescan');b.disabled=true;try{const r=await api('/gallery/scan',{method:'POST'});await refreshImages();toast(`已扫描 ${r.count} 张图片${r.errors.length?'，'+r.errors.length+' 个文件无法读取':''}`);}finally{b.disabled=false;}}));
$('#gallery-filters').addEventListener('click',safe(async event=>{const b=event.target.closest('[data-filter]');if(!b)return;state.filter=b.dataset.filter;$$('[data-filter]').forEach(x=>x.classList.toggle('selected',x===b));await refreshGallery();}));
$('#gallery-search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>refreshGallery().catch(e=>toast(e.message)),200);});
$('#open-compare').addEventListener('click',safe(compare));$('#clear-compare').addEventListener('click',()=>{state.selected.clear();updateCompare();});
async function submitLoad(){
  const next=loadFormSettings();
  const result=await api('/models/load',{method:'POST',body:next});
  state.settings=next;fillSettings();await refreshStatus();
  toast(result?.reused?'模型已按这套组合加载，无需重复读取权重':'已按这套组合开始加载，首次加载可能需要几分钟');
}
$('#load-form').addEventListener('submit',safe(async event=>{event.preventDefault();await modelAction(submitLoad);}));
$('#profile-save').addEventListener('click',safe(async()=>{
  const name=prompt('给这套组合起个名字：','');
  if(!name?.trim())return;
  await api('/profiles',{method:'POST',body:{name:name.trim(),settings:loadFormSettings()}});
  await refreshProfiles();toast('组合已保存，下次可直接套用');
}));
$('#profile-apply').addEventListener('click',safe(async()=>{
  const identity=$('#profile-select').value;if(!identity)throw new Error('还没有可用的组合预设');
  const saved=await api(`/profiles/${identity}/apply`,{method:'POST'});
  state.settings=saved;fillSettings();await refreshStatus();
  toast('已套用这套组合，点「加载」即可使用');
}));
$('#profile-delete').addEventListener('click',safe(async()=>{
  const identity=$('#profile-select').value;if(!identity)throw new Error('请选择要删除的组合');
  if(!confirm('删除这个组合预设？不影响当前设置。'))return;
  await api('/profiles/'+identity,{method:'DELETE'});
  await refreshProfiles();toast('组合预设已删除');
}));
$('#model-toggle').addEventListener('click',safe(async()=>{
  const release=$('#model-toggle').dataset.mode==='release';
  await modelAction(async()=>{if(release)await releaseModels();else await submitLoad();});
}));
for(const selector of ['#quick-release','#studio-model-release','#engine-unload'])$(selector).addEventListener('click',safe(()=>modelAction(releaseModels)));
$('#studio-model-load').addEventListener('click',safe(()=>modelAction(async()=>{
  await api('/models/load',{method:'POST',body:{...state.settings,eager_load:true}});
  state.settings=await api('/settings');fillSettings();fillLoadForm();
  await refreshStatus();toast('开始预加载整套权重，可取消并释放');
})));
$('#settings-form').addEventListener('submit',safe(async event=>{
  event.preventDefault();const form=event.target;const values={...state.settings};
  for(const key of Object.keys(state.settings)){const el=form.elements.namedItem(key);if(el)values[key]=el.type==='checkbox'?el.checked:el.type==='number'?+el.value:el.value.trim();}
  await api('/settings',{method:'PUT',body:values});state.settings=values;fillSettings();await refreshStatus();$('#settings-message').textContent='设置已保存 · '+new Date().toLocaleTimeString('zh-CN');toast('设置已保存');
}));
$('#password-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=$('#password-submit');button.disabled=true;$('#password-error').textContent='';
  const {action,id}=passwordAction;
  const password=$('#vault-password').value,confirm=$('#vault-password-confirm').value;
  try{
    if(action==='setup'){
      if(password!==confirm)throw new Error('两次密码不一致');
      await api('/vault/key',{method:'PUT',body:{password}});
      $('#password-dialog').close();toast('私密库密码已保存，之后加密与查看都不再询问');
      await refreshVault();
      if(id)await runEncrypt(id);
      return;
    }
    if(action==='rekey'){
      if(password!==confirm)throw new Error('两次密码不一致');
      const result=await api('/vault/rekey',{method:'POST',body:{password,confirm_password:confirm}});
      $('#password-dialog').close();await refreshVault();
      toast(`已更换密码，${result.rekeyed} 个私密文件重新加密完成`);
      return;
    }
    if(action==='encrypt'){
      if(password!==confirm)throw new Error('两次密码不一致');
      await api(`/images/${id}/encrypt`,{method:'POST',body:{password,confirm_password:confirm}});
      $('#password-dialog').close();await finishEncryption(id);
      return;
    }
    const result=await api(`/vault/${id}/unlock`,{method:'POST',body:{password}});
    $('#password-dialog').close();privatePreview(result);await refreshImages();
  }catch(error){$('#password-error').textContent=error.message;}finally{button.disabled=false;}
});
$('#vault-migration-form').addEventListener('submit',async event=>{
  event.preventDefault();if(state.vaultMigrationBusy)return;
  const button=$('#vault-migrate'),message=$('#vault-migration-error');message.textContent='';
  if($('#vault-new-password').value!==$('#vault-new-confirm').value){message.textContent='两次新密码不一致';return;}
  state.vaultMigrationBusy=true;button.disabled=true;
  $('#vault-migration-status').textContent='正在验证并迁移全部文件，可能需要几分钟。请等待完成；中断后可用同一新密码重试。';
  const body={password:$('#vault-new-password').value,confirm_password:$('#vault-new-confirm').value,legacy_password:$('#vault-legacy-password').value||null,keep_unreadable:$('#vault-keep-unreadable').checked};
  clearVaultPasswords();
  try{
    const result=await api('/vault/migrate',{method:'POST',body});
    localStorage.removeItem(DRAFT_KEY);clearPreview();$('#image-dialog').close();$('#image-detail').replaceChildren();$('#compare-dialog').close();$('#compare-content').replaceChildren();state.selected.clear();updateCompare();
    $('#vault-migration-form').reset();await refreshImages();await refreshVault();
    toast(`已升级 ${result.migrated} 个文件，旧密码本和关联明文文件已移除。请使用新密码查看。${result.kept?.length?`另有 ${result.kept.length} 个旧文件无法解锁，已保持加密保留。`:''}`);
  }catch(error){message.textContent=error.message;$('#vault-migration-status').textContent='升级未完成。原图或已验证的加密副本仍保留；请按提示重试。';}
  finally{body.password='';body.confirm_password='';body.legacy_password=null;state.vaultMigrationBusy=false;button.disabled=false;}
});
$('#password-dialog').addEventListener('close',clearVaultPasswords);
$('#private-dialog').addEventListener('close',()=>{$('#private-preview').replaceChildren();});
$('#assistant-config-toggle').addEventListener('click',()=>toggleAssistantConfig());
$('#assistant-config').addEventListener('submit',safe(async event=>{
  event.preventDefault();
  const body={base_url:$('#assistant-url').value.trim()||'https://api.deepseek.com',model:$('#assistant-model').value.trim(),remember:$('#assistant-remember').checked};
  const key=$('#assistant-key').value.trim();
  if(!body.model)throw new Error('模型名称不能为空');
  if(key)body.api_key=key;
  assistant.config=await api('/assistant/config',{method:'PUT',body});
  $('#assistant-key').value='';$('#assistant-url').value=assistant.config.base_url;$('#assistant-model').value=assistant.config.model;
  syncAssistant();
  if(!key&&!assistant.config.has_key)throw new Error('接口设置已保存；请填写 API Key 后才能对话');
  toast(assistant.config.saved?'配置已应用，密钥已用 DPAPI 加密保存到本机':assistant.config.has_key?'配置已应用，密钥只在本次运行的内存中':'接口设置已保存；填写 API Key 后才能对话');
}));
$('#assistant-test').addEventListener('click',safe(async()=>{
  const button=$('#assistant-test');button.disabled=true;button.textContent='测试中…';
  try{
    const result=await api('/assistant/check',{method:'POST'});
    toast(result.model_available?`连接成功 · ${result.models.length} 个模型可用`:`连接成功，但列表中未见「${assistant.config?.model||''}」，可用：${result.models.slice(0,4).join('、')||'无'}`);
  }finally{button.disabled=false;button.textContent='测试连接';}
}));
$('#assistant-forget').addEventListener('click',safe(async()=>{
  await api('/assistant/key',{method:'DELETE'});
  assistant.config=await api('/assistant/config');$('#assistant-remember').checked=false;syncAssistant();
  toast('已清除内存中的密钥，并删除本机保存的密钥文件');
}));
$('#assistant-clear').addEventListener('click',()=>{
  if(!assistant.messages.length)return;
  if(!confirm('清空当前对话？对话只存在于这个页面，清空后无法恢复。'))return;
  assistant.messages=[];clearAttachment();$('#assistant-log').innerHTML=`<div class="assistant-empty">${ASSISTANT_EMPTY}</div>`;updateContextRing();assistant.following=true;updateJump();
});
$('#assistant-prompt-toggle').addEventListener('click',safe(async()=>{
  const box=$('#assistant-prompt-text'),button=$('#assistant-prompt-toggle');
  if(!box.classList.contains('hidden')){box.classList.add('hidden');button.textContent='查看';return;}
  if(!box.dataset.loaded){const data=await api('/assistant/prompt');box.textContent=data.text;box.dataset.loaded='1';}
  box.classList.remove('hidden');button.textContent='收起';
}));
$('#assistant-form').addEventListener('submit',safe(async event=>{event.preventDefault();await sendAssistant();}));
$('#assistant-input').addEventListener('input',updateAssistantCount);
$('#assistant-pick').addEventListener('click',()=>$('#assistant-file').click());
$('#assistant-file').addEventListener('change',safe(async()=>{
  const file=$('#assistant-file').files[0];if(!file)return;
  if(!/^image\/(png|jpeg|webp)$/.test(file.type))throw new Error('仅支持 PNG / JPEG / WebP');
  if(file.size>25*1024*1024)throw new Error('图片上限 25 MB');
  const form=new FormData();form.append('file',file);
  const result=await api('/inputs',{method:'POST',body:form});
  assistant.attachment={id:result.id,name:result.name||file.name,url:URL.createObjectURL(file)};
  $('#assistant-attachment-thumb').src=assistant.attachment.url;
  $('#assistant-attachment-name').textContent=assistant.attachment.name;
  $('#assistant-attachment').classList.remove('hidden');
  toast('图片已附加，会随下一条消息发送给助手');
}));
$('#assistant-attachment-remove').addEventListener('click',clearAttachment);
$('#assistant-input').addEventListener('keydown',event=>{
  if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!assistant.busy&&assistant.config?.has_key)$('#assistant-form').requestSubmit();}
});
$('#assistant-stop').addEventListener('click',()=>{assistant.controller?.abort();});
$('#assistant-log').addEventListener('scroll',()=>{assistant.following=nearBottom();updateJump();});
$('#assistant-jump').addEventListener('click',()=>{assistant.following=true;scrollAssistant(true);});
$('#assistant-log').addEventListener('click',safe(async event=>{
  const button=event.target.closest('[data-use-prompt]');if(!button)return;
  const text=button._prompt??button.closest('.msg-code')?.querySelector('pre')?.textContent??'';
  if(!text.trim())return;
  const prompt=$('#prompt');
  if(prompt.value.trim()&&!confirm('用这段文字替换当前的画面描述？'))return;
  prompt.value=text.trim();updatePromptCount();prompt.focus();
  toast('已填入画面描述，核对参数后即可生成');
}));
// 闭合的 details 里的必填项无法获得焦点，提交前先展开。
$('#generation-form').addEventListener('invalid',event=>{const box=event.target.closest('details');if(box&&!box.open)box.open=true;},true);
document.addEventListener('visibilitychange',()=>{if(document.hidden){$('#private-dialog').close();$('#password-dialog').close();clearVaultPasswords();}});
$('#theme-toggle').addEventListener('click',()=>{document.body.classList.toggle('light');localStorage.setItem('qwen-theme',document.body.classList.contains('light')?'light':'dark');});
if(localStorage.getItem('qwen-theme')==='light')document.body.classList.add('light');
window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));

let polling=false;
async function refreshStatus() {
  const requestId=++statusRequestId;
  try{
    const status=await api('/status');
    if(requestId!==statusRequestId)return;
    state.connected=true;renderStatus(status);
  }catch(error){
    if(requestId!==statusRequestId)return;
    state.connected=false;if(state.status)renderStatus(state.status);
    $('#connection').textContent='本地连接中断';updateGenerateAvailability();
    throw error;
  }
}
async function poll() {
  if(state.vaultMigrationBusy)return;
  if(polling)return;polling=true;
  try{
    const [status,jobs]=await Promise.allSettled([refreshStatus(),api('/jobs')]);
    if(jobs.status==='fulfilled')renderJobs(jobs.value);
    else $('#jobs').title='任务列表暂时无法更新，下次轮询重试';
    if(status.status==='rejected')$('#connection').title=status.reason.message;
    if(imageToolInstances[state.view])await imageToolInstances[state.view].refresh().catch(error=>{$('#connection').title=error.message;});
  }
  finally{polling=false;}
}
async function init() {
  try{
    state.token=(await api('/session')).token;
    state.settings=await api('/settings');fillSettings();
    try{await loadAssistant();}catch{$('#assistant-status').textContent='助手接口不可用';$('#assistant-status').className='warn';}
    updateAssistantCount();updateContextRing();updateJump();
    navigate(location.hash.slice(1)||'studio');
    await poll();
    const scan=await api('/gallery/scan',{method:'POST'});await refreshImages();
    await refreshPresetCount();
    updateCanvasNav();
    if(scan.errors.length)toast(`${scan.errors.length} 张图片无法读取，其他作品已载入。`);
  }catch(error){toast('初始化失败：'+error.message);$('#connection').textContent='初始化失败';}
  setInterval(poll,3000);
}
for(const event of ['input','change'])$('#generation-form').addEventListener(event,()=>queueMicrotask(saveDraft));
$('#advanced-settings').addEventListener('toggle',saveDraft);
window.addEventListener('pagehide',saveDraft);
restoreDraft();
init();
