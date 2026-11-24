#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_site.py — 生成单文件静态页面 web/index.html

设计目标（按用户要求）：
  - 主页面直接显示“当天/本周”黑市刷新位置（周次、时间窗、位置、路线、官方线索图、刷新倒计时）。
  - 左侧栏可以查看其他位置：历史周次列表 + 地点库（按区域分组），支持搜索。
  - 页面只含本地图片与本地数据，不含任何第三方广告、导航、评论区内容。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "data" / "clean"
WEB = ROOT / "web"
WEB.mkdir(parents=True, exist_ok=True)

REGION_ORDER = ["Fadefields", "Terminus Range", "Carcadia Burns", "Dominion", "Elpis"]


def load(name: str) -> dict:
    return json.loads((CLEAN / name).read_text(encoding="utf-8"))


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>无主之地4 · 莫里斯黑市位置</title>
<style>
  :root{
    --bg:#0c0d12; --bg2:#12141c; --card:#171a24; --card2:#1e222e;
    --line:#2a2f3d; --txt:#e8eaf1; --dim:#9aa1b4; --dim2:#6b7285;
    --acc:#ffb72b; --acc2:#ff7a1a; --ok:#4ade80; --warn:#fbbf24; --bad:#f87171;
    --cyan:#5ee9e9;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;background:var(--bg);color:var(--txt);
    font:14px/1.6 "Microsoft YaHei UI","Microsoft YaHei","PingFang SC",system-ui,sans-serif;
    -webkit-font-smoothing:antialiased}
  a{color:var(--cyan);text-decoration:none}
  .app{display:grid;grid-template-columns:308px minmax(0,1fr);height:100vh}

  /* ---------- 左侧栏 ---------- */
  .sidebar{background:var(--bg2);border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
  .brand{padding:16px 18px 12px;border-bottom:1px solid var(--line)}
  .brand .t1{font-size:15px;font-weight:700;letter-spacing:.5px}
  .brand .t1 b{color:var(--acc)}
  .brand .t2{font-size:11px;color:var(--dim2);margin-top:3px}
  .search{padding:10px 12px;border-bottom:1px solid var(--line)}
  .search input{width:100%;background:var(--card);border:1px solid var(--line);color:var(--txt);
    border-radius:8px;padding:8px 10px;font-size:13px;outline:none}
  .search input:focus{border-color:#3d465c;box-shadow:0 0 0 2px rgba(255,183,43,.08)}
  .nav{overflow:auto;padding:8px 8px 24px;min-height:0;scrollbar-width:thin}
  .nav::-webkit-scrollbar{width:8px}
  .nav::-webkit-scrollbar-thumb{background:#2b3040;border-radius:4px}
  .grp{margin-top:10px}
  .grp>h3{margin:0 0 6px;padding:0 10px;font-size:11px;letter-spacing:1px;color:var(--dim2);
    text-transform:uppercase;font-weight:600}
  .row{display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:8px;cursor:pointer;
    color:var(--txt);border:1px solid transparent}
  .row:hover{background:var(--card)}
  .row.active{background:var(--card2);border-color:#3a4155}
  .row .wk{font-size:12px;color:var(--acc);min-width:44px;font-variant-numeric:tabular-nums}
  .row .nm{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .row .st{margin-left:auto;font-size:10px;padding:1px 6px;border-radius:999px;border:1px solid var(--line);color:var(--dim)}
  .row .st.ok{color:var(--ok);border-color:#1f5c3c;background:rgba(74,222,128,.07)}
  .row .st.warn{color:var(--warn);border-color:#5c4a1f;background:rgba(251,191,36,.07)}
  .row .st.na{color:var(--dim2)}
  .row .loc{font-size:11px;color:var(--dim);margin-left:auto;max-width:120px;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap}
  .row.small{padding:5px 10px}
  .row.small .nm{font-size:12.5px}
  .regionhead{display:flex;align-items:center;gap:8px;padding:8px 10px 4px;color:var(--dim);font-size:11.5px}
  .regionhead .dot{width:6px;height:6px;border-radius:50%;background:var(--acc)}

  /* ---------- 主区域 ---------- */
  main{overflow:auto;min-height:0;scrollbar-width:thin}
  main::-webkit-scrollbar{width:10px}
  main::-webkit-scrollbar-thumb{background:#2b3040;border-radius:5px}
  .wrap{max-width:1080px;margin:0 auto;padding:26px 30px 60px}

  .topline{display:flex;align-items:center;gap:10px;flex-wrap:wrap;color:var(--dim);font-size:12px}
  .pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;
    border:1px solid var(--line);background:var(--card);font-size:12px;color:var(--dim)}
  .pill.acc{color:var(--acc);border-color:#5a4318;background:rgba(255,183,43,.07)}
  .pill.ok{color:var(--ok);border-color:#1f5c3c;background:rgba(74,222,128,.07)}
  .pill.warn{color:var(--warn);border-color:#5c4a1f;background:rgba(251,191,36,.07)}
  .pill.bad{color:var(--bad);border-color:#5c2020;background:rgba(248,113,113,.07)}

  h1.hero{margin:14px 0 6px;font-size:34px;line-height:1.25;letter-spacing:.5px}
  h1.hero .small{font-size:16px;color:var(--dim);font-weight:400;margin-left:10px}
  .crumb{display:flex;flex-wrap:wrap;gap:6px;align-items:center;color:var(--dim);font-size:13px}
  .crumb .sep{color:var(--dim2)}
  .crumb .last{color:var(--txt)}

  .grid{display:grid;gap:14px;margin-top:18px}
  .grid.c2{grid-template-columns:1fr 1fr}
  .grid.c3{grid-template-columns:repeat(3,1fr)}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
  .card h2{margin:0 0 10px;font-size:13px;letter-spacing:1px;color:var(--dim2);font-weight:700;text-transform:uppercase}
  .card .big{font-size:20px;font-weight:700;color:var(--txt)}
  .kv{margin:8px 0 0}
  .kv .k{color:var(--dim2);font-size:12px}
  .kv .v{margin-top:2px;font-size:13.5px}

  .count{display:flex;gap:18px;align-items:baseline}
  .count .num{font-size:30px;font-weight:800;color:var(--acc);font-variant-numeric:tabular-nums;letter-spacing:1px}
  .count .lab{color:var(--dim2);font-size:12px}

  .hintwrap{position:relative;border-radius:12px;overflow:hidden;border:1px solid var(--line);background:#000;
    display:flex;align-items:center;justify-content:center}
  .hintwrap img{display:block;width:100%;height:auto;cursor:zoom-in}
  .hintbar{display:flex;align-items:center;gap:10px;margin:8px 0 0;font-size:12px;color:var(--dim2)}
  .tag{font-size:11px;padding:2px 8px;border-radius:6px;background:var(--card2);border:1px solid var(--line);color:var(--dim)}

  .howto{font-size:14px;line-height:1.85;color:#d7dbea;background:var(--card2);border-left:3px solid var(--acc);
    border-radius:0 10px 10px 0;padding:12px 14px}
  .note{font-size:12.5px;color:var(--dim);line-height:1.7}
  .note b{color:var(--txt)}
  .gall{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-top:10px}
  .gall .item{border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--card2);cursor:pointer}
  .gall .item:hover{border-color:#4a5369}
  .gall .item img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover}
  .gall .item .cap{padding:7px 9px;font-size:12px;color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

  table.src{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:6px}
  table.src td,table.src th{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}
  table.src th{color:var(--dim2);font-weight:600}
  table.src td.dim{color:var(--dim)}

  .list{display:flex;flex-direction:column;gap:6px}
  .wkrow{display:flex;align-items:center;gap:12px;padding:8px 10px;border-radius:9px;border:1px solid var(--line);
    background:var(--card);cursor:pointer}
  .wkrow:hover{background:var(--card2)}
  .wkrow .n{font-size:12px;color:var(--acc);min-width:52px;font-variant-numeric:tabular-nums}
  .wkrow .d{font-size:12px;color:var(--dim2);min-width:150px;font-variant-numeric:tabular-nums}
  .wkrow .l{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

  footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);color:var(--dim2);font-size:11.5px;line-height:1.8}

  .lightbox{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;
    z-index:99;padding:24px;cursor:zoom-out}
  .lightbox.on{display:flex}
  .lightbox img{max-width:96vw;max-height:92vh;border-radius:8px;box-shadow:0 20px 60px rgba(0,0,0,.6)}

  @media (max-width:900px){
    .app{grid-template-columns:1fr;grid-template-rows:auto 1fr;height:auto}
    .sidebar{max-height:42vh}
    .grid.c2,.grid.c3{grid-template-columns:1fr}
    h1.hero{font-size:26px}
  }
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <div class="t1">无主之地4 · <b>莫里斯黑市</b></div>
      <div class="t2">Maurice's Black Market · 位置速查</div>
    </div>
    <div class="search"><input id="q" placeholder="搜索：周次 / 日期 / 地点名"></div>
    <div class="nav" id="nav"></div>
  </aside>
  <main><div class="wrap" id="main"></div></main>
</div>
<div class="lightbox" id="lb"><img id="lbimg" alt=""></div>

<script>
const DATA = __DATA__;
const WEEK1 = Date.parse(`${DATA.meta.week1_start}T00:00:00+08:00`);
const WEEK_MS = 7*24*3600*1000;
const HOUR_MS = 3600000;
const pacificHour = new Intl.DateTimeFormat('en', {timeZone:'America/Los_Angeles', hour:'numeric', hourCycle:'h23'});

const byWeek = new Map(DATA.weeks.map(w=>[w.week,w]));
const byKey  = new Map(DATA.locations.map(l=>[l.key,l]));
const REGION_ORDER = ['Fadefields','Terminus Range','Carcadia Burns','Dominion','Elpis'];

function nowWeek(){ return Math.floor((Date.now()-WEEK1)/WEEK_MS)+1; }
function nextReset(now = Date.now()){
  // 北京周五 00:00 对应太平洋时间周四 08:00 或 09:00。
  const friday = WEEK1 + Math.floor((now-WEEK1)/WEEK_MS)*WEEK_MS;
  const estimate = midnight => midnight + (9-Number(pacificHour.format(midnight)))*HOUR_MS;
  const thisReset = estimate(friday);
  return thisReset > now ? thisReset : estimate(friday+WEEK_MS);
}
function fmtDate(s){ return s; }
function esc(s){ return (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function locationSearchText(loc){
  return loc ? [loc.cn, loc.en, loc.jp, ...loc.chain_cn, ...loc.chain_jp, loc.howto_cn].join(' ').toLowerCase() : '';
}
function statusPill(w){
  if(w.location_status==='verified') return '<span class="pill ok">位置已确认</span>';
  if(w.location_status==='reported') return '<span class="pill ok">社区实拍确认</span>';
  if(w.location_status==='tentative') return '<span class="pill warn">位置：推测</span>';
  return '<span class="pill warn">位置：待社区确认</span>';
}
function weekRow(w, active){
  const loc = byKey.get(w.location_key);
  const st = w.location_status==='verified'||w.location_status==='reported' ? 'ok' : (w.location_status==='tentative'?'warn':'na');
  const label = w.location_status==='verified' ? '已确认' : (w.location_status==='reported' ? '实拍' : (w.location_status==='tentative' ? '推测' : '待确认'));
  return `<div class="row ${active?'active':''}" data-view="week:${w.week}">
    <span class="wk">W${w.week}</span><span class="nm">${fmtDate(w.start).slice(5)} → ${fmtDate(w.end).slice(5)}</span>
    <span class="st ${st}">${label}</span>
  </div>`;
}

/* ---------------- 侧边栏 ---------------- */
let curView = null;
function renderNav(filter){
  const q = (filter||'').trim().toLowerCase();
  const cw = nowWeek();
  const nav = document.getElementById('nav');
  let h = '';

  const navItems = [
    `<div class="row ${curView==='about'?'active':''}" data-view="about"><span class="wk">?</span><span class="nm">机制与数据来源</span></div>`
  ];
  h += `<div class="grp"><h3>当前</h3>${navItems.join('')}</div>`;

  // 周次（倒序）
  const weeks = [...DATA.weeks].sort((a,b)=>b.week-a.week);
  const wrows = weeks.filter(w=>{
    if(!q) return true;
    const loc = byKey.get(w.location_key);
    const hay = `w${w.week} 第${w.week}周 第 ${w.week} 周 ${w.start} ${w.end} ${locationSearchText(loc)}`;
    return hay.includes(q);
  }).map(w=>weekRow(w, curView==='week:'+w.week)).join('');
  const weekBlock = `<div class="grp"><h3>周次（第 ${cw} 周为本周）</h3>${wrows}</div>`;

  // 地点库（按区域）
  const groups = {};
  for(const l of DATA.locations){
    const r = l.region_en || '其他';
    (groups[r] = groups[r] || []).push(l);
  }
  const order = REGION_ORDER.filter(r=>groups[r]).concat(Object.keys(groups).filter(r=>!REGION_ORDER.includes(r)));
  let lrows = '';
  for(const r of order){
    const items = groups[r].filter(l=>{
      if(!q) return true;
      return locationSearchText(l).includes(q);
    });
    if(!items.length) continue;
    lrows += `<div class="regionhead"><span class="dot"></span>${esc(items[0].chain_cn[0])}</div>`;
    for(const l of items){
      const active = curView==='loc:'+l.key;
      const seen = l.weeks && l.weeks.length ? 'W'+l.weeks.join(',W') : '';
      lrows += `<div class="row small ${active?'active':''}" data-view="loc:${l.key}">
        <span class="nm" title="${esc(l.en + ' · ' + l.jp)}">${esc(l.cn)}</span>${seen?`<span class="loc">${seen}</span>`:''}</div>`;
    }
  }
  if(lrows) h += `<div class="grp"><h3>地点库（${DATA.locations.length} 个已知点位）</h3>${lrows}</div>`;
  h += weekBlock;

  nav.innerHTML = h;
}

/* ---------------- 视图 ---------------- */
function renderWeek(week){
  const w = byWeek.get(week);
  if(!w) return renderMissing(week);
  const loc = byKey.get(w.location_key);
  const cw = nowWeek();
  const isNow = week===cw;
  const st = statusPill(w);

  const locBlock = loc ? `
    <div class="grid c2">
      <div class="card">
        <h2>怎么去</h2>
        <div class="howto">${esc(loc.howto_cn)}</div>
        <div class="kv"><div class="k">地图路径</div><div class="v">${loc.chain_cn.map(esc).join(' <span style="color:#5b6379">›</span> ')}</div></div>
        <div class="kv"><div class="k">出现过</div><div class="v">${(loc.weeks&&loc.weeks.length)?loc.weeks.map(x=>`<a href="#week-${x}" data-view="week:${x}">W${x}</a>`).join('、'):'—'}</div></div>
      </div>
      <div class="card">
        <h2>地点实景（社区截图）</h2>
        <div class="hintwrap" style="aspect-ratio:16/9"><img src="${loc.img}" alt="${esc(loc.cn)}" data-zoom="${loc.img}"></div>
        <div class="hintbar"><span class="tag">${esc(loc.cn)}</span><span>${esc(loc.en)}</span></div>
      </div>
    </div>` : `
    <div class="card" style="margin-top:18px">
      <h2>本周位置：官方只给线索图</h2>
      <div class="note" style="margin-bottom:10px">
        2K 每周只发布一张<b>官方线索图</b>，不公布地名；社区确认后才会写入数据。
        当前版本尚未拿到本周的社区确认，所以这里先显示官方线索图，并把已知地点的实景放在下方供比对。
      </div>
      ${w.hint_full?`<div class="hintwrap"><img src="${w.hint_full}" alt="本周官方线索图" data-zoom="${w.hint_full}"></div>
      <div class="hintbar"><span class="tag">官方线索图</span><span>点击放大 · 与下方“地点实景”对照地形</span></div>`:'<div class="note">本周官方线索图尚未收录。</div>'}
    </div>`;

  const hintCard = (loc && w.hint_full) ? `
    <div class="card" style="margin-top:14px">
      <h2>本周官方线索图</h2>
      <div class="hintwrap"><img src="${w.hint_full}" alt="官方线索图" data-zoom="${w.hint_full}"></div>
      <div class="hintbar"><span class="tag">官方</span><span>与上面的地点实景对照，可确认机器具体藏点</span></div>
    </div>` : '';

  const compare = loc ? '' : `
    <div class="card" style="margin-top:14px">
      <h2>已知地点实景对照（帮助比对线索图）</h2>
      <div class="note">按区域排列，共 ${DATA.locations.length} 个点位。点任意一张进入地点详情。</div>
      <div class="gall">
        ${DATA.locations.map(l=>`<div class="item" data-view="loc:${l.key}">
          <img src="${l.img}" alt="${esc(l.cn)}">
          <div class="cap">${esc(l.cn)}</div>
        </div>`).join('')}
      </div>
    </div>`;

  const countdown = isNow ? `
    <div class="card" style="margin-top:14px">
      <h2>距离下次刷新</h2>
    <div class="count"><span class="num" id="cd">--</span><span class="lab">按太平洋时间周四 09:00 估算</span></div>
    </div>` : '';

  document.getElementById('main').innerHTML = `
    <div class="topline">
      <span class="pill acc">第 ${w.week} 周</span>
      <span class="pill">${w.start} → ${w.end}</span>
      ${isNow?'<span class="pill ok">本周</span>':''}
      ${st}
      ${w.official?'<span class="pill">官方活动已发布</span>':'<span class="pill">官方活动帖未收录</span>'}
    </div>
    <h1 class="hero">${loc?esc(loc.cn):'本周位置待确认'}${loc?`<span class="small">${esc(loc.en)}</span>`:''}</h1>
    ${loc?`<div class="crumb">${loc.chain_cn.map((x,i)=>`<span class="${i===loc.chain_cn.length-1?'last':''}">${esc(x)}</span>`).join('<span class="sep">›</span>')}</div>`:
      `<div class="crumb"><span>${w.hint_full?'已收录官方线索图':'暂无本地线索图'}</span><span class="sep">·</span><span>等待社区确认点位</span></div>`}

    ${w.location_note?`<div class="card" style="margin-top:14px"><h2>备注</h2><div class="note">${esc(w.location_note)}</div></div>`:''}

    ${locBlock}
    ${hintCard}
    <div class="grid c3" style="margin-top:14px">
      <div class="card"><h2>每周盛大安可首领</h2><div class="big" title="${esc(w.boss)}">${esc(w.boss_cn || '—')}</div></div>
      <div class="card" style="grid-column:span 2"><h2>每周万用任务</h2><div class="big" style="font-size:16px" title="${esc(w.wildcard)}">${esc(w.wildcard_cn || '—')}</div></div>
    </div>
    ${countdown}
    ${compare}
    <footer>
      数据：官方每周活动公告（线索图） + Gamerch 日文 Wiki（地点区域链/实景） + FPS Index（早期路线） + Eurogamer（2026-02 位置）。
      周次按北京时间的周五至下周四编号；第 ${cw} 周为本周。
      页面仅含本地数据与本地图片，无第三方广告。
    </footer>`;
  startCountdown(isNow);
}

function renderLoc(key){
  const l = byKey.get(key);
  if(!l) return renderMissing('?');
  const cw = nowWeek();
  document.getElementById('main').innerHTML = `
    <div class="topline"><span class="pill acc">已知地点</span>
      ${l.weeks&&l.weeks.length?`<span class="pill ok">已确认出现：${l.weeks.map(w=>'第'+w+'周').join('、')}</span>`:'<span class="pill">尚无对应周次记录</span>'}
    </div>
    <h1 class="hero">${esc(l.cn)}<span class="small">${esc(l.en)}</span></h1>
    <div class="crumb">${l.chain_cn.map((x,i)=>`<span class="${i===l.chain_cn.length-1?'last':''}">${esc(x)}</span>`).join('<span class="sep">›</span>')}</div>

    <div class="grid c2" style="margin-top:18px">
      <div class="card">
        <h2>怎么去</h2>
        <div class="howto">${esc(l.howto_cn)}</div>
        <div class="kv"><div class="k">日文原名</div><div class="v">${esc(l.jp)}</div></div>
        <div class="kv"><div class="k">社区记录中的出现顺序</div><div class="v">${(l.orders||[]).join('、')}</div></div>
        <div class="kv"><div class="k">来源</div><div class="v" style="color:var(--dim)">${(l.sources||[]).join('；')}</div></div>
      </div>
      <div class="card">
        <h2>地点实景（社区截图）</h2>
        <div class="hintwrap" style="aspect-ratio:16/9"><img src="${l.img}" alt="${esc(l.cn)}" data-zoom="${l.img}"></div>
        <div class="hintbar"><span class="tag">对照用</span><span>机器不显示地图标记，靠近才显形</span></div>
      </div>
    </div>
    <footer>
      <a href="#week-${cw}" data-view="week:${cw}">← 回到本周</a>
    </footer>`;
}

function renderAbout(){
  const m = DATA.meta;
  document.getElementById('main').innerHTML = `
    <div class="topline"><span class="pill acc">机制</span><span class="pill">数据来源</span><span class="pill">无广告</span></div>
    <h1 class="hero">莫里斯黑市：规则与数据说明</h1>
    <div class="crumb"><span>每周固定时间换位置 · 不显示地图标记 · 靠近才显形</span></div>

    <div class="grid c2" style="margin-top:18px">
      <div class="card">
        <h2>刷新时间</h2>
        <div class="big">${esc(m.reset.weekday_cn)}凌晨（北京时间）</div>
        <div class="note" style="margin-top:8px">${esc(m.reset.rule_cn)}<br><span style="color:var(--dim2)">${esc(m.reset.source_note)}</span></div>
      </div>
      <div class="card">
        <h2>周次计算</h2>
        <div class="note">以 ${esc(m.week1_start)} 为第 1 周起点，7 天一周；第 1 周对应官方 2025-09-12 当周。当前为第 ${nowWeek()} 周。</div>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <h2>核心事实</h2>
      <div class="list">${(m.facts||[]).map(f=>`<div class="kv"><div class="k">${esc(f.k)}</div><div class="v">${esc(f.v)}</div></div>`).join('')}</div>
    </div>

    <div class="card" style="margin-top:14px">
      <h2>数据来源（含可信度）</h2>
      <p class="note">部分地点、首领、任务与词缀采用参考译名。地点可用英日文原名搜索，活动原文可在名称上悬停查看。</p>
      <table class="src"><tr><th>编号</th><th>来源</th><th>可信度</th><th>用途</th></tr>
      ${(m.sources||[]).map(s=>`<tr><td class="dim">${esc(s.id)}</td><td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a></td><td class="dim">${esc(s.trust)}</td><td class="dim">${esc(s.note)}</td></tr>`).join('')}
      </table>
    </div>

    <div class="card" style="margin-top:14px">
      <h2>为什么以前的数据里会有“广告”？</h2>
      <div class="note">${esc(m.ads_note)}</div>
    </div>

    <footer>单文件本地页面，数据生成于 ${esc(m.generated_at)}。每周更新：编辑 data/manual_weeks.json 后运行 python src/build_data.py 和 python src/build_site.py。</footer>`;
}

function renderMissing(x){
  const lastWeek = DATA.weeks.at(-1).week;
  document.getElementById('main').innerHTML = `<h1 class="hero">暂无数据</h1>
    <div class="note">本地数据已收录到第 ${lastWeek} 周。第 ${esc(x)} 周的点位待录入。</div>
    <p><a href="#week-${lastWeek}" data-view="week:${lastWeek}">查看最近收录的周次</a></p>`;
}

/* 倒计时 */
let cdTimer = null;
function startCountdown(on){
  if(cdTimer){ clearInterval(cdTimer); cdTimer=null; }
  const el = document.getElementById('cd');
  if(!el) return;
  const tick = ()=>{
    const el2 = document.getElementById('cd');
    if(!el2) return;
    const now = Date.now();
    const t = nextReset(now) - now;
    const d = Math.floor(t/86400000), h=Math.floor(t%86400000/3600000), mi=Math.floor(t%3600000/60000), s=Math.floor(t%60000/1000);
    el2.textContent = `${d} 天 ${String(h).padStart(2,'0')}:${String(mi).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  };
  tick(); cdTimer = setInterval(tick, 1000);
}

/* 路由 */
function route(){
  const h = decodeURIComponent(location.hash.replace(/^#/,''));
  let view = null;
  if(h.startsWith('week-')) view = 'week:'+parseInt(h.slice(5),10);
  else if(h.startsWith('loc-')) view = 'loc:'+h.slice(4);
  else if(h==='about') view = 'about';
  if(!view) view = 'week:'+nowWeek();
  curView = view;
  const [kind, arg] = view.split(':');
  if(kind==='week'){ renderWeek(parseInt(arg,10)); }
  else if(kind==='loc'){ renderLoc(arg); }
  else { renderAbout(); }
  renderNav(document.getElementById('q').value);
  document.querySelector('main').scrollTop = 0;
}

/* 事件 */
document.addEventListener('click', (e)=>{
  const viewEl = e.target.closest('[data-view]');
  if(viewEl){
    const v = viewEl.getAttribute('data-view');
    location.hash = v.replace(':','-');
    return;
  }
  const zoom = e.target.closest('[data-zoom]');
  if(zoom){
    document.getElementById('lbimg').src = zoom.getAttribute('data-zoom');
    document.getElementById('lb').classList.add('on');
  }
});
document.getElementById('lb').addEventListener('click', ()=>{ document.getElementById('lb').classList.remove('on'); });
document.getElementById('q').addEventListener('input', (e)=> renderNav(e.target.value));
window.addEventListener('hashchange', route);
route();
</script>
</body>
</html>
"""


def main() -> int:
    meta = load("meta.json")
    locations = load("locations.json")["locations"]
    weeks = load("weeks.json")["weeks"]

    for week in weeks:
        for field in ("hint_thumb", "hint_full"):
            if week.get(field) and not (WEB / week[field]).is_file():
                week[field] = None
    data = {"meta": meta, "locations": locations, "weeks": weeks}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    html = HTML.replace("__DATA__", payload)
    (WEB / "index.html").write_text(html, encoding="utf-8")
    print(f"written web/index.html ({len(html)/1024:.0f} KB), weeks={len(weeks)}, locations={len(locations)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
