import { appendFile, mkdir, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { RESOURCE_LABELS, summarizeEconomy, summarizeThreats } from "../tools-extra.mjs";

const clock = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", dateStyle: "short", timeStyle: "medium", hourCycle: "h23",
});
const timestamp = (value) => value == null ? "未采样" : clock.format(value);
const number = (value) => Number.isFinite(value) ? String(Math.round(value * 100) / 100) : "未知";
const percent = (ratio) => Number.isFinite(ratio) ? number(ratio * 100) + "%" : "未知";
const position = (value) => value ? "(" + number(value.x) + ", " + number(value.z) + ")" : "未知";
const cell = (value) => String(value ?? "—").replace(/[\\|<>]/g, "\\$&").replace(/\r?\n/g, " ");
const resource = (id) => RESOURCE_LABELS[id] ?? id;
const cargo = (items) => Object.entries(items ?? {})
  .map(([id, amount]) => resource(id) + " " + number(amount)).join("、") || "空";

const eventDescriptions = {
  enemy_approach: (data) => "敌群 " + data.groupId + "（" + data.count + " 人）距总部 " + data.hqDistance,
  squad_threat: (data) => "小队 " + data.squadId + " 附近有敌群 " + data.groupId + "，距离 " + data.distance,
  squad_low_hp: (data) => "小队 " + data.squadId + " 队员 " + data.memberId + " 血量 " + data.hp,
  order_completed: (data) => "小队 " + data.squadId + " 的 " + data.previousOrder + " 已离开当前订单栏",
  order_changed: (data) => "小队 " + data.squadId + " 切换订单为 " + data.to,
  squad_stuck: (data) => "小队 " + data.squadId + " 长时间停在原地",
  cargo_full: (data) => "车辆 " + data.vehicleId + " 装载率 " + percent(data.ratio),
  low_stock: (data) => resource(data.resourceId) + " 不足，可用 " + data.available,
  mission_updated: (data) => "任务变化 " + (data.missions ?? []).join("、"),
  nightfall: () => "入夜",
  new_day: (data) => "进入第 " + data.day + " 天",
  game_paused_changed: (data) => data.paused ? "游戏暂停" : "游戏恢复",
  focus_changed: (data) => data.focused ? "游戏回到前台" : "游戏失焦",
  game_not_ready: () => "聚落暂未就绪",
  bridge_offline: () => "游戏桥接断开",
  bridge_restored: () => "游戏桥接恢复",
  bridge_error: (data) => "桥接读取异常 " + (data.code ?? ""),
  session_changed: () => "切换游戏会话",
  dialogue_options_available: () => "收到无线电通话",
  radio_options_changed: (data) => "无线电选项更新（" + data.dialogue.options.length + " 项）",
  radio_ended: () => "无线电通话结束",
  action_finished: (data) => "小队 " + data.squadId + " 的协作行动 " + data.command + "，结果 " + data.state,
  action_verify_required: (data) => "协作行动待核实 " + (data.taskId ?? ""),
};

