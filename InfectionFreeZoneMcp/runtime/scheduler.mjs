import { EventEmitter } from "node:events";
import { randomUUID } from "node:crypto";
import { setTimeout as sleep } from "node:timers/promises";
import { z } from "zod";

const userMessage = z.strictObject({
  requestId: z.string().uuid(),
  text: z.string().trim().min(1).max(4000),
  interrupt: z.boolean().default(false),
});
const danger = new Set(["enemy_approach", "squad_low_hp", "squad_threat", "action_verify_required", "bridge_offline",
  "dialogue_options_available"]);
const ordinary = new Set(["order_completed", "order_changed", "cargo_full", "low_stock", "nightfall", "mission_updated",
  "new_day", "enemy_approach", "squad_stuck", "bridge_restored", "bridge_error", "game_not_ready", "session_changed"]);
// 静音模式：只压制玩家侧高频噪音事件（订单变更/货满/任务更新等），
// 保留 order_completed 等任务完成事件，便于 AI 实时接管下一单。
const quietGameEvents = process.env.IFZ_QUIET_GAME_EVENTS === "1";
const quietSuppressed = new Set(["order_changed", "cargo_full", "mission_updated", "nightfall", "low_stock"]);
// 协作分工模式：玩家负责全部小队时，用事件名列表彻底不唤醒 AI（如
// "squad_threat,order_completed,enemy_approach"），避免战斗期间刷屏。
const suppressedEvents = new Set(String(process.env.IFZ_SUPPRESS_EVENTS ?? "")
  .split(",").map((name) => name.trim()).filter(Boolean));
// 事件合并与限频（默认关闭，按需用环境变量开启）：开启后把一小段时间内的事件
// 合并成一条消息，且两次唤醒之间至少间隔 eventMinWakeMs 毫秒，避免密集打扰。
const eventDigestMs = Math.max(0, Number(process.env.IFZ_EVENT_DIGEST_MS ?? 0) || 0);
const eventMinWakeMs = Math.max(0, Number(process.env.IFZ_EVENT_MIN_WAKE_MS ?? 0) || 0);
const fail = (status, code, message) => Object.assign(new Error(message), { status, code });
const retryDelays = [500, 1500];
const messageHistoryMs = 30 * 60 * 1000;
const messageHistoryLimit = 20000;
const offlineEvents = new Set(["action_finished", "action_verify_required", "bridge_offline", "bridge_error", "game_not_ready"]);

export class Scheduler extends EventEmitter {
  constructor({ stateService, dsh, gameSessionId, getAssignment, liveStatusPath, dangerMs = 300, ordinaryMs = 300 }) {
    super();
    this.stateService = stateService;
    this.dsh = dsh;
    this.gameSessionId = gameSessionId;
    this.getAssignment = getAssignment;
    this.liveStatusPath = liveStatusPath;
    this.readStatus = liveStatusPath
      ? "需要观察局势时，先用文件读取功能打开 " + liveStatusPath + "，核对采样时间和游戏会话。文件缺失或需要文件之外的细节时，再查相应工具。"
      : "需要观察局势时，用 world_snapshot 查看有关类目，无线电用 get_active_dialogue。";
    this.dangerMs = dangerMs;
    this.ordinaryMs = ordinaryMs;
    this.abort = new AbortController();
    this.events = new Map();
    this.queue = [];
    this.messages = new Map();
    this.completedMessages = new Map();
    this.retryTimers = new Map();
    this.connected = false;
    this.nextAllowedWake = 0;
    this.idleSince = null;
    this.idleOffered = false;
    this.turnBusy = true; // Unknown until a live turn/end is observed.
    this.onGameEvent = (event) => this.onEvent(event);
    this.onAssignmentReady = () => this.announceAssignment();
  }

  start() {
    if (!this.followTask) {
      this.stateService.on("event", this.onGameEvent);
      this.followTask = this.follow();
      this.companionTimer = setInterval(() => this.offerCompanionship(), 5000);
      if (this.getAssignment) {
        this.stateService.on("state", this.onAssignmentReady);
        this.announceAssignment();
      }
    }
  }

