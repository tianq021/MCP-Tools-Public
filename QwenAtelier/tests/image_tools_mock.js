/*
 * 纯内存 mock 服务：只服务于 tests/image_tools_mock.html。
 * 不访问真实 /api、不读取全局 state、不发网络请求。
 * 生产模块（web/image-tools.js）不会加载本文件。
 */
(function () {
  'use strict';

  var ALIGNMENT = 32;
  var LIMITS = {
    max_input_bytes: 26214400,
    max_input_pixels: 16000000,
    max_output_pixels: 67108864,
    max_dimension: 8192,
    upscale: {max_dimension: null, max_output_pixels: null,
              recommended_dimension: 7680, recommended_pixels: 33177600},
    outpaint: {max_dimension: 4096, max_output_pixels: 16000000,
               recommended_dimension: 2048, recommended_pixels: 2220800}
  };

  var SCENARIOS = [
    ['stable', '稳定：能力可用'],
    ['unverified', '能力未验证'],
    ['capability-fail', '能力接口失败'],
    ['empty-models', '模型列表为空'],
    ['upload-fail', '上传失败'],
    ['submit-fail', '提交失败'],
    ['jobs-fail', '状态刷新失败'],
    ['queued-cancel', '排队中取消'],
    ['running-cancel', '运行中取消仍等待'],
    ['failed-job', '任务失败（含 HTML 注入文本）'],
    ['completed-job', '任务完成可下载'],
    ['unverified-preservation', '完成但原图保护未通过'],
    ['preview-race', '预览响应乱序'],
    ['sixteen-bit-input', '16 bit 输入应被阻断'],
    ['alpha-input', '透明输入自动展平']
  ];

  function clone(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  }

  function synthImage(label, width, height) {
    var fontSize = Math.max(14, Math.round(width / 9));
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + width + '" height="' + height +
      '" viewBox="0 0 ' + width + ' ' + height + '">' +
      '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0" stop-color="#7860bf"/><stop offset="1" stop-color="#72d3b3"/>' +
      '</linearGradient></defs>' +
      '<rect width="100%" height="100%" fill="url(#g)"/>' +
      '<circle cx="' + Math.round(width * 0.72) + '" cy="' + Math.round(height * 0.32) +
      '" r="' + Math.round(Math.min(width, height) * 0.18) + '" fill="#ffffff33"/>' +
      '<text x="50%" y="52%" font-size="' + fontSize +
      '" fill="#ffffff" text-anchor="middle" font-family="sans-serif">' + label + '</text>' +
      '</svg>';
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
  }

  var state = {
    scenario: 'stable',
    mode: 'upscale',
    caps: null,
    defaults: {
      upscale: {model_id: 'realesrgan_anime_6b', tile_size: 128, repeats: 1, output_format: 'png'},
      outpaint: {
        extend_left: 128, extend_right: 128, extend_top: 0, extend_bottom: 0,
        prompt: '', negative_prompt: '', steps: 8, cfg: 1, seed: -1,
        sampler: 'euler', scheduler: 'default', keep_original_pixels: true, output_format: 'png'
      }
    },
    images: [
      {id: 'img_mock_001', name: '合成示例-风景.png', width: 512, height: 512, size: 48210, deleted: false},
      {id: 'img_mock_002', name: '合成示例-竖图.png', width: 384, height: 640, size: 39120, deleted: false},
      {id: 'img_mock_deleted', name: '已删除图片.png', width: 512, height: 512, size: 1, deleted: true}
    ],
    inputs: {},
    jobs: [],
    calls: [],
    previewCount: 0,
    inputSeq: 0,
    jobSeq: 0,
    notifyLog: [],
    destroyed: false
  };

  // 生产模块与 mock 的唯一通道。
  function api(path, options) {
    var opts = options || {};
    var method = (opts.method || 'GET').toUpperCase();
    var body = opts.body;
    state.calls.push({
      path: path,
      method: method,
      at: Date.now(),
      // FormData 不展开；其余 body 存快照，便于断言"保存默认参数"的请求内容。
      body: body && typeof body.get === 'function' ? {formdata: true} : clone(body)
    });

    return new Promise(function (resolve, reject) {
      var delay = 20;
      if (state.scenario === 'preview-race' && path === '/image-tools/outpaint/preview') {
        state.previewCount += 1;
        delay = state.previewCount === 1 ? 900 : 60;
      }
      setTimeout(function () {
        try {
          resolve(clone(route(path, method, body)));
        } catch (error) {
          reject(error instanceof Error ? error : new Error(String(error)));
        }
      }, delay);
    });
  }

  function route(path, method, body) {
    if (path === '/image-tools/models') return state.modelStates || {
      upscale: {state:'idle',loaded:false,pid:null}, outpaint: {state:'idle',loaded:false,pid:null}
    };
    if (path.indexOf('/image-tools/models/') === 0) {
      var parts = path.split('/');
      state.modelStates = state.modelStates || {upscale:{},outpaint:{}};
      var loaded = parts[4] === 'load';
      state.modelStates[parts[3]] = {state:loaded?'ready':'idle',loaded:loaded,pid:loaded?12345:null};
      return state.modelStates[parts[3]];
    }
    if (path.indexOf('/image-tools/resources/') === 0) {
      var mode = path.split('/').pop();
      var low = state.scenario === 'resource-low';
      if (mode === 'outpaint') return {allowed:true, reason:'', warning: low ? '系统内存不足（mock）' : '', can_cancel:true};
      return {allowed:!low, reason:'系统内存不足（mock）', can_cancel:true};
    }
    if (path === '/image-tools/capabilities' && method === 'GET') {
      if (state.scenario === 'capability-fail') throw new Error('模拟能力接口失败：服务未就绪');
      return capabilities();
    }
    if (path.indexOf('/image-tools/defaults/') === 0) {
      var mode = path.split('/').pop();
      if (mode !== 'upscale' && mode !== 'outpaint') throw new Error('未知工作流');
      if (method === 'PUT') {
        state.defaults[mode] = clone(body);
        return state.defaults[mode];
      }
      return state.defaults[mode];
    }
    if (path === '/image-tools/inputs' && method === 'POST') {
      if (state.scenario === 'upload-fail') throw new Error('模拟上传失败：文件无法读取');
      if (!body || typeof body.get !== 'function') throw new Error('上传需要 FormData');
      var file = body.get('file');
      if (!file) throw new Error('未收到文件');
      return addInput({
        name: file.name || 'upload.png',
        width: 512,
        height: 512,
        size: file.size,
        bit_depth: 8
      });
    }
    if (path === '/image-tools/inputs/from-image' && method === 'POST') {
      var imageId = body && body.image_id;
      var image = libraryImages().filter(function (item) { return item.id === imageId; })[0];
      if (!image || image.deleted) throw new Error('图库图片不存在或已删除');
      return addInput({
        name: image.name,
        width: image.width,
        height: image.height,
        size: image.size,
        bit_depth: Number.isFinite(Number(image.bit_depth)) ? Number(image.bit_depth) : 8,
        has_transparency: image.has_transparency === true,
        alpha: image.alpha || null
      });
    }
    if (path === '/image-tools/outpaint/preview' && method === 'POST') {
      return preview(body);
    }
    if (path === '/image-tools/jobs' && method === 'POST') {
      if (state.scenario === 'submit-fail') throw new Error('模拟提交失败：引擎正在执行其他任务');
      return submit(body);
    }
    if (path === '/image-tools/jobs' && method === 'GET') {
      if (state.scenario === 'jobs-fail') throw new Error('模拟状态刷新失败');
      advanceJobs();
      return state.jobs;
    }
    if (/^\/image-tools\/jobs\/[^/]+\/cancel$/.test(path) && method === 'POST') {
      var jobId = decodeURIComponent(path.split('/')[3]);
      return cancel(jobId);
    }
    if (path === '/images' && method === 'GET') {
      return libraryImages();
    }
    throw new Error('mock 未实现接口：' + method + ' ' + path);
  }

  // 16 bit 合成图只在对应场景出现，用于验证"位深 > 8 禁止提交"。
  function libraryImages() {
    if (state.scenario === 'alpha-input') {
      return state.images.concat([
        {id: 'img_mock_alpha', name: '合成示例-alpha噪声.png', width: 512, height: 512, size: 52000, deleted: false,
         bit_depth: 8, has_transparency: true,
         alpha: {min: 252, visible_pixels: 0, visible_ratio: 0, policy: 'drop-alpha'}},
        {id: 'img_mock_cutout', name: '合成示例-镂空.png', width: 512, height: 512, size: 61000, deleted: false,
         bit_depth: 8, has_transparency: true,
         alpha: {min: 0, visible_pixels: 131072, visible_ratio: 0.5, policy: 'composite-white'}}
      ]);
    }
    if (state.scenario !== 'sixteen-bit-input') return state.images;
    return state.images.concat([{
      id: 'img_mock_16bit',
      name: '合成示例-16bit.png',
      width: 512,
      height: 512,
      size: 96000,
      deleted: false,
      bit_depth: 16
    }]);
  }

  function capabilities() {    var upscale = {
      verified: true,
      available: true,
      reason: '本机已验证（mock）',
      models: [
        {id: 'realesrgan_anime_6b', name: 'RealESRGAN x4plus anime 6B（mock）', scale: 4, verified: true},
        {id: 'pending_upstream', name: '待验证放大模型（mock）', scale: null, verified: false}
      ]
    };
    var outpaint = {
      verified: true,
      available: true,
      reason: '本机已验证（mock）',
      alignment: ALIGNMENT,
      strict_preservation: true
    };
    if (state.scenario === 'unverified') {
      upscale = {verified: false, available: false, reason: '待本机验证', models: []};
      outpaint = {verified: false, available: false, reason: '待本机验证', alignment: ALIGNMENT, strict_preservation: true};
    }
    if (state.scenario === 'empty-models') {
      upscale = {verified: true, available: true, reason: '本机已验证（mock）', models: []};
    }
    return {
      upscale: upscale,
      outpaint: outpaint,
      limits: LIMITS,
      output_bit_depths: [8]
    };
  }

  function preview(body) {
    if (!body || !body.input_id || !body.params) throw new Error('预览参数不完整');
    var input = state.inputs[body.input_id];
    if (!input) throw new Error('输入图片不存在');
    var params = body.params;
    var left = Math.max(0, Math.round(Number(params.extend_left) || 0));
    var right = Math.max(0, Math.round(Number(params.extend_right) || 0));
    var top = Math.max(0, Math.round(Number(params.extend_top) || 0));
    var bottom = Math.max(0, Math.round(Number(params.extend_bottom) || 0));
    var requestedWidth = input.width + left + right;
    var requestedHeight = input.height + top + bottom;
    var width = Math.ceil(requestedWidth / ALIGNMENT) * ALIGNMENT;
    var height = Math.ceil(requestedHeight / ALIGNMENT) * ALIGNMENT;
    return {
      width: width,
      height: height,
      offset_x: left,
      offset_y: top,
      original_width: input.width,
      original_height: input.height,
      requested_width: requestedWidth,
      requested_height: requestedHeight,
      requested_margins: {left:left,right:right,top:top,bottom:bottom},
      actual_margins: {left:left,right:width-input.width-left,top:top,bottom:height-input.height-top},
      alignment: ALIGNMENT,
      adjusted: width !== requestedWidth || height !== requestedHeight
    };
  }

  function addInput(meta) {
    state.inputSeq += 1;
    var id = 'inp_mock_' + state.inputSeq;
    var desc = {
      id: id,
      name: meta.name,
      width: meta.width,
      height: meta.height,
      size: meta.size,
      bit_depth: meta.bit_depth,
      has_transparency: meta.has_transparency === true,
      alpha: meta.alpha || null,
      preview_url: synthImage('输入 ' + meta.width + '×' + meta.height, 360, Math.round(360 * meta.height / meta.width)),
      // 契约只定义了 preview_url；"下载原图"需要一个 /api/ 下载地址，
      // 按契约缺口补一个可选字段。
      download_url: '/api/image-tools/inputs/' + id + '/download'
    };
    state.inputs[id] = desc;
    return desc;
  }

  function submit(body) {
    if (!body || !body.input_id || !body.params) throw new Error('提交参数不完整');
    if (body.content_confirmed !== true) throw new Error('缺少内容确认');
    var input = state.inputs[body.input_id];
    if (!input) throw new Error('输入图片不存在');
    state.jobSeq += 1;
    var job = {
      id: 'job_mock_' + state.jobSeq,
      task_type: body.task_type,
      status: 'queued',
      stage: '排队中',
      progress: null,
      created: Date.now() / 1000,
      started: null,
      finished: null,
      error: null,
      cancellation_requested: false,
      input: {
        id: input.id,
        name: input.name,
        width: input.width,
        height: input.height,
        preview_url: input.preview_url,
        download_url: input.download_url
      },
      params: clone(body.params),
      result: null,
      pollCount: 0
    };
    state.jobs.unshift(job);
    return job;
  }

  function cancel(jobId) {
    var job = state.jobs.filter(function (item) { return item.id === jobId; })[0];
    if (!job) throw new Error('任务不存在');
    if (job.status === 'queued') {
      job.status = 'cancelled';
      job.cancellation_requested = true;
      job.stage = '已取消（服务端）';
      job.finished = Date.now() / 1000;
      return {cancellation_requested: true, job: job};
    }
    if (job.status === 'running') {
      job.cancellation_requested = true;
      job.stage = '取消请求已提交，等待当前采样结束';
      return {cancellation_requested: true, job: job};
    }
    return {cancellation_requested: false, job: job};
  }

  function advanceJobs() {
    if (state.scenario === 'preview-race' || state.scenario === 'queued-cancel' ||
      state.scenario === 'jobs-fail') return;
    state.jobs.forEach(function (job) {
      if (job.cancellation_requested && state.scenario !== 'running-cancel') return;
      job.pollCount += 1;
      if (job.status === 'queued') {
        job.status = 'running';
        job.stage = '采样中';
        job.progress = 0.4;
        job.started = Date.now() / 1000;
        return;
      }
      if (job.status !== 'running' || job.cancellation_requested) return;
      if (state.scenario === 'completed-job') {
        complete(job, {verified: true, different_pixels: 0});
      } else if (state.scenario === 'unverified-preservation') {
        complete(job, {verified: false, different_pixels: 37});
      } else if (state.scenario === 'failed-job') {
        job.status = 'failed';
        job.stage = '失败';
        job.progress = null;
        job.finished = Date.now() / 1000;
        job.error = '生成失败：<img src=x onerror="window.__xss=1"> 引擎返回错误，请查看后端日志。';
      }
    });
  }

  function complete(job, preservation) {
    var input = state.inputs[job.input.id] || job.input;
    var width = job.task_type === 'upscale' ? input.width * 4 : (input.width + 256);
    var height = job.task_type === 'upscale' ? input.height * 4 : input.height;
    job.status = 'completed';
    job.stage = '已完成';
    job.progress = 1;
    job.finished = Date.now() / 1000;
    job.result = {
      image_id: 'img_out_' + job.id,
      width: width,
      height: height,
      preview_url: synthImage('输出 ' + width + '×' + height, 480, Math.round(480 * height / width)),
      download_url: '/api/images/img_out_' + job.id + '/file?download=true',
      manifest_url: '/api/image-tools/jobs/' + job.id + '/manifest',
      preservation: job.task_type === 'outpaint' ? preservation : null
    };
  }

  /* ---------------------------------------------------------------- 页面绑定 */

  var root = document.getElementById('it-root');
  var logBox = document.getElementById('mock-log');
  var toastBox = document.getElementById('mock-toast');
  var modeSelect = document.getElementById('mock-mode');
  var themeSelect = document.getElementById('mock-theme');
  var widthSelect = document.getElementById('mock-width');
  var scenarioBar = document.getElementById('mock-scenarios');
  var handle = null;
  var toastTimer = null;

  function notify(message) {
    state.notifyLog.push(String(message));
    toastBox.textContent = String(message);
    toastBox.classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastBox.classList.remove('visible'); }, 2600);
  }

  function renderLog() {
    var lines = state.calls.slice(-40).map(function (call, index) {
      return String(index + 1).padStart(2, '0') + ' ' + call.method + ' ' + call.path;
    });
    logBox.textContent = lines.join('\n') || '（尚无调用）';
    logBox.scrollTop = logBox.scrollHeight;
  }

  function mountPanel() {
    destroyPanel();
    root.textContent = '';
    handle = window.QwenImageTools.mount({
      root: root,
      mode: state.mode,
      api: trackedApi,
      notify: notify
    });
    renderLog();
    return handle;
  }

  function destroyPanel() {
    if (handle) {
      handle.destroy();
      handle = null;
    }
    state.destroyed = true;
  }

  function resetScenarioData(name) {
    state.modelStates = null;
    state.scenario = name;
    state.jobs = [];
    state.inputs = {};
    state.previewCount = 0;
    state.inputSeq = 0;
    state.jobSeq = 0;
    state.calls = [];
    state.notifyLog = [];
    Array.prototype.forEach.call(scenarioBar.children, function (button) {
      button.classList.toggle('active', button.getAttribute('data-scenario') === name);
    });
    renderLog();
  }

  function setScenario(name) {
    resetScenarioData(name);
    mountPanel();
  }

  function buildScenarioButtons() {
    SCENARIOS.forEach(function (entry) {
      var button = document.createElement('button');
      button.type = 'button';
      button.textContent = entry[1];
      button.setAttribute('data-scenario', entry[0]);
      button.addEventListener('click', function () { setScenario(entry[0]); });
      scenarioBar.appendChild(button);
    });
  }

  modeSelect.addEventListener('change', function () {
    state.mode = modeSelect.value;
    mountPanel();
  });

  themeSelect.addEventListener('change', function () {
    document.body.classList.toggle('light', themeSelect.value === 'light');
  });

  widthSelect.addEventListener('change', function () {
    var width = widthSelect.value;
    document.querySelector('.mock-shell').style.maxWidth = width === 'fluid' ? '1280px' : width + 'px';
  });

  document.getElementById('mock-remount').addEventListener('click', function () { mountPanel(); });
  document.getElementById('mock-destroy').addEventListener('click', function () {
    destroyPanel();
    root.textContent = '（模块已销毁；可点「重新挂载」恢复）';
  });
  document.getElementById('mock-clear-log').addEventListener('click', function () {
    state.calls = [];
    state.notifyLog = [];
    renderLog();
  });

  // 记录所有调用，便于自动检查"destroy 后没有轮询"。
  function trackedApi(path, options) {
    var promise = api(path, options);
    renderLog();
    return promise;
  }

  window.MockHarness = {
    state: state,
    api: trackedApi,
    setScenario: setScenario,
    setMode: function (mode) {
      state.mode = mode;
      modeSelect.value = mode;
      mountPanel();
    },
    mount: function () { return mountPanel(); },
    destroy: function () {
      destroyPanel();
    },
    calls: function () { return state.calls.slice(); },
    countCalls: function (path, method) {
      return state.calls.filter(function (call) {
        return call.path === path && (!method || call.method === method);
      }).length;
    },
    notifyLog: function () { return state.notifyLog.slice(); },
    clearCalls: function () { state.calls = []; renderLog(); },
    synth: synthImage
  };

  buildScenarioButtons();
  resetScenarioData('stable');
  mountPanel();
  renderLog();
})();