function renderStatus({ state, health, radio, events, now, stopped, dropped }) {
  const ready = health.online && health.ready && !stopped;
  const connection = stopped ? "Runtime 已停止" : ready ? "游戏在线" : health.online ? "聚落暂未就绪" : "游戏桥接离线";
  const lines = ["# 无感染区当前态势", "",
    "- 文件刷新（北京时间） " + timestamp(now),
    "- 状态 " + connection + (state && !ready ? "，以下为上次缓存，已过期" : ""),
    "- 游戏会话 " + (state?.sessionId ?? "无"),
    "- 状态采样 " + timestamp(state?.at) + (state ? "（" + number((now - state.at) / 1000) + " 秒前）" : ""),
    "- 库存采样 " + timestamp(state?.stockpileAt)];
  if (health.lastError) lines.push("- 最近读取错误 " + cell(health.lastError.code));
  if (dropped) lines.push("- 落盘积压期间有 " + dropped + " 条早期事件未能保存");
  if (state?.time) {
    const { day, hour, paused } = state.time;
    const minutes = Number.isFinite(hour) ? Math.floor(hour * 60 + 1e-6) : null;
    const time = minutes == null ? "未知" : [Math.floor(minutes / 60), minutes % 60]
      .map((part) => String(part).padStart(2, "0")).join(":");
    const speed = paused ? (state.radioPaused ? "无线电暂停" : "暂停") : "运行中";
    lines.push("- 游戏内第 " + number(day) + " 天 " + time + "，" + speed + (state.focused ? "" : "，失焦"));
  }

  lines.push("", "## 无线电", "");
  const dialogue = state?.dialogue;
  const observed = dialogue ?? radio?.dialogue;
  if (observed) {
    lines.push(dialogue ? (ready ? "当前通话" : "上次采样时的通话") : "最近一通已结束的通话", "");
    const text = dialogue ? observed.text : [...radio.lines, radio.text].filter(Boolean).join("\n");
    if (text) lines.push(...text.split(/\r?\n/).map((line) => "> " + cell(line)), "");
    if (observed.text_truncated) lines.push("游戏返回的台词有截断。", "");
    if (dialogue && !dialogue.options?.length) lines.push("当前没有可读选项。");
    for (const option of dialogue?.options ?? []) {
      const hints = (option.effect_hints ?? []).map(({ keys, alternatives, value, percent: isPercent }) =>
        (keys ?? []).map(resource).join(alternatives ? " 或 " : " + ") + " " + number(value) + (isPercent ? "%" : "")
      ).join("、");
      lines.push("- " + cell(option.text) + (option.selectable ? "" : "（暂不可选）") +
        (option.ends_call ? "（结束通话）" : "") + (hints ? "，游戏提示 " + cell(hints) : ""));
    }
  } else {
    lines.push(state ? "暂无通话记录。" : "等待首次游戏采样。");
  }

  lines.push("", "## 最近事件", "");
  for (const event of events) {
    const description = eventDescriptions[event.type]?.(event.data ?? {}) ?? event.type;
    lines.push("- " + timestamp(event.at) + " " + cell(description));
  }
  if (!events.length) lines.push("本会话暂无已捕获事件。");
  if (!state) return lines.join("\n") + "\n";

  const threats = state.headquarters ? summarizeThreats(state.groups.filter((group) => group.visible && group.position), {
    hq: state.headquarters, maxDistance: 800,
  }).nearest.slice(0, 5) : [];
  lines.push("", "## 总部与可见敌情", "",
    "- 总部 " + position(state.headquarters));
  for (const threat of threats) {
    lines.push("- 敌群 " + cell(threat.groupId) + " " + cell(threat.type) +
      "，距总部 " + number(threat.distanceToHq) + "，人数 " + number(threat.members) + (threat.closing ? "，正在逼近" : ""));
  }
  if (!threats.length) lines.push("总部附近暂无已报告的可见敌群。");

  const { shortfalls } = summarizeEconomy(state.stockpile, []);
  lines.push("", "## 共享库存", "",
    "- 常用物资缺口 " + (shortfalls.map(({ label, gap }) => label + " " + gap).join("、") || "暂无"),
    "", "| 资源 | 库存 | 可用 |", "|---|---:|---:|");
  for (const item of state.stockpile) {
    const label = resource(item.resource_id) + (item.storage === "virtual" ? "（虚拟）" : "");
    lines.push("| " + cell(label) + " | " + number(item.quantity) + " | " + number(item.available) + " |");
  }
  if (!state.stockpile.length) lines.push("| 没有已采集的库存条目 | — | — |");

  lines.push("", "## 小队（" + state.squads.length + "）", "");
  const vehicles = new Map(state.vehicles.map((vehicle) => [String(vehicle.id), vehicle]));
  for (const squad of state.squads) {
    lines.push("### " + cell(squad.name || "小队") + "（ID " + cell(squad.id) + "）", "",
      "- " + number(squad.member_count) + " 人，位置 " + position(squad.position) + "，目的地 " + position(squad.destination),
      "- 当前订单 " + cell(squad.active_order ?? "空闲") + "，排队 " + cell(squad.queued_orders?.join(" → ") || "无"),
      "- 随身携货 " + cell(cargo(squad.cargo)));
    const vehicle = vehicles.get(String(squad.vehicle_id));
    if (vehicle) {
      lines.push("- 乘坐 " + cell(vehicle.type) + "（ID " + cell(vehicle.id) + "），油量 " + percent(vehicle.fuel?.ratio) +
        "，后备箱 " + percent(vehicle.trunk?.load_ratio) + "，载货 " + cell(cargo(vehicle.trunk?.cargo)));
    } else if (squad.vehicle_id != null) {
      lines.push("- 乘坐车辆 " + cell(squad.vehicle_id) + "，暂无车辆详情");
    }
    if (squad.waiting_vehicle_id != null) lines.push("- 等待车辆 " + cell(squad.waiting_vehicle_id));
    for (const member of squad.members ?? []) {
      lines.push("- " + cell(member.name || member.id) + " · 血量 " + number(member.hp) + "/" + number(member.max_hp) +
        " · 武器 " + cell(member.weapon_id ?? "无") + " · 弹药 " + number(member.ammo));
    }
    if (squad.member_count > (squad.members?.length ?? 0)) lines.push("- 部分队员没有可用明细");
    lines.push("");
  }
  if (!state.squads.length) lines.push("暂无玩家小队。", "");

  lines.push("## 当前任务和事件", "");
  for (const item of state.events) {
    const objectives = (item.objectives ?? []).map((row) => (row.completed ? "已完成 " : "待完成 ") + row.type);
    lines.push("- " + cell(item.title_key ?? item.id) + (objectives.length ? " · " + cell(objectives.join("、")) : ""));
  }
  if (!state.events.length) lines.push("暂无活动任务或事件。");
  return lines.join("\n") + "\n";
}