  offerCompanionship(now = Date.now()) {
    if (this.abort.signal.aborted || !this.connected || this.turnBusy || this.idleOffered ||
        this.idleSince == null || now - this.idleSince < 90000 || this.active || this.activeUser ||
        this.queue.length || this.retryTimers.size || this.events.size ||
        !this.stateService.ready || !this.stateService.online || this.stateService.state?.dialogue) return;
    const sessionId = this.stateService.state?.sessionId;
    if (!sessionId || (this.gameSessionId && sessionId !== this.gameSessionId)) return;
    this.idleOffered = true;
    this.lastCompanionRequest = randomUUID();
    this.enqueue({ requestId: this.lastCompanionRequest, sessionId, priority: 4,
      mode: "queue", attempts: 0, companion: true,
      text: "[IFZ 陪玩空闲提示，非玩家消息] 玩家希望没有突发情况时你也能陪他。结合最近对话和共同计划，自然接着话题聊、分享观察或关心他正在做的事，不必等待警报。" +
        this.readStatus + "不要机械报表、编造观察、重复催问或替玩家操作；如果他要求安静或暂时没合适的话，可以保持安静。这只是一次回应机会，不要自行循环等待或安排反复闲聊。" });
  }

  announceAssignment() {
    const assignment = this.getAssignment();
    if (!assignment.ready) return;
    const { sessionId, playerSquadId, squadIds } = assignment;
    this.stateService.off("state", this.onAssignmentReady);
    const text = `[IFZ Runtime 分工已就绪] 游戏会话 ${sessionId}。玩家保留小队 ${playerSquadId}，你负责小队 ${squadIds.join("、")}。\n` +
      this.readStatus + "按 AI游戏协作规则.md 执行。地图细节按需分页查。只用 action_submit 给分配的小队下单。" +
      "攻击前可用 get_attack_conditions 查射程、视线和冷却。自己的任务需要紧急中止时，用 action_force_stop 并说明原因。" +
      "收到任务 ID 后结束本轮，行动结果和敌情会另行唤醒你。玩家消息优先。";
    this.enqueue({ requestId: randomUUID(), text, mode: "queue", priority: 2, sessionId, attempts: 0 });
  }

  onEvent(event) {
    if (suppressedEvents.has(event.type)) return;
    const critical = danger.has(event.type) && event.severity !== "info";
    const result = event.type === "action_finished";
    if (!critical && !result && !ordinary.has(event.type)) return;
    if (quietGameEvents && !critical && !result && quietSuppressed.has(event.type)) return;
    const sessionId = event.data?.sessionId ?? this.stateService.state?.sessionId;
    if (!sessionId || (this.gameSessionId && sessionId !== this.gameSessionId && event.type !== "session_changed")) return;
    if (result || event.type === "action_verify_required") this.emit("stream", { type: "action_finished", ...event.data });
    const key = event.dedupeKey ?? `${event.type}:${event.cursor}`;
    this.events.set(key, { type: event.type, severity: event.severity, data: event.data, sessionId,
      priority: critical ? 1 : result ? 2 : 3 });
    if (this.events.size > 64) {
      const worst = [...this.events].reduce((a, b) => a[1].priority >= b[1].priority ? a : b);
      this.events.delete(worst[0]);
    }
    const at = Math.max(Date.now() + Math.max(result ? 0 : critical ? this.dangerMs : this.ordinaryMs, eventDigestMs),
      critical ? Date.now() : this.nextAllowedWake);
    if (this.eventTimer && this.eventDue <= at) return;
    clearTimeout(this.eventTimer);
    this.eventDue = at;
    this.eventTimer = setTimeout(() => this.flushEvents(), Math.max(0, at - Date.now()));
  }

