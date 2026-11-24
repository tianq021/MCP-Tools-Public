const weekMs = 7 * 24 * 3600 * 1000;
const firstFriday = Date.UTC(2021, 3, 9);
// Match the seed on the Friday after the date shown by the US rotation guides.
const firstSeed = 1155;
const featuredWeek = 293;
const beijingOffset = 8 * 3600 * 1000;
const todayWeek = 1 + Math.floor((Date.now() + beijingOffset - firstFriday) / weekMs);
const maxWeek = Math.max(todayWeek, featuredWeek) + locationNotes.length;
const dateForWeek = week => new Date(firstFriday + (week - 1) * weekMs).toISOString().slice(0, 10);

// Based on apple1417's research. Each entry is picked once within its own round.
function rotationIndex(seed, length) {
  const available = Array.from({length}, (_, index) => index);
  const roundStart = seed - seed % length;
  for (let week = roundStart; week <= seed; week++) {
    const fraction = ((Math.imul(week, 0xbb38435) + 0x3619636b) & 0x7fffff) / 0x800000;
    const index = Math.floor(fraction * available.length);
    if (week === seed) return available[index];
    available.splice(index, 1);
  }
}

const locationEntries = marketData.locations.map(([name, source], index) => {
  const id = index + 1;
  const disabled = id === 12;
  const map = disabled ? 'Meridian Metroplex' : name;
  const [planet, cn] = mapNames[map];
  const image = id === 28 ? 'assets/map.jpg' : `assets/locations/${String(id === 2 ? 1 : id).padStart(2, '0')}.webp`;
  return {id, planet, map, cn, source, image, note: locationNotes[index], disabled};
});
const locations = locationEntries.filter(entry => entry.id !== 2);
const pools = marketData.pools.map((stock, index) => ({id: index + 1, stock}));
const weeks = Array.from({length: maxWeek}, (_, index) => {
  const id = index + 1;
  const seed = firstSeed + index;
  const record = marketData.observed[id];
  const rawLocation = record?.id ?? rotationIndex(seed, locationEntries.length) + 1;
  const location = locationEntries[rawLocation === 2 ? 0 : rawLocation - 1];
  const pool = record && id <= 32 ? null : pools[rotationIndex(seed, pools.length)];
  return {id, location, pool, stock: record?.stock ?? pool.stock, observed: Boolean(record), date: dateForWeek(id)};
});

const alternateGearNames = {
  'AutoAimé': '自动瞄准', DNA: 'D.N.A.', 'King’s Call': '国王的召唤',
  'Queen’s Call': '女王的召唤', Shredifier: '粉碎者',
  'Sledge’s Shotgun': '斯莱奇霰弹枪', 'Smart Gun XXL': '智能枪 XXL'
};
const translatedGear = name => name.split(' / ').map(part => gearNames[part] || alternateGearNames[part] || part).join(' / ');
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[char]));
const stockNames = stock => stock.map(translatedGear).join('、');
const locationLabel = location => `${location.cn} · ${location.note}`;
const recordStatus = week => week.observed ? '社区实录' : '算法推算';

const modes = {weeks: [...weeks].reverse(), locations, pools};
const modeButtons = document.querySelectorAll('[data-mode]');
const search = document.querySelector('#catalog-search');
const list = document.querySelector('#catalog-list');
const detail = document.querySelector('#catalog-detail');
const count = document.querySelector('#catalog-count');
let mode = 'weeks';
let selected = Math.min(maxWeek, Math.max(1, todayWeek));

function stockList(stock) {
  return `<ul class="gear-list">${stock.map(name =>
    `<li><strong>${escapeHtml(translatedGear(name))}</strong><small>${escapeHtml(name)}</small></li>`).join('')}</ul>`;
}

function renderLocation(place) {
  return `<p class="quiet">${escapeHtml(place.planet)} · ${escapeHtml(place.map)} · 点位 ${place.id}</p>
    <h3>${escapeHtml(place.cn)}</h3>
    <p>${escapeHtml(place.note)}</p>
    ${place.disabled ? '<p class="unavailable">此位置条目被禁用。抽到这条时，贩卖机不会出现在该点。</p>' : ''}
    <figure class="catalog-figure">
      <a class="zoom" href="${place.image}" data-zoom aria-label="放大点位图">
        <img src="${place.image}" alt="${escapeHtml(locationLabel(place))}的游戏内点位图">
        <span class="zoom-label" aria-hidden="true">放大 ↗</span>
      </a>
      <figcaption>同点位历史参考图。<a href="${escapeHtml(place.source)}">MentalMars 原图 ↗</a></figcaption>
    </figure>`;
}

function renderDetail(entry) {
  if (!entry) {
    detail.innerHTML = '<p>没有找到符合条件的记录。</p>';
  } else if (mode === 'locations') {
    detail.innerHTML = renderLocation(entry) + `<button class="catalog-jump" data-search="点位 ${entry.id}">查此点位的周次</button>`;
  } else if (mode === 'pools') {
    detail.innerHTML = `<h3>库存组合 ${entry.id}</h3>${stockList(entry.stock)}
      <button class="catalog-jump" data-search="库存 ${entry.id}">查此组合的周次</button>`;
  } else {
    detail.innerHTML = `<p class="catalog-week">第 ${entry.id} 周 · ${entry.date} 至 ${dateForWeek(entry.id + 1)}
      <span class="catalog-status">${recordStatus(entry)}</span></p>
      ${renderLocation(entry.location)}
      <h4>${entry.location.disabled ? '算法选出的库存' : '本周库存'}${entry.pool ? ` · 组合 ${entry.pool.id}` : ''}</h4>${stockList(entry.stock)}`;
  }
}