export class LiveFiles {
  constructor({ stateService, directory, logger = console.error }) {
    this.service = stateService;
    this.directory = directory;
    this.currentPath = join(directory, "current.md");
    this.eventPath = join(directory, "events.jsonl");
    this.temporaryPath = join(directory, ".current-" + process.pid + ".tmp");
    this.logger = logger;
    this.pending = [];
    this.recent = [];
    this.dropped = 0;
    this.onEvent = (event) => this.record(event);
    this.onState = () => this.observeRadio();
  }

  start() {
    this.service.on("event", this.onEvent);
    this.service.on("state", this.onState);
    this.observeRadio();
    this.tick();
    this.timer = setInterval(() => this.tick(), 1000);
    this.timer.unref();
  }

  record(event) {
    const record = { ...event, at: event.at ?? Date.now(),
      sessionId: event.data?.sessionId ?? this.service.state?.sessionId ?? null };
    this.recent.push(record);
    if (this.recent.length > 12) this.recent.shift();
    this.pending.push(record);
    if (this.pending.length > 2000) {
      this.pending.shift();
      this.dropped++;
    }
  }

  observeRadio() {
    const state = this.service.state;
    if (!state) return;
    if (this.radioSession !== state.sessionId) {
      this.radio = null;
      this.radioSession = state.sessionId;
    }
    const dialogue = state.dialogue;
    const previous = this.radio;
    const sameCall = previous?.active && dialogue && previous.dialogue.transmission_id === dialogue.transmission_id;
    if (previous?.active && !sameCall) {
      this.record({ type: "radio_ended", at: state.at, data: {
        dialogue: previous.dialogue, observed_lines: [...previous.lines, previous.text].filter(Boolean),
      } });
      this.radio = { ...previous, active: false };
    }
    if (dialogue) {
      const options = JSON.stringify([dialogue.choice_id, dialogue.options]);
      if (dialogue.options?.length && (!sameCall || previous.options !== options)) {
        this.record({ type: "radio_options_changed", at: state.at, data: { dialogue } });
      }
      const lastText = sameCall ? previous.text : "";
      const text = dialogue.text || lastText;
      let lines = sameCall ? previous.lines : [];
      if (lastText && !text.startsWith(lastText)) lines = [...lines, lastText].slice(-8);
      this.radio = { active: true, dialogue, options, lines, text };
    }
  }

  tick() {
    if (this.writing) return;
    this.writing = this.write().finally(() => { this.writing = null; });
  }

  async write(stopped = false) {
    try {
      const health = this.service.health();
      const state = this.service.state;
      const events = this.recent.filter((event) => event.sessionId === (state?.sessionId ?? null)).reverse();
      const markdown = renderStatus({ state, health, radio: this.radio, events, now: Date.now(), stopped, dropped: this.dropped });
      await mkdir(this.directory, { recursive: true });
      const batch = this.pending.slice();
      if (batch.length || !this.eventsInitialized) {
        await appendFile(this.eventPath, batch.map((item) => JSON.stringify(item) + "\n").join(""), "utf8");
        this.eventsInitialized = true;
        const written = new Set(batch);
        this.pending = this.pending.filter((item) => !written.has(item));
      }
      await writeFile(this.temporaryPath, markdown, "utf8");
      await rename(this.temporaryPath, this.currentPath);
      this.lastError = null;
    } catch (error) {
      if (this.lastError !== error.message) this.logger(error.message);
      this.lastError = error.message;
    }
  }

  async stop() {
    clearInterval(this.timer);
    this.service.off("event", this.onEvent);
    this.service.off("state", this.onState);
    await this.writing;
    await this.write(true);
  }
}