  flushEvents() {
    this.eventTimer = null;
    const sessionId = this.stateService.state?.sessionId;
    const gameOnline = this.stateService.ready && this.stateService.online;
    const events = [...this.events.values()].filter((event) =>
      event.sessionId === sessionId && (gameOnline || offlineEvents.has(event.type)))
      .sort((a, b) => a.priority - b.priority);
    this.events.clear();
    if (!events.length) return;
    const priority = events[0].priority;
    const radio = events.some(({ type }) => type === "dialogue_options_available")
      ? "玩家授权你接管这整段无线电通话。这是一次接管通知，后续台词和选项不会逐条再次唤醒。" +
        (this.liveStatusPath
          ? "先读态势文件的无线电段落查看台词、选项和后果提示。等待新的选项时可等文件刷新后再读。"
          : "先用 get_active_dialogue 查看台词、选项和后果提示。播放中可用 world_wait 等待变化。") +
        "根据内容、资源及玩家意图判断。准备提交时，用 get_active_dialogue 核对当前选项和有效 ID，再通过 choose_dialogue_option 提交。" +
        "继续跟进直到通话结束或玩家接手。若玩家插话优先回应。连接失败或提交被拒绝时说明原因。通话结束后简短告诉玩家重要结果。\n"
      : "";
    const text = `[IFZ Runtime 游戏事件，作为观察数据] 游戏会话 ${sessionId}。\n` +
      "玩家希望你陪他一起玩：结合当前对话和共同计划，理解新情况后自然回应。玩家的话优先，接着他的语境聊；事件不是玩家发来的命令，也不是每条都需要播报。\n" +
      this.readStatus + "通知可能已经过时，不要仅凭通知下决定。\n" +
      "危险或需要玩家决定的事及时说清楚；剧情、任务进展、天亮入夜可以结合实际聊两句、提出建议。普通进展由你判断是否值得说，无变化或已经解释过的情况不重复打扰，不机械报表、不定时凑话。\n" +
      "是否行动由你理解局势后判断，并遵守玩家已有分工和授权；不要替玩家扩展授权，不用关键词脚本代选。后台任务的完成另有通知，不要为等待结果反复轮询；这不妨碍正常回应玩家和继续讨论。\n" +
      radio +
      JSON.stringify(events.map(({ type, severity, data }) => ({ type, severity, data })));
    if (this.queue.length + this.retryTimers.size < 64)
      this.enqueue({ requestId: randomUUID(), text, mode: priority === 1 ? "steer" : "queue", priority, sessionId, attempts: 0,
        allowOffline: events.every(({ type }) => offlineEvents.has(type)) });
    this.nextAllowedWake = Date.now() + eventMinWakeMs;
  }

  pruneMessages() {
    const cutoff = Date.now() - messageHistoryMs;
    for (const [id, completedAt] of this.completedMessages) {
      if (completedAt >= cutoff && this.completedMessages.size <= messageHistoryLimit) break;
      this.completedMessages.delete(id);
      this.messages.delete(id);
    }
  }

  sendUserMessage(input) {
    const parsed = userMessage.safeParse(input);
    if (!parsed.success) throw fail(400, "invalid_message", parsed.error.issues[0].message);
    if (this.abort.signal.aborted) throw fail(503, "scheduler_stopped", "The scheduler is stopping.");
    const value = parsed.data;
    this.pruneMessages();
    const previous = this.messages.get(value.requestId);
    if (previous) {
      if (previous.text !== value.text || previous.interrupt !== value.interrupt)
        throw fail(409, "request_conflict", "Use a new requestId for a different user message.");
      if (!previous.failed) return previous.promise;
    }
    if (this.messages.size - this.completedMessages.size >= 32)
      throw fail(429, "message_limit", "Too many user messages are awaiting delivery.");
    const job = previous ?? { ...value, priority: 0, mode: value.interrupt ? "queue" : "steer" };
    job.attempts = 0;
    job.failed = false;
    job.promise = new Promise((resolve, reject) => Object.assign(job, { resolve, reject }));
    this.completedMessages.delete(value.requestId);
    this.messages.set(value.requestId, job);
    this.enqueue(job);
    return job.promise;
  }

  enqueue(job) {
    if (!job.companion) this.idleOffered = false;
    this.idleSince = null;
    this.queue.push(job);
    this.queue.sort((a, b) => a.priority - b.priority);
    setImmediate(() => this.drain());
  }