function entryText(entry) {
  if (mode === 'locations') return `${entry.planet} ${entry.map} ${locationLabel(entry)}`;
  if (mode === 'pools') return `库存 ${entry.id} ${stockNames(entry.stock)} ${entry.stock.join(' ')}`;
  const {id, date, location, pool, stock} = entry;
  return `w${id} 第${id}周 第 ${id} 周 ${date} ${location.planet} ${location.map} ${locationLabel(location)} ${stockNames(stock)} ${stock.join(' ')}`;
}

function rowLabel(entry) {
  if (mode === 'locations') return [entry.cn, entry.note];
  if (mode === 'pools') return [`库存组合 ${entry.id}`, stockNames(entry.stock)];
  const marker = entry.id === todayWeek ? ' · 本周' : entry.id > todayWeek ? ' · 未来' : '';
  return [`${entry.date} · ${entry.location.cn}`, `第 ${entry.id} 周 · ${recordStatus(entry)}${marker}`];
}

function renderCatalog() {
  const query = search.value.trim().toLowerCase();
  const exact = mode === 'weeks' && /^(库存|点位)\s*(\d+)$/.exec(query);
  const matches = modes[mode].filter(entry => {
    if (!exact) return entryText(entry).toLowerCase().includes(query);
    const linked = exact[1] === '库存' ? entry.pool : entry.location;
    return linked?.id === Number(exact[2]);
  });
  const active = matches.find(entry => entry.id === selected) || matches[0];
  selected = active?.id;
  count.textContent = `${matches.length} 条`;
  modeButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === mode)));
  const row = entry => {
    const [title, subtitle] = rowLabel(entry);
    return `<button data-id="${entry.id}" aria-current="${entry.id === selected}">
      <strong>${escapeHtml(title)}</strong><small>${escapeHtml(subtitle)}</small></button>`;
  };
  if (mode === 'locations') {
    list.innerHTML = [...new Set(matches.map(entry => entry.planet))].map(planet =>
      `<h3>${escapeHtml(planet)}</h3>${matches.filter(entry => entry.planet === planet).map(row).join('')}`).join('');
  } else {
    list.innerHTML = matches.map(row).join('');
  }
  renderDetail(active);
}

search.addEventListener('input', renderCatalog);
modeButtons.forEach(button => button.addEventListener('click', () => {
  mode = button.dataset.mode;
  selected = mode === 'weeks' ? todayWeek : undefined;
  list.scrollTop = 0;
  renderCatalog();
}));
list.addEventListener('click', event => {
  const button = event.target.closest('[data-id]');
  if (!button) return;
  list.querySelector('[aria-current="true"]')?.setAttribute('aria-current', 'false');
  button.setAttribute('aria-current', 'true');
  selected = Number(button.dataset.id);
  renderDetail(modes[mode].find(entry => entry.id === selected));
});
detail.addEventListener('click', event => {
  const button = event.target.closest('[data-search]');
  if (!button) return;
  mode = 'weeks';
  search.value = button.dataset.search;
  list.scrollTop = 0;
  renderCatalog();
  search.focus();
});

const current = weeks[todayWeek - 1];
if (current && current.id !== featuredWeek) {
  document.querySelector('.hero-copy .section-label').textContent = '莫里斯黑市 · 本周';
  document.querySelector('#title').textContent = current.location.disabled ? '本周抽到禁用点位' : current.location.cn;
  document.querySelector('.breadcrumb').textContent = `${current.location.planet} › ${current.location.cn}`;
  document.querySelector('.summary').textContent = `${current.location.note} · ${recordStatus(current)}`;
  const currentLink = document.querySelector('.primary');
  currentLink.setAttribute('href', '#catalog');
  currentLink.textContent = '查看本周地图与库存 ↓';
  currentLink.addEventListener('click', () => {
    mode = 'weeks';
    selected = todayWeek;
    search.value = '';
    renderCatalog();
    scrollToActiveWeek();
  });
  document.querySelector('.rotation .dates').textContent = `${current.date} — ${dateForWeek(current.id + 1)}`;
  document.querySelector('#rotation-status').textContent = recordStatus(current);
  document.querySelector('.rotation .quiet').textContent = '北京时间 · 周五轮换';
  document.querySelector('#map-title').textContent = '第 293 周实录 · 雅各布斯庄园';
  document.querySelector('#stock-title').textContent = '第 293 周在售装备';
}

function scrollToActiveWeek() {
  const row = list.querySelector('[aria-current="true"]');
  if (row) list.scrollTop += row.getBoundingClientRect().top - list.getBoundingClientRect().top - 65;
}

renderCatalog();
scrollToActiveWeek();
