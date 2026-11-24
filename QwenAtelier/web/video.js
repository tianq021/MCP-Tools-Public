'use strict';
const videoPanel = (() => {
  const el = id => document.getElementById('wan-' + id);
  const show = (id, visible) => el(id).classList.toggle('hidden', !visible);
  const form = el('form');
  const player = el('player');
  player.addEventListener('loadedmetadata', () => {
    if (player.videoWidth && player.videoHeight) {
      player.parentElement.style.aspectRatio = player.videoWidth + ' / ' + player.videoHeight;
    }
  });
  let visible = false, timer, epoch = 0, snapshot, initialized = false, sending = false;
  let historyKey = null, selected = '', lastDone = '';
  const key = 'qwen-wan-video-settings-v1';
  const fields = ['mode', 'preset', 'frames', 'fps', 'steps', 'seed'];

  function saveSettings() {
    if (!initialized) return;
    const values = {auto:el('auto').checked};
    for (const name of fields) {
      const input = form.elements.namedItem(name);
      if (input.validity.valid) values[name] = input.value;
    }
    try { localStorage.setItem(key, JSON.stringify(values)); } catch {}
  }
  function restoreSettings() {
    let saved;
    try { saved = JSON.parse(localStorage.getItem(key)); } catch {}
    if (!saved || typeof saved !== 'object') return;
    el('auto').checked = saved.auto === true;
    for (const name of fields) {
      const input = form.elements.namedItem(name);
      if (typeof saved[name] !== 'string') continue;
      const previous = input.value;
      input.value = saved[name];
      if (!input.validity.valid || (input.tagName === 'SELECT' && !input.value)) input.value = previous;
      if (name === 'preset') applyPreset();
    }
    setMode();
    duration();
  }

  function duration() {
    const seconds = Number(el('frames').value) / Number(el('fps').value);
    el('duration').textContent = Number.isFinite(seconds) && seconds > 0 ? '时长约 ' + seconds.toFixed(2) + ' 秒' : '';
  }
  function applyPreset() {
    const preset = snapshot.presets[el('preset').value];
    el('frames').value = preset.frames;
    el('fps').value = preset.fps;
    duration();
  }
  function setMode() {
    const image = el('mode').value === 'image';
    show('reference', image);
    el('image').required = image;
  }
  function selectVideo(id) {
    selected = id;
    const url = '/api/video/files/' + id + '.mp4';
    el('player').src = url;
    el('download').href = url;
    el('download').download = 'Wan-' + id + '.mp4';
    show('download', true);
    show('player', true);
    show('empty', false);
  }
  function buttons() {
    const model = snapshot?.model.state;
    const active = snapshot?.active;
    const busy = !model || sending || !!active;
    el('preload').disabled = busy || !['unloaded', 'error'].includes(model);
    el('unload').disabled = busy || !['ready', 'loading'].includes(model);
    el('submit').disabled = busy || ['loading', 'unloading'].includes(model);
    show('cancel', !!active);
    el('cancel').disabled = sending || !!active?.cancelled;
  }
  function render(data) {
    snapshot = data;
    if (!initialized) {
      for (const [key, preset] of Object.entries(data.presets)) el('preset').add(new Option(preset.label, key));
      applyPreset();
      el('steps').value = data.steps;
      el('negative').value = data.negative;
      const s = data.sampling;
      el('sampler').textContent = [s.sampler, s.scheduler, 'CFG ' + s.cfg, 'Shift ' + s.shift].join(' · ');
      initialized = true;
      restoreSettings();
    }
    el('model').textContent = data.model.state === 'ready' ? '已预加载 · 主模型、文本编码器和 VAE 可复用' : data.model.stage;
    const job = data.active;
    el('status').textContent = job ? job.stage + (job.total ? ' · ' + job.current + ' / ' + job.total : '') :
      (data.last?.error || data.last?.stage || '等待生成');
    show('progress', !!job);
    if (job?.total) {
      el('progress').max = job.total;
      el('progress').value = job.current;
    } else el('progress').removeAttribute('value');
    const key = data.history.map(item => item.id).join(',');
    if (key !== historyKey) {
      historyKey = key;
      el('history').replaceChildren();
      if (!key) el('history').textContent = '还没有生成记录';
      for (const item of data.history) {
        const button = document.createElement('button');
        button.className = 'button subtle';
        button.textContent = item.prompt + ' · ' + item.width + '×' + item.height + ' · ' + (item.frames / item.fps).toFixed(2) + ' 秒';
        button.onclick = () => selectVideo(item.id);
        el('history').append(button);
      }
    }
    if (data.history.length && (!selected || (data.last?.status === 'done' && data.last.id !== lastDone))) {
      selectVideo(data.history[0].id);
      lastDone = data.last?.id || '';
    }
    buttons();
  }
  async function refresh(connect = false) {
    clearTimeout(timer);
    const ticket = ++epoch;
    try {
      if (connect) await api('/video/open', {method:'POST'});
      const data = await api('/video/status');
      if (ticket !== epoch) return;
      render(data);
      show('reconnect', false);
      if (connect && el('auto').checked && data.model.state === 'unloaded' && !data.active) {
        await action('model/preload');
      }
    } catch (error) {
      if (ticket !== epoch) return;
      snapshot = null;
      buttons();
      el('model').textContent = error.message;
      show('reconnect', true);
    } finally {
      if (ticket === epoch && visible) timer = setTimeout(refresh, 1800);
    }
  }
  async function action(path, body) {
    if (sending) return;
    sending = true;
    buttons();
    el('error').textContent = '';
    try {
      await api('/video/' + path, {method:'POST', body});
    } catch (error) {
      el('error').textContent = error.message;
    } finally {
      sending = false;
      if (visible) await refresh();
    }
  }
  el('mode').onchange = setMode;
  el('frames').oninput = el('fps').oninput = duration;
  el('preset').onchange = applyPreset;
  form.addEventListener('change', saveSettings);
  el('auto').onchange = () => {
    if (el('auto').checked && snapshot?.model.state === 'unloaded' && !snapshot.active) action('model/preload');
  };
  el('reconnect').onclick = () => refresh(true);
  el('preload').onclick = () => action('model/preload');
  el('unload').onclick = () => action('model/unload');
  el('cancel').onclick = () => {
    if (snapshot?.active) action('jobs/' + snapshot.active.id + '/cancel');
  };
  form.addEventListener('invalid', e => {
    const details = e.target.closest('details');
    if (details) details.open = true;
  }, true);
  form.onsubmit = event => {
    event.preventDefault();
    const data = new FormData(form);
    if (el('mode').value === 'image') {
      const file = el('image').files[0];
      if (!file || file.size > 16 * 1024**2) {
        el('error').textContent = '请选择 16 MiB 以内的参考图';
        return;
      }
      data.set('image', file);
    }
    action('jobs', data);
  };
  el('player').onerror = () => { el('error').textContent = '当前浏览器无法播放这段视频，可以下载 MP4 查看。'; };
  return {
    navigate(active) {
      if (active === visible) return;
      visible = active;
      clearTimeout(timer);
      ++epoch;
      if (active) refresh(true);
      else el('player').pause();
    }
  };
})();