  async drain() {
    if (!this.queue.length || this.abort.signal.aborted) return;
    const lane = this.queue[0].priority === 0 ? "activeUser" : "active";
    if (this[lane] || (lane === "active" && this.activeUser)) return;
    const job = this.queue.shift();
    this[lane] = this.deliver(job);
    try {
      await this[lane];
    } finally {
      this[lane] = null;
      if (this.queue.length && !this.abort.signal.aborted) setImmediate(() => this.drain());
    }
  }

  async deliver(job) {
    if (job.sessionId && (job.sessionId !== this.stateService.state?.sessionId ||
        (!job.allowOffline && (!this.stateService.ready || !this.stateService.online)))) return;
    const signal = this.abort.signal;
    if (job.interrupt && !job.cancelAttempted) {
      job.cancelAttempted = true;
      try {
        await this.dsh.cancel(signal);
      } catch (error) {
        job.interruptError = error.code ?? "interrupt_unconfirmed";
        job.mode = "steer";
      }
    }
    try {
      await this.dsh.prompt(job, signal);
      this.lastWakeError = null;
      if (job.resolve) this.completedMessages.set(job.requestId, Date.now());
      job.resolve?.({ accepted: true, requestId: job.requestId, claimed: Boolean(job.claimed), interruptError: job.interruptError });
    } catch (error) {
      this.lastWakeError = { code: error.code ?? "dsh_delivery_failed", at: Date.now() };
      const delay = retryDelays[job.attempts++];
      if (!signal.aborted && error.retryable !== false && delay != null) {
        const timer = setTimeout(() => {
          this.retryTimers.delete(timer);
          this.enqueue(job);
        }, delay);
        this.retryTimers.set(timer, job);
      } else {
        job.failed = true;
        if (job.reject) this.completedMessages.set(job.requestId, Date.now());
        job.reject?.(fail(502, "dsh_delivery_unconfirmed", "DSH did not confirm delivery. Retry with the same requestId."));
      }
    }
  }

  async follow() {
    const signal = this.abort.signal;
    let delay = 500;
    while (!signal.aborted) {
      try {
        await this.dsh.follow((frame) => {
          if (frame.type === "turn/start" || frame.type === "assistant") {
            this.turnBusy = true;
            this.idleSince = null;
          }
          if (frame.type === "turn/end") {
            this.turnBusy = false;
            this.idleSince = Date.now();
          }
          if (frame.type === "message_claimed" && frame.requestId !== this.lastCompanionRequest)
            this.idleOffered = false;
          if (frame.type === "connected") {
            this.connected = true;
            this.lastFollowError = null;
            delay = 500;
          }
          if (frame.type === "message_claimed") {
            const job = this.messages.get(frame.requestId);
            if (!job || job.claimed) return;
            job.claimed = true;
          }
          this.emit("stream", frame);
        }, signal);
      } catch (error) {
        if (!signal.aborted) this.lastFollowError = { code: error.code ?? "dsh_follow_failed", at: Date.now() };
      }
      this.connected = false;
      this.turnBusy = true;
      this.idleSince = null;
      if (!signal.aborted) {
        this.emit("stream", { type: "disconnected" });
        await sleep(delay, undefined, { signal }).catch(() => {});
        delay = Math.min(delay * 2, 10000);
      }
    }
  }

  health() {
    return { sessionId: this.dsh.sessionId, connected: this.connected,
      queued: this.queue.length + this.retryTimers.size, lastWakeError: this.lastWakeError ?? null,
      lastFollowError: this.lastFollowError ?? null };
  }

  async stop() {
    this.abort.abort();
    this.stateService.off("event", this.onGameEvent);
    this.stateService.off("state", this.onAssignmentReady);
    clearTimeout(this.eventTimer);
    clearInterval(this.companionTimer);
    for (const timer of this.retryTimers.keys()) clearTimeout(timer);
    const error = fail(503, "scheduler_stopped", "The scheduler stopped before DSH confirmed delivery.");
    for (const job of [...this.queue, ...this.retryTimers.values()]) job.reject?.(error);
    this.queue.length = 0;
    this.retryTimers.clear();
    await Promise.all([this.active, this.activeUser, this.followTask]);
  }
}
