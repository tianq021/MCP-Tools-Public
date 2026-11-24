/*
 * Qwen Atelier · AI 放大 / AI 扩图 独立界面模块（第一批）
 *
 * 设计约束（见 IMAGE_TOOLS_CONTRACT.md）：
 * - 普通 JavaScript，无框架、无构建、无 CDN；唯一全局入口 window.QwenImageTools。
 * - 只操作传入 root 内的 DOM；所有请求都经过注入的 api(path, {method, body})。
 * - 不读取全局 state，不自己取 token，不直接 fetch。
 * - 接口缺失或请求失败即禁用提交，不假设能力可用，不回落到 mock 数据。
 */
(function (global) {
  'use strict';

  var UID = 0;
  var ACCEPTED_MIME = ['image/png', 'image/jpeg', 'image/webp'];
  var ACCEPTED_NAME = /\.(png|jpe?g|webp)$/i;
  var TERMINAL = {completed: true, failed: true, cancelled: true};
  var STATUS_TEXT = {queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消'};
  var MODE_META = {
    upscale: {
      title: 'AI 放大',
      lead: '对已有图片做超分辨率放大，输出为新文件，不覆盖原图。',
      badge: '放大'
    },
    outpaint: {
      title: 'AI 扩图',
      lead: '使用 Qwen Image 2.1 Outpaint LoRA v2 延伸画面，默认逐像素保留原图。',
      badge: '扩图'
    }
  };

  function node(tag, className, value) {
    var n = document.createElement(tag);
    if (className) n.className = className;
    if (value !== undefined && value !== null) n.textContent = String(value);
    return n;
  }

  function clearNode(target) {
    while (target.firstChild) target.removeChild(target.firstChild);
  }

  function str(value) {
    return value === null || value === undefined ? '' : String(value);
  }

  function fmtBytes(bytes) {
    var n = Number(bytes);
    if (!Number.isFinite(n) || n < 0) return '未知大小';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(2) + ' MB';
  }

  function fmtDims(width, height) {
    var w = Number(width), h = Number(height);
    if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return '尺寸未知';
    return Math.round(w) + ' × ' + Math.round(h);
  }

  function fmtRatio(value) {
    var pct = Number(value) * 100;
    if (!Number.isFinite(pct) || pct < 0) return '未知比例';
    if (pct === 0) return '0%';
    if (pct >= 10) return pct.toFixed(0) + '%';
    if (pct >= 1) return pct.toFixed(1) + '%';
    return pct.toFixed(2) + '%';
  }

  function fmtTime(seconds) {
    var n = Number(seconds);
    if (!Number.isFinite(n) || n <= 0) return '';
    try {
      return new Date(n * 1000).toLocaleString('zh-CN', {hour12: false});
    } catch (error) {
      return '';
    }
  }

  // 预览可接受 /api/ 相对路径；mock 页面用 data:image/... 合成图。
  // 拒绝 http(s)、协议相对地址、反斜杠和任何其它协议，避免模块被用来指向外部站点。
  function safePreviewUrl(url) {
    if (typeof url !== 'string' || !url) return null;
    if (url.slice(0, 5) === 'data:') return url.slice(0, 11) === 'data:image/' ? url : null;
    if (url.slice(0, 5) !== '/api/') return null;
    if (url.indexOf('\\') !== -1 || url.slice(0, 6) === '/api//') return null;
    return url;
  }

  // 下载链接只允许本机 /api/ 相对路径。
  function safeDownloadUrl(url) {
    if (typeof url !== 'string' || !url) return null;
    if (url.slice(0, 5) !== '/api/') return null;
    if (url.indexOf('\\') !== -1 || url.slice(0, 6) === '/api//') return null;
    return url;
  }

  function isNumeric(value) {
    return typeof value === 'number' && Number.isFinite(value);
  }

  function toInt(value, fallback) {
    var n = Math.round(Number(value));
    return Number.isFinite(n) ? n : fallback;
  }

  function formatClock(job) {
    var parts = [];
    if (fmtTime(job && job.created)) parts.push('创建 ' + fmtTime(job.created));
    if (fmtTime(job && job.started)) parts.push('开始 ' + fmtTime(job.started));
    if (fmtTime(job && job.finished)) parts.push('结束 ' + fmtTime(job.finished));
    return parts.join(' · ');
  }

  function mount(options) {
    var opts = options || {};
    if (!opts.root || opts.root.nodeType !== 1) throw new Error('mount 需要一个 DOM 容器作为 root');
    if (opts.mode !== 'upscale' && opts.mode !== 'outpaint') throw new Error('mode 必须是 upscale 或 outpaint');
    if (typeof opts.api !== 'function') throw new Error('mount 需要注入 api(path, {method, body}) 函数');
    var panel = new ImageTools(opts);
    panel.start();
    return {
      refresh: function () { return panel.refresh(); },
      destroy: function () { panel.destroy(); }
    };
  }

  function ImageTools(options) {
    this.root = options.root;
    this.mode = options.mode;
    this.api = options.api;
    this.notify = typeof options.notify === 'function' ? options.notify : function () {};
    this.uid = ++UID;

    this.destroyed = false;
    this.started = false;
    this.caps = null;
    this.paramsReady = false;
    this.paramsDirty = false;

    this.input = null;
    this.objectUrl = null;

    this.previewSeq = 0;
    this.previewKey = null;
    this.previewData = null;
    this.previewPending = false;

    this.job = null;
    this.jobs = [];
    this.submitting = false;
    this.cancelling = false;
    this.savingDefaults = false;

    this.pollTimer = null;
    this.polling = false;
    this.previewTimer = null;
    this.pollError = null;

    this.galleryOpen = false;
    this.galleryBusy = false;

    this.ui = {};
  }

  ImageTools.prototype.start = function () {
    if (this.started || this.destroyed) return;
    this.started = true;
    this.build();
    this.bind();
    this.renderAll();
    var self = this;
    this.refresh().catch(function (error) {
      if (!self.destroyed) self.reportError(error && error.message ? error.message : '加载失败');
    });
  };

  /* ------------------------------------------------------------------ 构建 */

  ImageTools.prototype.build = function () {
    var self = this;
    var meta = MODE_META[this.mode];
    var root = this.root;

    root.classList.add('image-tools');
    root.classList.add('it-mode-' + this.mode);
    root.setAttribute('data-mode', this.mode);

    var header = node('header', 'it-head');
    var headText = node('div', 'it-head-text');
    headText.appendChild(node('h2', 'it-title', meta.title));
    headText.appendChild(node('p', 'it-lead', meta.lead));
    header.appendChild(headText);
    var badgeWrap = node('div', 'it-head-badge');
    this.ui.badge = node('span', 'it-badge', '能力待加载');
    badgeWrap.appendChild(this.ui.badge);
    header.appendChild(badgeWrap);
    root.appendChild(header);
    root.appendChild(this.buildModelPanel());

    var grid = node('div', 'it-grid');
    var left = node('div', 'it-column it-left');
    var right = node('div', 'it-column it-right');
    grid.appendChild(left);
    grid.appendChild(right);
    root.appendChild(grid);

    left.appendChild(this.buildInputPanel());
    left.appendChild(this.buildParamsPanel());
    left.appendChild(this.buildActionsPanel());
    right.appendChild(this.buildPreviewPanel());
    right.appendChild(this.buildJobPanel());
    right.appendChild(this.buildResultPanel());

    this.ui.note = node('p', 'it-note');
    this.ui.note.setAttribute('role', 'status');
    this.ui.note.setAttribute('aria-live', 'polite');
    root.appendChild(this.ui.note);
  };

  ImageTools.prototype.fieldId = function (name) {
    return 'it-' + this.mode + '-' + this.uid + '-' + name;
  };

  ImageTools.prototype.buildModelPanel = function () {
    var self = this;
    var panel = node('section', 'it-panel');
    panel.appendChild(node('h3', 'it-panel-title', '模型 · 加载与释放'));
    this.ui.modelState = node('p', 'it-hint', '正在读取模型状态…');
    panel.appendChild(this.ui.modelState);
    panel.appendChild(node('p', 'it-hint', this.mode === 'upscale'
      ? '放大模型可独立常驻。资源足够时允许与 Qwen 同时加载，任务排队执行。'
      : '扩图与生图共用 Qwen 模型。释放此模型也会释放生图组件。'));
    var row = node('div', 'it-btn-row');
    [['modelLoad', '加载模型', 'model-load'], ['modelRelease', '释放模型', 'model-release'],
      ['resourceCancel', '取消本次操作', 'resource-cancel']].forEach(function (entry) {
      var button = node('button', 'it-btn it-ghost', entry[1]);
      button.type = 'button'; button.setAttribute('data-role', entry[2]);
      self.ui[entry[0]] = button; row.appendChild(button);
    });
    this.ui.resourceCancel.hidden = true;
    this.ui.modelLoad.addEventListener('click', function () { self.modelAction('load'); });
    this.ui.modelRelease.addEventListener('click', function () { self.modelAction('release'); });
    this.ui.resourceCancel.addEventListener('click', function () {
      self.ui.resourceMessage.textContent = '本次操作已取消，参数已保留。';
      self.ui.resourceCancel.hidden = true;
      self.clearError();
    });
    panel.appendChild(row);
    this.ui.resourceMessage = node('p', 'it-hint');
    this.ui.resourceMessage.setAttribute('aria-live', 'polite');
    this.ui.resourceMessage.setAttribute('data-role', 'resource-message');
    panel.appendChild(this.ui.resourceMessage);
    return panel;
  };

  ImageTools.prototype.checkResources = function (loading, params, inputId) {
    var self = this;
    return this.api('/image-tools/resources/' + this.mode, {method: 'POST',
      body: {params: params, input_id: loading ? null : inputId, loading: loading}}).then(function (result) {
      if (self.destroyed) throw new Error('页面已关闭，本次操作未提交');
      if (!result || result.allowed !== true) {
        var reason = result && result.reason ? result.reason : '无法确认资源是否足够';
        self.ui.resourceMessage.textContent = '未加载模型、未提交任务：' + reason;
        self.ui.resourceCancel.hidden = false;
        throw new Error('资源检查未通过：' + reason);
      }
      self.ui.resourceMessage.textContent = result.warning
        ? '资源提示：' + result.warning + '。仍会尝试生成，失败或超时按任务失败处理。'
        : '资源检查通过；执行时会再次检查。';
      self.ui.resourceCancel.hidden = true;
      return result;
    });
  };

  ImageTools.prototype.modelAction = function (action) {
    var self = this;
    if (this.destroyed || this.modelActionPending) return;
    var params = this.readParams();
    this.modelActionPending = true;
    this.renderModelState();
    var before = action === 'load' ? this.checkResources(true, params) : Promise.resolve();
    before.then(function () {
      return self.api('/image-tools/models/' + self.mode + '/' + action, {method: 'POST', body: {params: params}});
    }).then(function () {
      if (self.destroyed) return;
      self.notify(action === 'load' ? '已开始加载模型' : '模型已释放');
      return self.refresh();
    }).catch(function (error) {
      if (!self.destroyed) { self.reportError(error.message); self.notify(error.message); }
    }).finally(function () {
      self.modelActionPending = false;
      if (!self.destroyed) { self.renderModelState(); self.updateActions(); }
    });
  };

  ImageTools.prototype.renderModelState = function () {
    if (!this.ui.modelState) return;
    var state = this.modelStates && this.modelStates[this.mode];
    var loading = state && state.state === 'loading';
    var running = state && state.state === 'running';
    var cap = this.capability();
    this.ui.modelState.textContent = !state ? '模型状态尚未获取' : loading ? '模型加载中…'
      : running ? '放大任务运行中' : state.loaded ? '模型已加载，可复用'
      : state.context_loaded ? 'Qwen 上下文已创建，组件尚未全部加载；任务会按需加载' : '模型未加载；开始任务时会检查资源并自动加载';
    if (state && state.error) this.ui.modelState.textContent += ' · ' + state.error;
    this.ui.modelLoad.disabled = !state || !cap || !cap.verified || this.modelActionPending || loading || running || this.isActive() || this.submitting;
    this.ui.modelRelease.disabled = !state || this.modelActionPending || running || this.isActive() || this.submitting || !(state.pid || state.loaded || state.context_loaded || loading);
    this.ui.modelRelease.textContent = loading ? '取消加载并释放' : '释放模型';
  };

  ImageTools.prototype.buildInputPanel = function () {
    var self = this;
    var panel = node('section', 'it-panel');
    var head = node('div', 'it-panel-head');
    head.appendChild(node('h3', 'it-panel-title', '1 · 选择图片'));
    this.ui.inputState = node('span', 'it-mini', '未选择');
    head.appendChild(this.ui.inputState);
    panel.appendChild(head);

    var row = node('div', 'it-btn-row');
    this.ui.pickButton = node('button', 'it-btn', '选择文件');
    this.ui.pickButton.type = 'button';
    this.ui.pickButton.setAttribute('data-role', 'pick-file');
    this.ui.galleryButton = node('button', 'it-btn it-ghost', '从图库选择');
    this.ui.galleryButton.type = 'button';
    this.ui.galleryButton.setAttribute('aria-expanded', 'false');
    this.ui.galleryButton.setAttribute('data-role', 'open-gallery');
    row.appendChild(this.ui.pickButton);
    row.appendChild(this.ui.galleryButton);
    panel.appendChild(row);

    this.ui.fileInput = node('input', 'it-file-input');
    this.ui.fileInput.type = 'file';
    this.ui.fileInput.accept = 'image/png,image/jpeg,image/webp';
    this.ui.fileInput.setAttribute('aria-hidden', 'true');
    this.ui.fileInput.tabIndex = -1;
    panel.appendChild(this.ui.fileInput);

    this.ui.inputHint = node('p', 'it-hint', 'PNG / JPEG / WebP · 初版只接受 8 bit 图片');
    this.ui.inputHint.setAttribute('data-role', 'input-hint');
    panel.appendChild(this.ui.inputHint);

    // 已选图片卡片
    var card = node('div', 'it-input-card');
    card.hidden = true;
    var thumbWrap = node('div', 'it-input-thumb');
    this.ui.inputThumb = node('img', 'it-thumb-img');
    this.ui.inputThumb.alt = '';
    this.ui.inputThumb.decoding = 'async';
    thumbWrap.appendChild(this.ui.inputThumb);
    this.ui.inputThumbEmpty = node('span', 'it-thumb-empty', '无预览');
    thumbWrap.appendChild(this.ui.inputThumbEmpty);
    card.appendChild(thumbWrap);

    var info = node('div', 'it-input-info');
    this.ui.inputName = node('strong', 'it-input-name', '');
    this.ui.inputMeta = node('span', 'it-input-meta', '');
    this.ui.inputBits = node('span', 'it-input-bits', '');
    info.appendChild(this.ui.inputName);
    info.appendChild(this.ui.inputMeta);
    info.appendChild(this.ui.inputBits);
    var cardActions = node('div', 'it-btn-row it-tight');
    this.ui.clearInputButton = node('button', 'it-btn it-ghost it-small', '清空');
    this.ui.clearInputButton.type = 'button';
    this.ui.clearInputButton.setAttribute('data-role', 'clear-input');
    cardActions.appendChild(this.ui.clearInputButton);
    info.appendChild(cardActions);
    card.appendChild(info);
    this.ui.inputCard = card;
    panel.appendChild(card);

    // 图库选择器
    var gallery = node('div', 'it-gallery');
    gallery.hidden = true;
    var galleryHead = node('div', 'it-gallery-head');
    galleryHead.appendChild(node('span', 'it-mini', '仅显示未删除的普通图库图片'));
    this.ui.galleryClose = node('button', 'it-btn it-ghost it-small', '收起');
    this.ui.galleryClose.type = 'button';
    galleryHead.appendChild(this.ui.galleryClose);
    gallery.appendChild(galleryHead);
    this.ui.galleryList = node('div', 'it-gallery-list');
    gallery.appendChild(this.ui.galleryList);
    this.ui.gallery = gallery;
    panel.appendChild(gallery);

    return panel;
  };

  ImageTools.prototype.buildParamsPanel = function () {
    var panel = node('section', 'it-panel');
    var head = node('div', 'it-panel-head');
    head.appendChild(node('h3', 'it-panel-title', '2 · 参数'));
    head.appendChild(node('span', 'it-mini', '初版输出 8 bit PNG'));
    panel.appendChild(head);

    this.ui.params = node('div', 'it-params');
    panel.appendChild(this.ui.params);
    if (this.mode === 'upscale') this.buildUpscaleParams();
    else this.buildOutpaintParams();

    panel.appendChild(node(
      'p',
      'it-warn',
      '本工作流初版固定输出 8 bit PNG；不支持对 16 bit 原图做无损放大或扩图。'
    ));
    return panel;
  };

  ImageTools.prototype.buildUpscaleParams = function () {
    var modelRow = this.field('模型', 'model_id');
    this.ui.model = node('select', 'it-control');
    this.ui.model.id = this.fieldId('model');
    this.ui.model.disabled = true;
    modelRow.appendChild(this.ui.model);
    modelRow.appendChild(node('span', 'it-hint', '只列出本机已验证可用的放大模型。'));
    this.ui.params.appendChild(modelRow);

    var grid = node('div', 'it-two');
    var tileHelp = '把图片切成多大的小块分别放大。显存不足或报错时调小；越大通常越快，但更吃显存。范围 64–384，且必须是 32 的倍数；本机 tile 512 会因显存不足失败。';
    var tile = this.field('分块大小 tile size', 'tile', tileHelp);
    this.ui.tileSize = node('input', 'it-control');
    this.ui.tileSize.type = 'number';
    this.ui.tileSize.min = '64';
    this.ui.tileSize.max = '384';
    this.ui.tileSize.step = '32';
    this.ui.tileSize.inputMode = 'numeric';
    this.ui.tileSize.id = this.fieldId('tile');
    this.ui.tileSize.title = tileHelp;
    tile.appendChild(this.ui.tileSize);
    grid.appendChild(tile);

    var repeatsHelp = '1 次为基础 4×；2 次为再次放大到 16×，伪影风险高，像素与资源占用大增。再次放大不是 AI 强化。';
    var repeats = this.field('重复次数 repeats', 'repeats', repeatsHelp);
    this.ui.repeats = node('input', 'it-control');
    this.ui.repeats.type = 'number';
    this.ui.repeats.min = '1';
    this.ui.repeats.max = '2';
    this.ui.repeats.step = '1';
    this.ui.repeats.inputMode = 'numeric';
    this.ui.repeats.id = this.fieldId('repeats');
    this.ui.repeats.title = repeatsHelp;
    repeats.appendChild(this.ui.repeats);
    grid.appendChild(repeats);
    this.ui.params.appendChild(grid);
    this.ui.enhanceButton = node('button', 'it-btn', 'AI强化 · 待本机验证');
    this.ui.enhanceButton.type = 'button';
    this.ui.enhanceButton.disabled = true;
    this.ui.enhanceButton.setAttribute('data-role', 'enhance');
    this.ui.enhanceButton.title = 'VOSR 2.0 / SeedVR2 7B Sharp Q4_K_M 尚未完成本机推理验证';
    this.ui.params.appendChild(this.ui.enhanceButton);
    this.ui.params.appendChild(node('p', 'it-hint', 'AI强化尚不可用：候选模型未完成本机加载、性能与结构保真验证。基础放大仍可使用；不会用普通锐化代替 AI 推理。'));
    this.ui.params.appendChild(node(
      'p',
      'it-hint',
      '放大不设固定输出上限，按当前内存、显存和磁盘预算放行；8K UHD（7680×4320）实测约 29 秒、额外显存约 0.6 GB。输入最大 1600 万像素 / 25 MiB。tile 512 会因显存不足失败，建议 128/256。'
    ));
    this.ui.params.appendChild(node(
      'p',
      'it-hint',
      '资源足够时可保留已加载的 Qwen。加载和执行前检查内存、显存与显卡负载；资源不足会提示取消，不自动卸载其他模型。'
    ));

    var out = node('div', 'it-static');
    out.appendChild(node('span', 'it-static-key', '输出格式'));
    out.appendChild(node('span', 'it-static-value', 'PNG · 8 bit'));
    this.ui.params.appendChild(out);

    this.ui.sampler = null;
    this.ui.scheduler = null;
  };

  ImageTools.prototype.buildOutpaintParams = function () {
    var self = this;
    var extendGrid = node('div', 'it-four');
    var sides = [
      ['extend_left', '左'],
      ['extend_top', '上'],
      ['extend_right', '右'],
      ['extend_bottom', '下']
    ];
    this.ui.extends = {};
    sides.forEach(function (entry) {
      var wrap = self.field('扩展' + entry[1] + '（像素）');
      var input = node('input', 'it-control');
      input.type = 'number';
      input.min = '0';
      input.max = '4096';
      input.step = '8';
      input.inputMode = 'numeric';
      input.id = self.fieldId(entry[0]);
      wrap.appendChild(input);
      extendGrid.appendChild(wrap);
      self.ui.extends[entry[0]] = input;
    });
    this.ui.params.appendChild(extendGrid);

    var prompt = this.field('提示词');
    this.ui.prompt = node('textarea', 'it-control it-textarea');
    this.ui.prompt.id = this.fieldId('prompt');
    this.ui.prompt.rows = 4;
    this.ui.prompt.placeholder = '描述新增区域应出现的内容，例如「延伸两侧的建筑与天空，保持主体不变」';
    prompt.appendChild(this.ui.prompt);
    prompt.appendChild(node('span', 'it-hint', '必填；提示词只描述新增区域，不改变原图内容。'));
    this.ui.params.appendChild(prompt);

    var details = node('details', 'it-details');
    details.appendChild(node('summary', 'it-summary', '反向提示词（可选）'));
    this.ui.negative = node('textarea', 'it-control it-textarea');
    this.ui.negative.id = this.fieldId('negative');
    this.ui.negative.rows = 2;
    details.appendChild(this.ui.negative);
    this.ui.params.appendChild(details);

    var grid = node('div', 'it-three');
    var steps = this.field('步数');
    this.ui.steps = node('input', 'it-control');
    this.ui.steps.type = 'number';
    this.ui.steps.min = '1';
    this.ui.steps.max = '100';
    this.ui.steps.step = '1';
    this.ui.steps.inputMode = 'numeric';
    this.ui.steps.id = this.fieldId('steps');
    steps.appendChild(this.ui.steps);
    grid.appendChild(steps);

    var cfg = this.field('CFG');
    this.ui.cfg = node('input', 'it-control');
    this.ui.cfg.type = 'number';
    this.ui.cfg.min = '0';
    this.ui.cfg.max = '30';
    this.ui.cfg.step = '0.1';
    this.ui.cfg.inputMode = 'decimal';
    this.ui.cfg.id = this.fieldId('cfg');
    cfg.appendChild(this.ui.cfg);
    grid.appendChild(cfg);

    var seed = this.field('种子');
    this.ui.seed = node('input', 'it-control');
    this.ui.seed.type = 'number';
    this.ui.seed.step = '1';
    this.ui.seed.inputMode = 'numeric';
    this.ui.seed.id = this.fieldId('seed');
    seed.appendChild(this.ui.seed);
    grid.appendChild(seed);
    this.ui.params.appendChild(grid);

    var keep = node('label', 'it-check');
    this.ui.keepOriginal = node('input');
    this.ui.keepOriginal.type = 'checkbox';
    this.ui.keepOriginal.checked = true;
    this.ui.keepOriginal.disabled = true;
    this.ui.keepOriginal.id = this.fieldId('keep');
    keep.appendChild(this.ui.keepOriginal);
    var keepText = node('span');
    keepText.appendChild(node('strong', null, '保护原图'));
    keepText.appendChild(node('small', null, '严格模式保护全部像素；无缝融合仅允许过渡带变化，核心像素不变。'));
    keep.appendChild(keepText);
    this.ui.params.appendChild(keep);
    var seam = this.field('接缝模式');
    this.ui.seamMode = node('select', 'it-control');
    this.ui.seamMode.id = this.fieldId('seam_mode');
    [['strict','严格保留（默认）'],['feather','无缝融合（实验性，需检查）']].forEach(function (entry) {
      var option = node('option', null, entry[1]); option.value = entry[0]; self.ui.seamMode.appendChild(option);
    });
    seam.appendChild(this.ui.seamMode);
    this.ui.params.appendChild(seam);
    var feather = this.field('过渡带宽度');
    this.ui.featherPx = node('select', 'it-control');
    this.ui.featherPx.id = this.fieldId('feather_px');
    [16,32,48].forEach(function (n) { var option = node('option',null,n+' px'); option.value=String(n); self.ui.featherPx.appendChild(option); });
    feather.appendChild(this.ui.featherPx);
    this.ui.params.appendChild(feather);
    var tone = node('label','it-check');
    this.ui.toneMatch = node('input'); this.ui.toneMatch.type='checkbox'; this.ui.toneMatch.checked=true;
    this.ui.toneMatch.id = this.fieldId('tone_match');
    tone.appendChild(this.ui.toneMatch); tone.appendChild(node('span',null,'局部色调匹配（仅无缝融合；异常时自动跳过）'));
    this.ui.params.appendChild(tone);
    this.ui.params.appendChild(node(
      'p',
      'it-hint',
      '资源检查只作提示：内存/显存估计不足时仍可生成，失败或超时按任务失败处理；电池、模型缺失与显卡占用检查仍会阻止任务。'
    ));

    var formats = node('div', 'it-static');
    formats.appendChild(node('span', 'it-static-key', '输出格式'));
    formats.appendChild(node('span', 'it-static-value', 'PNG · 8 bit'));
    this.ui.params.appendChild(formats);
    this.ui.params.appendChild(node(
      'p',
      'it-hint',
      '最大画布 4096 单边 / 1600 万像素；实测安全范围约 1984×1120（2.2 MP）。4K（3840×2160）会因系统内存不足失败，仍可提交但预计失败。'
    ));

    this.ui.model = null;
    this.ui.tileSize = null;
    this.ui.repeats = null;
  };

  ImageTools.prototype.field = function (labelText, name, help) {
    var wrap = node('label', 'it-field');
    if (name === 'repeats') wrap.classList.add('it-tip-right');
    var label = node('span', 'it-label', labelText);
    if (help) {
      var marker = node('span', 'it-help', '?');
      marker.tabIndex = 0;
      marker.setAttribute('role', 'note');
      marker.setAttribute('aria-label', help);
      marker.appendChild(node('span', 'it-tip', help));
      label.appendChild(marker);
    }
    wrap.appendChild(label);
    return wrap;
  };

  ImageTools.prototype.buildActionsPanel = function () {
    var panel = node('section', 'it-panel it-actions');
    var head = node('div', 'it-panel-head');
    head.appendChild(node('h3', 'it-panel-title', '3 · 执行'));
    panel.appendChild(head);

    this.ui.confirmRow = null;
    if (this.mode === 'outpaint') {
      var confirmLabel = node('label', 'it-check');
      this.ui.confirm = node('input');
      this.ui.confirm.type = 'checkbox';
      this.ui.confirm.id = this.fieldId('confirm');
      confirmLabel.appendChild(this.ui.confirm);
      var confirmText = node('span');
      // 文案与 web/index.html 的 #content_confirmed 完全一致。
      confirmText.appendChild(node('strong', null, '遵守内容约定：不生成极端血腥或用于欺骗他人的内容。'));
      confirmText.appendChild(node('small', null, '扩图沿用现有内容确认，不增加新的内容规则。'));
      confirmLabel.appendChild(confirmText);
      panel.appendChild(confirmLabel);
      this.ui.confirmRow = confirmLabel;
    } else {
      this.ui.confirm = null;
    }

    var row = node('div', 'it-btn-row');
    this.ui.startButton = node('button', 'it-btn it-primary', this.mode === 'upscale' ? '开始放大' : '开始扩图');
    this.ui.startButton.type = 'button';
    this.ui.startButton.setAttribute('data-role', 'start-job');
    row.appendChild(this.ui.startButton);
    this.ui.cancelButton = node('button', 'it-btn it-ghost', '取消任务');
    this.ui.cancelButton.type = 'button';
    this.ui.cancelButton.hidden = true;
    this.ui.cancelButton.setAttribute('data-role', 'cancel-job');
    row.appendChild(this.ui.cancelButton);
    panel.appendChild(row);

    var row2 = node('div', 'it-btn-row it-tight');
    this.ui.saveDefaultsButton = node('button', 'it-btn it-ghost it-small', '保存默认参数');
    this.ui.saveDefaultsButton.type = 'button';
    this.ui.saveDefaultsButton.setAttribute('data-role', 'save-defaults');
    this.ui.reloadDefaultsButton = node('button', 'it-btn it-ghost it-small', '重新载入默认参数');
    this.ui.reloadDefaultsButton.type = 'button';
    this.ui.reloadDefaultsButton.setAttribute('data-role', 'reload-defaults');
    row2.appendChild(this.ui.saveDefaultsButton);
    row2.appendChild(this.ui.reloadDefaultsButton);
    panel.appendChild(row2);

    this.ui.blocked = node('p', 'it-blocked');
    this.ui.blocked.setAttribute('role', 'status');
    this.ui.blocked.setAttribute('aria-live', 'polite');
    panel.appendChild(this.ui.blocked);
    this.ui.estimateWarn = node('p', 'it-warn it-estimate-warn');
    this.ui.estimateWarn.hidden = true;
    panel.appendChild(this.ui.estimateWarn);
    panel.appendChild(node('p', 'it-hint', '保存 / 载入默认参数只作用于参数，不会提交任务。'));
    return panel;
  };

  ImageTools.prototype.buildPreviewPanel = function () {
    var panel = node('section', 'it-panel it-preview-panel');
    var head = node('div', 'it-panel-head');
    head.appendChild(node('h3', 'it-panel-title', this.mode === 'upscale' ? '预计输出' : '画布预览'));
    this.ui.previewState = node('span', 'it-mini', '等待图片');
    head.appendChild(this.ui.previewState);
    panel.appendChild(head);

    if (this.mode === 'upscale') {
      this.ui.estimate = node('p', 'it-estimate', '选择图片和模型后显示预计尺寸。');
      panel.appendChild(this.ui.estimate);
      this.ui.estimateNote = node('p', 'it-hint', '按服务端模型倍率与重复次数估算，标注为估算值；实际尺寸以任务结果为准。');
      panel.appendChild(this.ui.estimateNote);
    } else {
      this.ui.geometry = node('div', 'it-geometry');
      this.ui.canvasBox = node('div', 'it-canvas-box');
      this.ui.originalBox = node('div', 'it-original-box');
      this.ui.originalBox.appendChild(node('span', 'it-original-label', '原图'));
      this.ui.canvasBox.appendChild(this.ui.originalBox);
      this.ui.geometry.appendChild(this.ui.canvasBox);
      panel.appendChild(this.ui.geometry);
      this.ui.geometryText = node('p', 'it-estimate', '选择图片并填写扩展量后，由服务端计算目标画布。');
      panel.appendChild(this.ui.geometryText);
      this.ui.geometryNote = node('p', 'it-hint', '最终宽高与偏移以服务端预览结果为准；参数修改后需等待新一轮预览才能提交。');
      panel.appendChild(this.ui.geometryNote);
    }
    return panel;
  };

  ImageTools.prototype.buildJobPanel = function () {
    var panel = node('section', 'it-panel it-job-panel');
    var head = node('div', 'it-panel-head');
    head.appendChild(node('h3', 'it-panel-title', '任务状态'));
    this.ui.jobBadge = node('span', 'it-badge it-badge-idle', '未提交');
    this.ui.jobBadge.setAttribute('role', 'status');
    this.ui.jobBadge.setAttribute('aria-live', 'polite');
    head.appendChild(this.ui.jobBadge);
    panel.appendChild(head);

    this.ui.jobCard = node('div', 'it-job');
    this.ui.jobStage = node('p', 'it-job-stage', '尚未提交任务。');
    this.ui.jobCard.appendChild(this.ui.jobStage);
    this.ui.progressWrap = node('div', 'it-progress');
    this.ui.progressBar = node('div', 'it-progress-bar');
    this.ui.progressWrap.appendChild(this.ui.progressBar);
    this.ui.jobCard.appendChild(this.ui.progressWrap);
    this.ui.jobProgress = node('span', 'it-mini', '');
    this.ui.jobCard.appendChild(this.ui.jobProgress);
    this.ui.jobTimes = node('p', 'it-mini', '');
    this.ui.jobCard.appendChild(this.ui.jobTimes);
    this.ui.jobError = node('p', 'it-error');
    this.ui.jobError.hidden = true;
    this.ui.jobCard.appendChild(this.ui.jobError);
    panel.appendChild(this.ui.jobCard);

    var historyHead = node('div', 'it-panel-head it-history-head');
    historyHead.appendChild(node('h3', 'it-panel-title it-subtitle', '最近任务'));
    this.ui.historyRefresh = node('button', 'it-btn it-ghost it-small', '刷新');
    this.ui.historyRefresh.type = 'button';
    this.ui.historyRefresh.setAttribute('data-role', 'refresh-history');
    historyHead.appendChild(this.ui.historyRefresh);
    panel.appendChild(historyHead);

    this.ui.history = node('div', 'it-history');
    panel.appendChild(this.ui.history);
    return panel;
  };

  ImageTools.prototype.buildResultPanel = function () {
    var panel = node('section', 'it-panel it-result');
    panel.hidden = true;
    var head = node('div', 'it-panel-head');
    head.appendChild(node('h3', 'it-panel-title', '结果'));
    this.ui.resultState = node('span', 'it-badge it-badge-ok', '已完成');
    head.appendChild(this.ui.resultState);
    panel.appendChild(head);

    var compare = node('div', 'it-compare');
    var inputSide = node('figure', 'it-figure');
    var inputImgWrap = node('div', 'it-figure-img');
    this.ui.resultInputImg = node('img');
    this.ui.resultInputImg.alt = '输入图片';
    inputImgWrap.appendChild(this.ui.resultInputImg);
    inputSide.appendChild(inputImgWrap);
    inputSide.appendChild(node('figcaption', 'it-figcaption', '输入'));
    compare.appendChild(inputSide);

    var outputSide = node('figure', 'it-figure');
    var outputImgWrap = node('div', 'it-figure-img');
    this.ui.resultOutputImg = node('img');
    this.ui.resultOutputImg.alt = '输出图片';
    outputImgWrap.appendChild(this.ui.resultOutputImg);
    outputSide.appendChild(outputImgWrap);
    outputSide.appendChild(node('figcaption', 'it-figcaption', '输出'));
    compare.appendChild(outputSide);
    panel.appendChild(compare);

    this.ui.resultMeta = node('p', 'it-estimate');
    panel.appendChild(this.ui.resultMeta);
    this.ui.preservation = node('p', 'it-preservation');
    panel.appendChild(this.ui.preservation);
    this.ui.seamWarning = node('p', 'it-warn');
    panel.appendChild(this.ui.seamWarning);
    this.ui.strictDownload = node('a', 'it-btn it-ghost', '使用严格保留结果');
    this.ui.comparisonDownload = node('a', 'it-btn it-ghost', '查看对照（左严格 / 右融合）');
    this.ui.strictDownload.hidden = this.ui.comparisonDownload.hidden = true;

    var downloads = node('div', 'it-btn-row it-tight');
    this.ui.downloadOriginal = node('a', 'it-btn it-ghost it-small', '下载原图');
    this.ui.downloadOutput = node('a', 'it-btn it-small', '下载输出图');
    this.ui.downloadManifest = node('a', 'it-btn it-ghost it-small', '下载 run.json');
    [this.ui.downloadOriginal, this.ui.downloadOutput, this.ui.downloadManifest].forEach(function (link) {
      link.setAttribute('download', '');
    });
    downloads.appendChild(this.ui.downloadOriginal);
    downloads.appendChild(this.ui.downloadOutput);
    downloads.appendChild(this.ui.downloadManifest);
    panel.appendChild(downloads);
    panel.appendChild(this.ui.strictDownload); panel.appendChild(this.ui.comparisonDownload);

    this.ui.result = panel;
    return panel;
  };

  /* ------------------------------------------------------------------ 事件 */

  ImageTools.prototype.bind = function () {
    var self = this;

    this.ui.pickButton.addEventListener('click', function () {
      if (self.destroyed) return;
      self.ui.fileInput.click();
    });

    this.ui.fileInput.addEventListener('change', function () {
      var file = self.ui.fileInput.files && self.ui.fileInput.files[0];
      self.ui.fileInput.value = '';
      if (!file || self.destroyed) return;
      self.handleFile(file);
    });

    this.ui.galleryButton.addEventListener('click', function () {
      self.toggleGallery();
    });

    this.ui.galleryClose.addEventListener('click', function () {
      self.closeGallery();
    });

    this.ui.clearInputButton.addEventListener('click', function () {
      self.clearInput();
      self.notify('已清空已选图片');
    });

    this.ui.galleryList.addEventListener('click', function (event) {
      var target = event.target instanceof Element ? event.target.closest('[data-image-id]') : null;
      if (!target || self.destroyed) return;
      self.pickFromGallery(target.getAttribute('data-image-id'));
    });

    this.ui.params.addEventListener('input', function () {
      self.onParamsChanged();
    });
    this.ui.params.addEventListener('change', function () {
      self.onParamsChanged();
    });

    if (this.ui.confirm) this.ui.confirm.addEventListener('change', function () { self.updateActions(); });

    this.ui.startButton.addEventListener('click', function () { self.submit(); });
    this.ui.cancelButton.addEventListener('click', function () { self.cancel(); });
    this.ui.saveDefaultsButton.addEventListener('click', function () { self.saveDefaults(); });
    this.ui.reloadDefaultsButton.addEventListener('click', function () { self.reloadDefaults(); });
    this.ui.historyRefresh.addEventListener('click', function () {
      self.refresh().catch(function (error) {
        if (!self.destroyed) self.notify(error && error.message ? error.message : '刷新失败');
      });
    });

    // Esc 收起图库面板；监听只挂在 root 内，destroy 时移除。
    this.onKeydown = function (event) {
      if (self.destroyed) return;
      if (event.key === 'Escape' && self.galleryOpen) {
        event.stopPropagation();
        self.closeGallery();
        self.ui.galleryButton.focus();
      }
    };
    this.root.addEventListener('keydown', this.onKeydown);
  };

  ImageTools.prototype.onParamsChanged = function () {
    if (this.destroyed) return;
    this.paramsDirty = true;
    this.previewKey = null;
    if (this.mode === 'outpaint') {
      this.previewData = null;
      if (this.ui.previewState) this.ui.previewState.textContent = '等待预览';
      this.renderGeometry('参数已修改，等待新一轮预览。');
    }
    this.renderEstimate();
    this.updateActions();
    if (this.mode === 'outpaint') this.schedulePreview();
  };

  ImageTools.prototype.schedulePreview = function () {
    var self = this;
    if (this.destroyed) return;
    clearTimeout(this.previewTimer);
    if (!this.input) {
      this.previewPending = false;
      return;
    }
    var params = this.readParams();
    if (!this.geometryPlausible(params)) {
      this.previewPending = false;
      if (this.ui.previewState) this.ui.previewState.textContent = '等待有效扩展量';
      return;
    }
    this.previewPending = true;
    if (this.ui.previewState) this.ui.previewState.textContent = '计算中…';
    this.previewTimer = setTimeout(function () {
      self.requestPreview();
    }, 260);
  };

  ImageTools.prototype.requestPreview = function () {
    var self = this;
    if (this.destroyed || !this.input) return;
    var seq = ++this.previewSeq;
    var params = this.readParams();
    var inputId = this.input.id;
    var key = this.previewSignature(inputId, params);
    this.api('/image-tools/outpaint/preview', {method: 'POST', body: {input_id: inputId, params: params}})
      .then(function (data) {
        // 只接纳最后一次请求的响应；destroy 之后不再更新页面。
        if (self.destroyed || seq !== self.previewSeq) return;
        if (!data || !Number.isFinite(Number(data.width)) || !Number.isFinite(Number(data.height))) {
          self.previewPending = false;
          self.previewKey = null;
          if (self.ui.previewState) self.ui.previewState.textContent = '预览失败';
          self.renderGeometry('服务端未返回有效的画布尺寸。');
          self.updateActions();
          return;
        }
        self.previewPending = false;
        self.previewData = data;
        self.previewKey = key;
        if (self.ui.previewState) self.ui.previewState.textContent = '几何已确认';
        self.renderGeometry();
        self.updateActions();
      })
      .catch(function (error) {
        if (self.destroyed || seq !== self.previewSeq) return;
        self.previewPending = false;
        self.previewKey = null;
        if (self.ui.previewState) self.ui.previewState.textContent = '预览失败';
        self.renderGeometry('画布预览失败：' + (error && error.message ? error.message : '请求失败'));
        self.updateActions();
      });
  };

  /* ------------------------------------------------------------------ 数据 */

  ImageTools.prototype.refresh = function () {
    var self = this;
    if (this.destroyed) return Promise.resolve();
    if (this.refreshInFlight) return this.refreshInFlight;
    if (this.polling) return Promise.resolve();
    var failure = null;
    this.refreshInFlight = this.api('/image-tools/capabilities')
      .then(function (caps) {
        if (self.destroyed) return null;
        self.caps = caps && typeof caps === 'object' ? caps : null;
        return self.api('/image-tools/models').then(function (states) {
          if (!self.destroyed) self.modelStates = states;
        }).catch(function () { self.modelStates = null; }).then(function () {
          return self.api('/image-tools/defaults/' + self.mode);
        }).catch(function (error) {
          failure = failure || error;
          return null;
        });
      })
      .then(function (defaults) {
        if (self.destroyed) return null;
        if (!self.paramsReady && defaults && typeof defaults === 'object') {
          self.applyParams(defaults);
          self.paramsReady = true;
        }
        return self.api('/image-tools/jobs').catch(function (error) {
          failure = failure || error;
          return null;
        });
      })
      .then(function (jobs) {
        if (self.destroyed) return;
        self.acceptJobs(jobs);
        self.renderAll();
        if (self.isActive()) self.startPolling();
        if (failure) throw failure;
      }).catch(function (error) {
        if (!self.destroyed) {
          self.caps = null;
          self.renderCapability();
          self.updateActions();
        }
        throw error;
      }).finally(function () { self.refreshInFlight = null; });
    return this.refreshInFlight;
  };

  // 只认本工作流的任务：接口可能同时返回放大与扩图任务。
  ImageTools.prototype.acceptJobs = function (jobs) {
    if (!Array.isArray(jobs)) return;
    this.jobs = jobs.filter(function (job) {
      return job && job.task_type === this.mode;
    }, this);
    var current = this.job && this.jobs.find(function (job) { return job.id === this.job.id; }, this);
    this.job = current || this.jobs.find(function (job) { return !TERMINAL[job.status]; }) || this.jobs[0] || null;
  };

  ImageTools.prototype.capability = function () {
    if (!this.caps) return null;
    var cap = this.caps[this.mode];
    return cap && typeof cap === 'object' ? cap : null;
  };

  ImageTools.prototype.limits = function () {
    var limits = this.caps && this.caps.limits;
    return limits && typeof limits === 'object' ? limits : {};
  };

  // 每个工作流有独立上限；顶层仍保留输入上限等公共字段。
  ImageTools.prototype.modeLimits = function () {
    var limits = this.limits();
    var own = limits[this.mode];
    var result = {};
    Object.keys(limits).forEach(function (key) { if (key !== 'upscale' && key !== 'outpaint') result[key] = limits[key]; });
    if (own && typeof own === 'object') Object.keys(own).forEach(function (key) { result[key] = own[key]; });
    return result;
  };

  ImageTools.prototype.models = function () {
    var cap = this.capability();
    var list = cap && Array.isArray(cap.models) ? cap.models : [];
    return list.filter(function (m) { return m && m.id && m.verified === true; });
  };

  ImageTools.prototype.applyParams = function (params) {
    if (this.mode === 'upscale') {
      var modelId = typeof params.model_id === 'string' ? params.model_id : '';
      var verified = this.models();
      var allowed = verified.some(function (m) { return m.id === modelId; });
      this.ui.model.value = allowed ? modelId : (verified[0] ? verified[0].id : '');
      this.ui.tileSize.value = String(toInt(params.tile_size, 128));
      this.ui.repeats.value = String(toInt(params.repeats, 1));
    } else {
      var sides = ['extend_left', 'extend_right', 'extend_top', 'extend_bottom'];
      sides.forEach(function (key) {
        var value = toInt(params[key], 0);
        this.ui.extends[key].value = String(Math.max(0, value));
      }, this);
      this.ui.prompt.value = str(params.prompt);
      this.ui.negative.value = str(params.negative_prompt);
      this.ui.steps.value = String(toInt(params.steps, 8));
      this.ui.cfg.value = String(isNumeric(params.cfg) ? params.cfg : 1);
      this.ui.seed.value = String(toInt(params.seed, -1));
      this.ui.keepOriginal.checked = true;
      this.ui.seamMode.value = params.seam_mode === 'feather' ? 'feather' : 'strict';
      this.ui.featherPx.value = String([16,32,48].indexOf(params.feather_px) >= 0 ? params.feather_px : 32);
      this.ui.toneMatch.checked = params.tone_match !== false;
      this.previewKey = null;
      this.previewData = null;
      if (this.ui.previewState) this.ui.previewState.textContent = this.input ? '等待预览' : '等待图片';
      this.renderGeometry(this.input
        ? '参数已重新载入，等待新一轮预览。'
        : '选择图片并填写扩展量后，由服务端计算目标画布。');
      if (this.input) this.schedulePreview();
    }
    this.paramsDirty = false;
    this.renderEstimate();
    this.updateActions();
  };

  ImageTools.prototype.readParams = function () {
    if (this.mode === 'upscale') {
      return {
        model_id: this.ui.model.value || null,
        tile_size: toInt(this.ui.tileSize.value, 128),
        repeats: toInt(this.ui.repeats.value, 1),
        output_format: 'png'
      };
    }
    var cfgValue = Number(this.ui.cfg.value);
    return {
      extend_left: toInt(this.ui.extends.extend_left.value, 0),
      extend_right: toInt(this.ui.extends.extend_right.value, 0),
      extend_top: toInt(this.ui.extends.extend_top.value, 0),
      extend_bottom: toInt(this.ui.extends.extend_bottom.value, 0),
      prompt: this.ui.prompt.value.trim(),
      negative_prompt: this.ui.negative.value,
      steps: toInt(this.ui.steps.value, 8),
      cfg: Number.isFinite(cfgValue) ? cfgValue : 1,
      seed: toInt(this.ui.seed.value, -1),
      keep_original_pixels: true,
      seam_mode: this.ui.seamMode.value,
      feather_px: toInt(this.ui.featherPx.value,32),
      tone_match: this.ui.toneMatch.checked,
      output_format: 'png'
    };
  };

  ImageTools.prototype.previewSignature = function (inputId, params) {
    return JSON.stringify([inputId, params.extend_left, params.extend_right, params.extend_top,
      params.extend_bottom, params.prompt, params.negative_prompt, params.steps, params.cfg, params.seed]);
  };

  ImageTools.prototype.geometryPlausible = function (params) {
    var values = [params.extend_left, params.extend_right, params.extend_top, params.extend_bottom];
    if (values.some(function (v) { return !Number.isFinite(v) || v < 0; })) return false;
    return values.some(function (v) { return v > 0; });
  };

  /* ------------------------------------------------------------------ 图片输入 */

  ImageTools.prototype.handleFile = function (file) {
    var self = this;
    var typeOk = ACCEPTED_MIME.indexOf(file.type) !== -1 || ACCEPTED_NAME.test(file.name || '');
    if (!typeOk) {
      this.failInput('只支持 PNG / JPEG / WebP 图片。');
      return;
    }
    var limit = Number(this.limits().max_input_bytes);
    if (!Number.isFinite(limit)) limit = 26214400;
    if (file.size > limit) {
      this.failInput('图片过大：' + fmtBytes(file.size) + '，上限 ' + fmtBytes(limit) + '。');
      return;
    }
    this.setInputBusy(true);
    var form = new FormData();
    form.append('file', file, file.name || 'upload');
    this.api('/image-tools/inputs', {method: 'POST', body: form})
      .then(function (desc) {
        if (self.destroyed) return;
        if (!desc || !desc.id) throw new Error('服务端未返回输入图片信息。');
        self.applyInput(desc, file);
        self.notify('图片已就绪');
      })
      .catch(function (error) {
        if (self.destroyed) return;
        self.failInput(error && error.message ? error.message : '上传失败');
      })
      .then(function () {
        if (!self.destroyed) self.setInputBusy(false);
      });
  };

  ImageTools.prototype.pickFromGallery = function (imageId) {
    var self = this;
    if (this.destroyed || !imageId || this.galleryBusy) return;
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(imageId)) {
      this.notify('图库图片 ID 不合法。');
      return;
    }
    this.galleryBusy = true;
    this.ui.galleryList.setAttribute('aria-busy', 'true');
    this.api('/image-tools/inputs/from-image', {method: 'POST', body: {image_id: imageId}})
      .then(function (desc) {
        if (self.destroyed) return;
        if (!desc || !desc.id) throw new Error('服务端未返回输入图片信息。');
        self.applyInput(desc, null);
        self.closeGallery();
        self.notify('已从图库选择图片');
      })
      .catch(function (error) {
        if (self.destroyed) return;
        self.failInput(error && error.message ? error.message : '图库图片读取失败');
      })
      .then(function () {
        self.galleryBusy = false;
        if (!self.destroyed) self.ui.galleryList.removeAttribute('aria-busy');
      });
  };

  ImageTools.prototype.applyInput = function (desc, file) {
    this.revokeObjectUrl();
    this.clearError();
    this.input = {
      id: str(desc.id),
      name: str(desc.name) || '未命名图片',
      width: desc.width,
      height: desc.height,
      size: desc.size,
      bit_depth: desc.bit_depth,
      has_transparency: desc.has_transparency === true,
      alpha: desc.alpha && typeof desc.alpha === 'object' ? desc.alpha : null,
      preview_url: desc.preview_url,
      download_url: desc.download_url
    };
    var src = safePreviewUrl(desc.preview_url);
    if (!src && file && global.URL && typeof global.URL.createObjectURL === 'function') {
      src = global.URL.createObjectURL(file);
      this.objectUrl = src;
    }
    this.ui.inputThumb.hidden = false;
    if (src) {
      this.ui.inputThumb.src = src;
      this.ui.inputThumb.hidden = false;
      this.ui.inputThumbEmpty.hidden = true;
    } else {
      this.ui.inputThumb.removeAttribute('src');
      this.ui.inputThumb.hidden = true;
      this.ui.inputThumbEmpty.hidden = false;
    }
    this.ui.inputName.textContent = this.input.name;
    this.ui.inputName.title = this.input.name;
    this.ui.inputMeta.textContent = fmtDims(this.input.width, this.input.height) + ' · ' + fmtBytes(this.input.size);
    var bits = Number(this.input.bit_depth);
    this.ui.inputBits.textContent = Number.isFinite(bits) ? ('位深 ' + bits + ' bit' + (bits > 8 ? '（初版不支持）' : '')) : '位深未记录';
    this.ui.inputBits.classList.toggle('it-warn-text', Number.isFinite(bits) && bits > 8);
    this.ui.inputCard.hidden = false;
    this.ui.inputState.textContent = '已选择';
    this.ui.inputHint.textContent = this.alphaNote() || '换图会清空当前选择并重新预览；任务参数不会被自动改写。';
    this.previewSeq++;
    this.previewKey = null;
    this.previewData = null;
    this.previewPending = false;
    if (this.mode === 'outpaint') {
      this.renderGeometry(this.input ? '等待服务端预览…' : '');
      this.schedulePreview();
    }
    this.renderEstimate();
    this.updateActions();
  };

  ImageTools.prototype.alphaNote = function () {
    var input = this.input;
    if (!input) return '';
    var alpha = input.alpha;
    if (alpha && alpha.policy === 'composite-white') {
      return '含 ' + fmtRatio(alpha.visible_ratio) + ' 明显透明像素，提交时会合成到白色背景。';
    }
    if (alpha && alpha.policy === 'drop-alpha') {
      var ratio = Number(alpha.visible_ratio);
      return '含不可见的 alpha 噪声（' + (ratio > 0 ? '明显透明像素 ' + fmtRatio(ratio) : '无可察觉的透明像素') +
        '），提交时直接丢弃 alpha，RGB 不变。';
    }
    if (input.has_transparency === true) {
      return '含透明通道，提交时会自动转为不透明图（透明区域铺白底）。';
    }
    return '';
  };

  ImageTools.prototype.clearInput = function () {
    this.revokeObjectUrl();
    this.input = null;
    this.previewSeq++;
    this.previewKey = null;
    this.previewData = null;
    this.previewPending = false;
    if (this.ui.inputThumb) {
      this.ui.inputThumb.removeAttribute('src');
      this.ui.inputThumb.hidden = true;
      this.ui.inputThumbEmpty.hidden = false;
    }
    this.ui.inputCard.hidden = true;
    this.ui.inputState.textContent = '未选择';
    this.ui.inputHint.textContent = 'PNG / JPEG / WebP · 初版只接受 8 bit 图片';
    if (this.mode === 'outpaint') {
      if (this.ui.previewState) this.ui.previewState.textContent = '等待图片';
      this.renderGeometry('选择图片并填写扩展量后，由服务端计算目标画布。');
    }
    this.renderEstimate();
    this.updateActions();
  };

  ImageTools.prototype.failInput = function (message) {
    this.clearInput();
    this.reportError(message);
    this.notify(message);
  };

  ImageTools.prototype.revokeObjectUrl = function () {
    if (this.objectUrl && global.URL && typeof global.URL.revokeObjectURL === 'function') {
      try { global.URL.revokeObjectURL(this.objectUrl); } catch (error) { /* 忽略释放失败 */ }
    }
    this.objectUrl = null;
  };

  ImageTools.prototype.setInputBusy = function (busy) {
    this.ui.pickButton.disabled = !!busy;
    this.ui.galleryButton.disabled = !!busy;
    this.ui.pickButton.textContent = busy ? '上传中…' : '选择文件';
  };

  ImageTools.prototype.toggleGallery = function () {
    if (this.destroyed) return;
    if (this.galleryOpen) this.closeGallery();
    else this.openGallery();
  };

  ImageTools.prototype.openGallery = function () {
    var self = this;
    this.galleryOpen = true;
    this.ui.gallery.hidden = false;
    this.ui.galleryButton.setAttribute('aria-expanded', 'true');
    this.ui.galleryList.textContent = '正在读取图库…';
    this.api('/images')
      .then(function (list) {
        if (self.destroyed) return;
        self.renderGalleryList(list);
      })
      .catch(function (error) {
        if (self.destroyed) return;
        clearNode(self.ui.galleryList);
        self.ui.galleryList.appendChild(node('p', 'it-error', error && error.message ? error.message : '图库读取失败'));
      });
  };

  ImageTools.prototype.closeGallery = function () {
    this.galleryOpen = false;
    this.ui.gallery.hidden = true;
    this.ui.galleryButton.setAttribute('aria-expanded', 'false');
  };

  ImageTools.prototype.renderGalleryList = function (list) {
    var self = this;
    clearNode(this.ui.galleryList);
    var images = Array.isArray(list) ? list.filter(function (item) {
      return item && item.id && item.deleted !== true;
    }) : [];
    if (!images.length) {
      this.ui.galleryList.appendChild(node('p', 'it-mini', '图库没有可用图片，请先上传。'));
      return;
    }
    images.forEach(function (image) {
      var id = str(image.id);
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) return;
      var item = node('button', 'it-gallery-item');
      item.type = 'button';
      item.setAttribute('data-image-id', id);
      var thumb = node('img', 'it-gallery-thumb');
      thumb.alt = '';
      thumb.loading = 'lazy';
      thumb.src = '/api/images/' + id + '/file?thumb=true';
      item.appendChild(thumb);
      var info = node('span', 'it-gallery-info');
      info.appendChild(node('strong', null, str(image.name) || '未命名图片'));
      info.appendChild(node('small', null, fmtDims(image.width, image.height) + ' · ' + fmtBytes(image.size)));
      item.appendChild(info);
      self.ui.galleryList.appendChild(item);
    });
  };

  /* ------------------------------------------------------------------ 参数 → 只读展示 */

  ImageTools.prototype.renderEstimate = function () {
    if (this.mode !== 'upscale' || !this.ui.estimate) return;
    var params = this.readParams();
    var model = this.models().filter(function (m) { return m.id === params.model_id; })[0];
    if (!this.input) {
      this.ui.estimate.textContent = '选择图片和模型后显示预计尺寸。';
      return;
    }
    if (!model) {
      this.ui.estimate.textContent = '未选择已验证模型，无法估算输出尺寸。';
      return;
    }
    var scale = Number(model.scale);
    if (!Number.isFinite(scale) || scale <= 0) {
      this.ui.estimate.textContent = '该模型倍率待验证，无法估算输出尺寸。';
      return;
    }
    var width = Number(this.input.width), height = Number(this.input.height);
    if (!Number.isFinite(width) || !Number.isFinite(height)) {
      this.ui.estimate.textContent = '输入尺寸未记录，无法估算输出尺寸。';
      return;
    }
    var factor = Math.pow(scale, Math.max(1, params.repeats));
    var outW = width * factor, outH = height * factor, outPixels = outW * outH;
    var tile = Number(params.tile_size) || 128;
    var vram = tile <= 64 ? 0.35 : tile <= 128 ? 0.6 : tile <= 192 ? 1.0 : tile <= 256 ? 1.8 : tile <= 320 ? 2.9 : 3.9;
    var ram = Math.max(0.4, outPixels * 3 * 4 / 1024 ** 3);
    var seconds = Math.max(1, outPixels / 33177600 * 29);
    var timeText = seconds < 90 ? seconds.toFixed(0) + ' 秒' : (seconds / 60).toFixed(1) + ' 分钟';
    this.ui.estimate.textContent = '预计输出约 ' + fmtDims(outW, outH) +
      '（估算：' + scale + '× 重复 ' + Math.max(1, params.repeats) + ' 次）' +
      ' · 额外显存约 ' + vram.toFixed(1) + ' GB' +
      ' · 系统内存约 ' + ram.toFixed(1) + ' GB' +
      ' · 8K 参考耗时约 ' + timeText;
  };

  ImageTools.prototype.renderGeometry = function (message) {
    if (this.mode !== 'outpaint' || !this.ui.geometryText) return;
    var data = this.previewData;
    if (!data) {
      this.ui.geometryText.textContent = message || '选择图片并填写扩展量后，由服务端计算目标画布。';
      this.ui.canvasBox.style.aspectRatio = '';
      this.ui.originalBox.style.left = '0%';
      this.ui.originalBox.style.top = '0%';
      this.ui.originalBox.style.width = '100%';
      this.ui.originalBox.style.height = '100%';
      return;
    }
    var width = Number(data.width), height = Number(data.height);
    var offsetX = Number(data.offset_x), offsetY = Number(data.offset_y);
    var originalWidth = Number(data.original_width), originalHeight = Number(data.original_height);
    if (!(width > 0 && height > 0 && originalWidth > 0 && originalHeight > 0)) {
      this.ui.geometryText.textContent = '服务端返回的画布尺寸无效。';
      return;
    }
    var left = Math.max(0, Math.min(100, offsetX / width * 100));
    var top = Math.max(0, Math.min(100, offsetY / height * 100));
    var w = Math.max(2, Math.min(100 - left, originalWidth / width * 100));
    var h = Math.max(2, Math.min(100 - top, originalHeight / height * 100));
    // 示意图按服务端返回的目标画布比例绘制，避免视觉比例失真。
    this.ui.canvasBox.style.aspectRatio = width + ' / ' + height;
    this.ui.originalBox.style.left = left + '%';
    this.ui.originalBox.style.top = top + '%';
    this.ui.originalBox.style.width = w + '%';
    this.ui.originalBox.style.height = h + '%';

    var lines = [
      '目标画布 ' + fmtDims(width, height) + ' · 原图 ' + fmtDims(originalWidth, originalHeight),
      '偏移 x ' + Math.round(offsetX) + ' / y ' + Math.round(offsetY) +
        ' · 对齐 ' + (Number.isFinite(Number(data.alignment)) ? Number(data.alignment) : '未提供')
    ];
    if (data.adjusted === true) {
      lines.push('实际尺寸已按对齐要求调整：请求 ' +
        fmtDims(data.requested_width, data.requested_height) + ' → 最终 ' + fmtDims(width, height));
    }
    if (data.requested_margins && data.actual_margins) {
      [['请求扩展量',data.requested_margins],['对齐后实际扩展量',data.actual_margins]].forEach(function (entry) {
        var m=entry[1]; lines.push(entry[0]+'：左 '+m.left+' / 右 '+m.right+' / 上 '+m.top+' / 下 '+m.bottom+' px');
      });
    }
    var targetPixels = width * height;
    if (targetPixels <= 2_500_000) {
      lines.push('实际耗时取决于画布大小、步数和模型加载状态，可在完成后的任务详情查看。');
    } else {
      lines.push('预计显存 ≥ 8 GB、系统内存 18 GB+，可能耗尽 · 4K 已实测不可行，预计失败或超过 1 小时');
    }
    this.ui.geometryText.textContent = lines.join('\n');
    this.ui.geometryText.style.whiteSpace = 'pre-line';
    this.ui.previewState.textContent = '几何已确认';
  };

  /* ------------------------------------------------------------------ 校验 / 动作 */

  ImageTools.prototype.validate = function (params) {
    if (this.destroyed) return {ok: false, reason: '模块已销毁'};
    var cap = this.capability();
    if (!this.caps) return {ok: false, reason: '能力信息尚未加载，请稍候或点击刷新。'};
    if (!cap) return {ok: false, reason: '服务端未提供该工作流的能力信息。'};
    if (cap.available !== true) return {ok: false, reason: str(cap.reason) || '当前环境不可用（可能因电池供电或资源占用）。'};
    if (cap.verified !== true) return {ok: false, reason: str(cap.reason) || '本机能力待验证，暂不能提交。'};
    // 模型/能力缺口比"还没选图"更根本，先报出来。
    if (this.mode === 'upscale') {
      var verifiedModels = this.models();
      if (!verifiedModels.length) return {ok: false, reason: '暂无可用的已验证放大模型。'};
      var chosen = verifiedModels.filter(function (m) { return m.id === params.model_id; })[0];
      if (!chosen) return {ok: false, reason: '请选择一个已验证的放大模型。'};
    }
    if (!this.input) return {ok: false, reason: '请先选择一张图片。'};

    // 初版只处理 8 bit；16 bit 输入由后端明确拒绝，前端提前阻断提交。
    var inputBits = Number(this.input.bit_depth);
    if (Number.isFinite(inputBits) && inputBits > 8) {
      return {ok: false, reason: '初版只支持 8 bit 输入，当前图片为 ' + inputBits + ' bit。'};
    }
    if (this.submitting) return {ok: false, reason: '正在提交任务…'};
    if (this.isActive()) return {ok: false, reason: '已有任务在排队或运行。'};

    var limits = this.modeLimits();
    var maxBytes = Number(limits.max_input_bytes);
    if (Number.isFinite(maxBytes) && Number(this.input.size) > maxBytes) {
      return {ok: false, reason: '输入图片超过大小上限 ' + fmtBytes(maxBytes) + '。'};
    }
    var maxPixels = Number(limits.max_input_pixels);
    var pixels = Number(this.input.width) * Number(this.input.height);
    if (Number.isFinite(maxPixels) && maxPixels > 0 && pixels > maxPixels) {
      return {ok: false, reason: '输入像素数超过上限 ' + maxPixels.toLocaleString('zh-CN') + '。'};
    }

    if (this.mode === 'upscale') {
      var model = chosen;
      if (!Number.isFinite(params.tile_size) || params.tile_size < 64 || params.tile_size > 384 || params.tile_size % 32 !== 0) {
        return {ok: false, reason: '分块大小需为 64–384 之间、32 的倍数。'};
      }
      if (!Number.isFinite(params.repeats) || params.repeats < 1 || params.repeats > 2) {
        return {ok: false, reason: '重复次数只能是 1 或 2。'};
      }
      var scale = Number(model.scale);
      var maxOutput = Number(limits.max_output_pixels);
      if (Number.isFinite(scale) && scale > 0) {
        var factor = Math.pow(scale, params.repeats);
        var outW = Number(this.input.width) * factor;
        var outH = Number(this.input.height) * factor;
        var maxSide = Number(limits.max_dimension);
        if (Number.isFinite(maxSide) && maxSide > 0 && (outW > maxSide || outH > maxSide)) {
          return {ok: false, reason: '预计输出单边超过上限 ' + maxSide + ' 像素。'};
        }
        var outPixels = outW * outH;
        if (Number.isFinite(maxOutput) && maxOutput > 0 && outPixels > maxOutput) {
          return {ok: false, reason: '预计输出像素数超过上限，请减小输入图片或重复次数。'};
        }
      }
      return {ok: true, reason: ''};
    }

    var values = [params.extend_left, params.extend_right, params.extend_top, params.extend_bottom];
    if (values.some(function (v) { return !Number.isFinite(v) || v < 0 || v > 4096; })) {
      return {ok: false, reason: '四边扩展量需为 0–4096 之间的整数。'};
    }
    if (!values.some(function (v) { return v > 0; })) {
      return {ok: false, reason: '至少需要扩展一边。'};
    }
    if (!params.prompt) return {ok: false, reason: '扩图提示词不能为空。'};
    if (!Number.isFinite(params.steps) || params.steps < 1 || params.steps > 100) {
      return {ok: false, reason: '步数需在 1–100 之间。'};
    }
    if (!Number.isFinite(params.cfg) || params.cfg < 0 || params.cfg > 30) {
      return {ok: false, reason: 'CFG 需在 0–30 之间。'};
    }
    if (!Number.isFinite(params.seed) || params.seed < -1) {
      return {ok: false, reason: '种子需为 -1（随机）或不小于 0 的整数。'};
    }
    var maxDimension = Number(limits.max_dimension);
    if (Number.isFinite(maxDimension) && maxDimension > 0) {
      var targetW = Number(this.input.width) + params.extend_left + params.extend_right;
      var targetH = Number(this.input.height) + params.extend_top + params.extend_bottom;
      if (Number.isFinite(targetW) && Number.isFinite(targetH) &&
        (targetW > maxDimension || targetH > maxDimension)) {
        return {ok: false, reason: '目标画布超过单边上限 ' + maxDimension + ' 像素。'};
      }
    }
    if (this.previewPending) return {ok: false, reason: '正在计算画布预览，请稍候。'};
    if (!this.previewKey) return {ok: false, reason: '参数已修改，等待画布预览确认后才能提交。'};
    if (this.previewKey !== this.previewSignature(this.input.id, params)) {
      return {ok: false, reason: '参数已修改，等待画布预览确认后才能提交。'};
    }
    if (this.ui.confirm && !this.ui.confirm.checked) {
      return {ok: false, reason: '请先勾选内容确认。'};
    }
    return {ok: true, reason: ''};
  };

  ImageTools.prototype.availabilityWarning = function (params) {
    var limits = this.modeLimits();
    if (this.mode === 'upscale') {
      if (!this.input) return '';
      var model = this.models().filter(function (m) { return m.id === params.model_id; })[0];
      var scale = Number(model && model.scale) || 4;
      var factor = Math.pow(scale, Math.max(1, params.repeats));
      var width = Number(this.input.width) * factor;
      var height = Number(this.input.height) * factor;
      var dim = Number(limits.recommended_dimension) || 7680;
      var pixels = Number(limits.recommended_pixels) || 33177600;
      if (width > dim || height > dim || width * height > pixels) {
        return '提醒：已超过实测 8K 范围（7680×4320 / 3318 万像素）。放大不再设固定上限，' +
          '能否完成取决于当前内存、显存和磁盘预算；耗时与文件体积会明显增加。';
      }
      return '';
    }
    var data = this.previewData;
    if (!data) return '';
    var targetPixels = Number(data.width) * Number(data.height);
    var recommended = Number(limits.recommended_pixels) || 2220800;
    var hardPixels = Number(limits.max_output_pixels) || 16000000;
    var hardDim = Number(limits.max_dimension) || 4096;
    if (targetPixels > hardPixels || Number(data.width) > hardDim || Number(data.height) > hardDim) return '';
    if (targetPixels > recommended) {
      return '提醒：已超过实测安全范围（约 1984×1120 / 2.2 MP）。2K 已接近显存极限，4K 预计因系统内存不足失败；仍可提交。';
    }
    return '';
  };

  ImageTools.prototype.updateActions = function () {
    if (this.destroyed) return;
    this.renderModelState();
    var params = this.readParams();
    var check = this.validate(params);
    var blockedByJob = this.isActive();
    var disabled = this.submitting || !check.ok || blockedByJob;
    this.ui.startButton.disabled = !!disabled;
    this.ui.startButton.textContent = this.submitting ? '提交中…' : (this.mode === 'upscale' ? '开始放大' : '开始扩图');
    this.ui.saveDefaultsButton.disabled = this.savingDefaults;
    this.ui.reloadDefaultsButton.disabled = this.savingDefaults;
    this.ui.clearInputButton.disabled = this.submitting;
    this.ui.pickButton.disabled = this.submitting;
    this.ui.galleryButton.disabled = this.submitting;

    var job = this.job;
    var cancelRequested = !!job && job.cancellation_requested === true && !TERMINAL[job.status];
    var canCancel = !!job && !TERMINAL[job.status] && !this.cancelling && !cancelRequested;
    this.ui.cancelButton.hidden = !(canCancel || this.cancelling || cancelRequested);
    this.ui.cancelButton.disabled = !canCancel;
    this.ui.cancelButton.textContent = this.cancelling
      ? '取消请求中…'
      : (cancelRequested ? '取消请求已提交' : '取消任务');

    this.ui.blocked.textContent = check.ok ? (blockedByJob ? '任务进行中，结束后才能提交新任务。' : '参数校验通过，可以提交。') : check.reason;
    this.ui.blocked.classList.toggle('it-blocked-ok', !!check.ok && !blockedByJob);
    var warning = this.availabilityWarning(params);
    this.ui.estimateWarn.textContent = warning;
    this.ui.estimateWarn.hidden = !warning;
    this.ui.startButton.title = disabled
      ? (check.ok && blockedByJob ? '任务进行中，结束后才能提交新任务。' : (check.reason || '当前不能提交'))
      : '';
  };

  ImageTools.prototype.submit = function () {
    var self = this;
    if (this.destroyed || this.submitting) return;
    var params = this.readParams();
    var check = this.validate(params);
    if (!check.ok) {
      this.reportError(check.reason);
      this.notify(check.reason);
      return;
    }
    this.submitting = true;
    this.pollError = null;
    this.ui.startButton.disabled = true;
    this.ui.startButton.textContent = '提交中…';
    var body = {
      task_type: this.mode,
      input_id: this.input.id,
      params: params,
      content_confirmed: this.ui.confirm ? !!this.ui.confirm.checked : true
    };
    this.checkResources(false, params, body.input_id).then(function () {
      return self.api('/image-tools/jobs', {method: 'POST', body: body});
    })
      .then(function (job) {
        if (self.destroyed) return;
        if (!job || !job.id) throw new Error('服务端未返回任务信息。');
        self.job = job;
        self.upsertJob(job);
        self.clearError();
        self.renderJob();
        self.renderHistory();
        self.renderResult();
        self.notify('任务已提交');
        self.startPolling();
      })
      .catch(function (error) {
        if (self.destroyed) return;
        var message = error && error.message ? error.message : '提交失败';
        self.reportError(message);
        self.notify(message);
      })
      .then(function () {
        if (self.destroyed) return;
        self.submitting = false;
        self.updateActions();
      });
  };

  ImageTools.prototype.cancel = function () {
    var self = this;
    var job = this.job;
    if (this.destroyed || this.cancelling || !job || TERMINAL[job.status]) return;
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(str(job.id))) {
      this.notify('任务 ID 不合法，无法取消。');
      return;
    }
    this.cancelling = true;
    this.updateActions();
    this.api('/image-tools/jobs/' + encodeURIComponent(job.id) + '/cancel', {method: 'POST'})
      .then(function (result) {
        if (self.destroyed) return;
        var updated = result && result.job ? result.job : null;
        if (updated) {
          self.job = updated;
          self.upsertJob(updated);
        }
        self.renderJob();
        self.renderHistory();
        self.renderResult();
        if (result && result.cancellation_requested && self.job && !TERMINAL[self.job.status]) {
          self.ui.jobStage.textContent = '取消请求已提交；服务端会在当前任务真正结束后更新状态，请继续等待。';
        }
        self.startPolling();
      })
      .catch(function (error) {
        if (self.destroyed) return;
        self.notify(error && error.message ? error.message : '取消请求失败');
      })
      .then(function () {
        if (self.destroyed) return;
        self.cancelling = false;
        self.updateActions();
      });
  };

  ImageTools.prototype.saveDefaults = function () {
    var self = this;
    if (this.destroyed || this.savingDefaults) return;
    var params = this.readParams();
    this.savingDefaults = true;
    this.updateActions();
    this.api('/image-tools/defaults/' + this.mode, {method: 'PUT', body: params})
      .then(function () {
        if (self.destroyed) return;
        self.notify('默认参数已保存（未提交任务）');
      })
      .catch(function (error) {
        if (self.destroyed) return;
        self.notify(error && error.message ? error.message : '默认参数保存失败');
      })
      .then(function () {
        if (self.destroyed) return;
        self.savingDefaults = false;
        self.updateActions();
      });
  };

  ImageTools.prototype.reloadDefaults = function () {
    var self = this;
    if (this.destroyed) return;
    this.api('/image-tools/defaults/' + this.mode)
      .then(function (params) {
        if (self.destroyed) return;
        if (!params || typeof params !== 'object') throw new Error('服务端未返回默认参数。');
        self.applyParams(params);
        self.notify('已重新载入默认参数（未提交任务）');
      })
      .catch(function (error) {
        if (self.destroyed) return;
        self.notify(error && error.message ? error.message : '默认参数读取失败');
      });
  };

  /* ------------------------------------------------------------------ 轮询 */

  ImageTools.prototype.isActive = function () {
    return !!this.job && !TERMINAL[this.job.status];
  };

  ImageTools.prototype.startPolling = function () {
    if (this.destroyed || this.pollTimer !== null) return;
    var self = this;
    this.pollTimer = setTimeout(function () {
      self.pollTimer = null;
      self.pollOnce();
    }, 1500);
  };

  ImageTools.prototype.stopPolling = function () {
    if (this.pollTimer !== null) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
  };

  ImageTools.prototype.pollOnce = function () {
    var self = this;
    if (this.destroyed || this.polling) return;
    if (this.refreshInFlight) { this.startPolling(); return; }
    this.polling = true;
    this.api('/image-tools/jobs')
      .then(function (jobs) {
        if (self.destroyed) return;
        if (!Array.isArray(jobs)) return;
        self.acceptJobs(jobs);
        self.pollError = null;
        self.syncCurrentJob();
        self.renderJob();
        self.renderHistory();
        self.renderResult();
        self.updateActions();
      })
      .catch(function (error) {
        if (self.destroyed) return;
        self.pollError = error && error.message ? error.message : '状态刷新失败';
        self.ui.jobTimes.textContent = self.pollError + '（继续重试）';
      })
      .then(function () {
        self.polling = false;
        if (self.destroyed) return;
        if (self.isActive()) self.startPolling();
      });
  };

  ImageTools.prototype.syncCurrentJob = function () {
    if (!this.job) return;
    var match = this.jobs.filter(function (job) { return job && job.id === this.job.id; }, this)[0];
    if (match) this.job = match;
  };

  ImageTools.prototype.upsertJob = function (job) {
    var list = this.jobs.filter(function (item) { return item && item.id !== job.id; });
    list.unshift(job);
    this.jobs = list;
  };

  /* ------------------------------------------------------------------ 渲染 */

  ImageTools.prototype.renderAll = function () {
    if (this.destroyed) return;
    this.renderCapability();
    this.renderModelOptions();
    this.renderEstimate();
    this.renderJob();
    this.renderHistory();
    this.renderResult();
    this.updateActions();
  };

  ImageTools.prototype.renderCapability = function () {
    var cap = this.capability();
    var badge = this.ui.badge;
    badge.classList.remove('it-badge-ok', 'it-badge-warn', 'it-badge-idle');
    if (!this.caps) {
      badge.textContent = '能力待加载';
      badge.classList.add('it-badge-idle');
      return;
    }
    if (!cap) {
      badge.textContent = '能力未知';
      badge.classList.add('it-badge-warn');
      return;
    }
    if (cap.available === true && cap.verified === true) {
      badge.textContent = '本机已验证';
      badge.classList.add('it-badge-ok');
      return;
    }
    badge.textContent = str(cap.reason) || (cap.available === false ? '当前不可用' : '待本机验证');
    badge.classList.add('it-badge-warn');
  };

  ImageTools.prototype.renderModelOptions = function () {
    if (this.mode !== 'upscale' || !this.ui.model) return;
    var self = this;
    var select = this.ui.model;
    var current = select.value;
    clearNode(select);
    var models = this.models();
    var cap = this.capability();
    var all = cap && Array.isArray(cap.models) ? cap.models : [];
    if (!all.length) {
      var none = node('option', null, '暂无已验证的放大模型');
      none.value = '';
      select.appendChild(none);
      select.disabled = true;
      return;
    }
    if (!models.length) {
      var pending = node('option', null, '模型待本机验证');
      pending.value = '';
      select.appendChild(pending);
      select.disabled = true;
      return;
    }
    models.forEach(function (model) {
      var option = node('option', null, str(model.name) || str(model.id));
      option.value = str(model.id);
      var scale = Number(model.scale);
      if (!Number.isFinite(scale) || scale <= 0) option.textContent = option.textContent + '（倍率待验证）';
      select.appendChild(option);
    });
    select.disabled = false;
    var keep = models.filter(function (m) { return m.id === current; })[0];
    select.value = keep ? current : models[0].id;
    self.renderEstimate();
  };

  ImageTools.prototype.renderJob = function () {
    var job = this.job;
    var badge = this.ui.jobBadge;
    badge.classList.remove('it-badge-ok', 'it-badge-warn', 'it-badge-idle', 'it-badge-run');
    if (!job) {
      badge.textContent = '未提交';
      badge.classList.add('it-badge-idle');
      this.ui.jobStage.textContent = '尚未提交任务。';
      this.ui.progressWrap.hidden = true;
      this.ui.jobProgress.textContent = '';
      this.ui.jobTimes.textContent = '';
      this.ui.jobError.hidden = true;
      this.updateActions();
      return;
    }
    var status = str(job.status);
    var cancelRequested = job.cancellation_requested === true;
    var label = STATUS_TEXT[status] || '状态未知';
    if (cancelRequested && !TERMINAL[status]) label = '取消请求中';
    badge.textContent = label;
    badge.classList.add(TERMINAL[status] ? (status === 'completed' ? 'it-badge-ok' : 'it-badge-warn') : 'it-badge-run');

    var stage = str(job.stage);
    if (cancelRequested && !TERMINAL[status]) {
      stage = stage || '等待引擎结束当前任务…';
      if (status === 'running') stage = '取消请求已提交；引擎无法中断采样，等待当前任务结束。';
    }
    if (status === 'completed') stage = stage || '任务已完成。';
    if (status === 'failed') stage = stage || '任务失败。';
    if (status === 'cancelled') stage = stage || '任务已取消（服务端状态）。';
    if (!stage) stage = STATUS_TEXT[status] || '状态未知';
    this.ui.jobStage.textContent = stage;

    var progress = job.progress;
    if (isNumeric(progress) && progress >= 0 && progress <= 1) {
      this.ui.progressWrap.hidden = false;
      this.ui.progressBar.style.width = (progress * 100).toFixed(1) + '%';
      this.ui.jobProgress.textContent = '进度 ' + Math.round(progress * 100) + '%（服务端报告）';
    } else {
      this.ui.progressWrap.hidden = true;
      this.ui.progressBar.style.width = '0%';
      this.ui.jobProgress.textContent = '进度未知（服务端未报告百分比，不估算剩余时间）';
    }

    this.ui.jobTimes.textContent = formatClock(job);
    if (job.error) {
      this.ui.jobError.hidden = false;
      this.ui.jobError.textContent = str(job.error);
    } else {
      this.ui.jobError.hidden = true;
      this.ui.jobError.textContent = '';
    }
    this.updateActions();
  };

  ImageTools.prototype.renderHistory = function () {
    var self = this;
    clearNode(this.ui.history);
    var jobs = Array.isArray(this.jobs) ? this.jobs.slice(0, 5) : [];
    if (!jobs.length) {
      this.ui.history.appendChild(node('p', 'it-mini', '本工作流还没有任务记录。'));
      return;
    }
    jobs.forEach(function (job) {
      var item = node('button', 'it-history-item');
      item.type = 'button';
      item.setAttribute('data-job-id', str(job.id));
      var top = node('span', 'it-history-top');
      top.appendChild(node('strong', null, str(job.id).slice(0, 8)));
      var status = str(job.status);
      var statusLabel = job.cancellation_requested === true && !TERMINAL[status] ? '取消请求中' : (STATUS_TEXT[status] || '状态未知');
      var chip = node('span', 'it-chip', statusLabel);
      chip.classList.add(TERMINAL[status] ? (status === 'completed' ? 'it-chip-ok' : 'it-chip-warn') : 'it-chip-run');
      top.appendChild(chip);
      item.appendChild(top);
      item.appendChild(node('small', 'it-mini', str(job.stage) || fmtTime(job.created) || '无阶段信息'));
      item.addEventListener('click', function () {
        if (self.destroyed) return;
        self.job = job;
        self.renderJob();
        self.renderResult();
        if (!TERMINAL[status]) self.startPolling();
      });
      self.ui.history.appendChild(item);
    });
  };

  ImageTools.prototype.renderResult = function () {
    var self = this;
    var job = this.job;
    this.ui.seamWarning.textContent = '';
    this.clearDownload(this.ui.strictDownload); this.clearDownload(this.ui.comparisonDownload);
    if (!job || job.status !== 'completed' || !job.result) {
      this.ui.result.hidden = true;
      this.ui.resultInputImg.removeAttribute('src');
      this.ui.resultOutputImg.removeAttribute('src');
      this.clearDownload(this.ui.downloadOriginal);
      this.clearDownload(this.ui.downloadOutput);
      this.clearDownload(this.ui.downloadManifest);
      this.ui.preservation.textContent = '';
      return;
    }
    var result = job.result;
    // 历史结果只认任务记录里的输入；当前表单里新选的图片不能冒充它的原图。
    var record = job.input && typeof job.input === 'object' ? job.input : null;
    var inputPreview = record ? safePreviewUrl(record.preview_url) : null;
    if (inputPreview) this.ui.resultInputImg.src = inputPreview;
    else this.ui.resultInputImg.removeAttribute('src');

    var outputPreview = safePreviewUrl(result.preview_url);
    if (outputPreview) this.ui.resultOutputImg.src = outputPreview;
    else this.ui.resultOutputImg.removeAttribute('src');

    this.ui.resultMeta.textContent = '输出 ' + fmtDims(result.width, result.height) +
      (outputPreview ? '' : ' · 服务端未提供可用预览地址');

    // 原图下载只认任务记录里的 download_url；缺失就隐藏，绝不以预览图代替原图。
    this.applyDownload(this.ui.downloadOriginal, record ? safeDownloadUrl(record.download_url) : null);

    this.applyDownload(this.ui.downloadOutput, safeDownloadUrl(result.download_url));
    this.applyDownload(this.ui.downloadManifest, safeDownloadUrl(result.manifest_url));

    if (this.mode === 'outpaint') {
      var preserve = result.preservation;
      var diff = preserve ? preserve.different_pixels : null;
      // 严格判定：必须是数字 0，null / 空字符串 / 缺字段都不算"已保护"。
      var hasDiff = typeof diff === 'number' && Number.isFinite(diff);
      if (preserve && preserve.verified === true && hasDiff && diff === 0) {
        this.ui.preservation.textContent = preserve.scope === 'core'
          ? '原图核心像素已保护（差异像素 0）；过渡带允许变化，不代表整张原图像素不变。'
          : '原图像素已保护（差异像素 0，服务端校验通过）。';
        this.ui.preservation.className = 'it-preservation it-preservation-ok';
      } else if (preserve && preserve.verified === true && hasDiff && diff > 0) {
        this.ui.preservation.textContent = '原图像素未通过保护校验：差异像素 ' + diff + '。';
        this.ui.preservation.className = 'it-preservation it-preservation-bad';
      } else if (preserve) {
        this.ui.preservation.textContent = '原图像素保护结果未验证' + (hasDiff
          ? '（服务端报告差异像素 ' + diff + '，不能视为已保护）。'
          : '（差异像素未记录），不能视为已保护。');
        this.ui.preservation.className = 'it-preservation it-preservation-warn';
      } else {
        this.ui.preservation.textContent = '服务端未提供原图像素保护结果，不能视为已保护。';
        this.ui.preservation.className = 'it-preservation it-preservation-warn';
      }
    } else {
      this.ui.preservation.textContent = '';
      this.ui.preservation.className = 'it-preservation';
    }
    if (result.seam) {
      this.ui.seamWarning.textContent = (result.seam.warning || '') +
        (result.seam.fallback && result.seam.fallback.length ? ' 已触发安全回退，原因见运行记录。' : '');
    }
    this.applyDownload(this.ui.strictDownload,safeDownloadUrl(result.strict_url));
    this.applyDownload(this.ui.comparisonDownload,safeDownloadUrl(result.comparison_url));
    this.ui.result.hidden = false;
  };

  ImageTools.prototype.applyDownload = function (link, url) {
    if (url) {
      link.href = url;
      link.hidden = false;
      link.removeAttribute('aria-disabled');
    } else {
      link.removeAttribute('href');
      link.hidden = true;
      link.setAttribute('aria-disabled', 'true');
    }
  };

  ImageTools.prototype.clearDownload = function (link) {
    link.removeAttribute('href');
    link.hidden = true;
  };

  ImageTools.prototype.reportError = function (message) {
    var text = str(message) || '操作失败';
    this.ui.note.textContent = text;
    this.ui.note.classList.add('it-note-error');
  };

  ImageTools.prototype.clearError = function () {
    this.ui.note.textContent = '';
    this.ui.note.classList.remove('it-note-error');
  };

  /* ------------------------------------------------------------------ 销毁 */

  ImageTools.prototype.destroy = function () {
    if (this.destroyed) return;
    this.destroyed = true;
    this.stopPolling();
    clearTimeout(this.previewTimer);
    this.previewTimer = null;
    this.previewSeq++;
    if (this.onKeydown) this.root.removeEventListener('keydown', this.onKeydown);
    this.onKeydown = null;
    this.revokeObjectUrl();
    this.input = null;
    this.previewData = null;
    this.job = null;
    this.jobs = [];
    clearNode(this.root);
    this.root.classList.remove('image-tools', 'it-mode-upscale', 'it-mode-outpaint');
    this.root.removeAttribute('data-mode');
    this.ui = {};
  };

  global.QwenImageTools = {mount: mount};
})(window);
