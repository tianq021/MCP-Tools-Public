import { randomUUID } from 'node:crypto';

const point = (value) => Number.isFinite(value?.x) && Number.isFinite(value?.z);
const distance = (a, b) => Math.hypot(a.x - b.x, a.z - b.z);
const wait = (reason) => ({ type: 'wait', reason });
const attention = (reason, key = reason) => ({ type: 'attention', reason, key });
const order = (command, params, notice) => ({ type: 'order', command, params, notice });
const squadById = (state, id) => state.squads?.find((squad) => String(squad.id) === id);
const inProgress = new Set(['queued', 'preparing', 'submitting', 'watching']);

function cargoCount(cargo) {
  if (!cargo || typeof cargo !== 'object' || Array.isArray(cargo)) return null;
  const counts = Object.values(cargo);
  if (counts.some((value) => !Number.isFinite(value) || value < 0)) return null;
  return counts.reduce((total, count) => total + count, 0);
}

function carriedCargo(state, squad) {
  const carried = cargoCount(squad.cargo);
  if (!squad.vehicle_id || carried == null) return carried;
  const vehicle = state.vehicles?.find((item) => String(item.id) === String(squad.vehicle_id));
  const trunk = cargoCount(vehicle?.trunk?.cargo);
  return trunk == null ? null : carried + trunk;
}

function assignmentReady(state, assignment) {
  return Boolean(state?.ready && state.sessionId === assignment.sessionId &&
    state.headquartersId === assignment.headquartersId &&
    assignment.squadId !== assignment.playerSquadId && squadById(state, assignment.playerSquadId) &&
    squadById(state, assignment.squadId));
}

export function planNext(state, assignment, { targets, avoided = new Set() } = {}) {
  if (!assignmentReady(state, assignment)) return wait('assignment_unavailable');
  const squad = squadById(state, assignment.squadId);
  if (state.dialogue?.choice_id) return attention('radio_choice', `radio:${state.dialogue.choice_id}`);
  if (state.focused !== true || state.time?.paused !== false || state.radioPaused)
    return wait('game_unavailable');
  if (!point(squad.position) || !Array.isArray(squad.queued_orders) ||
      !Array.isArray(squad.members) || !squad.members.length ||
      squad.members.some((member) => !Number.isFinite(member.hp)) ||
      !Array.isArray(state.groups) || !Array.isArray(state.virtualGroups)) return wait('incomplete_squad_state');

  const threats = [...state.groups, ...state.virtualGroups].filter((group) =>
    group.visible === true && point(group.position) &&
    (group.affiliation_to_player === 'Hostile' ||
      (group.affiliation_to_player == null && group.hostile_to_player === true)));
  const nearbyThreat = threats.find((group) => distance(group.position, squad.position) <= 55);
  if (nearbyThreat) return attention('hostile_near_squad', `hostile:${nearbyThreat.id}`);
  if (squad.members.some((member) => member.hp < 60)) return attention('low_health');
  const summary = state.game?.playerSquads?.find((item) => String(item.id) === assignment.squadId);
  if (squad.active_order || squad.queued_orders.length || summary?.moving === true) return wait('existing_order');

  const cargo = carriedCargo(state, squad);
  if (cargo == null) return wait('incomplete_cargo_state');
  if (cargo > 0) {
    return order('return_to_stockroom', { stockroom_id: assignment.headquartersId }, '本地脚本回总部卸货');
  }
  if (targets === undefined) return { type: 'query_targets', position: squad.position };
  const target = targets.filter((item) => {
    const id = String(item?.id ?? '');
    return id && id !== assignment.headquartersId && !avoided.has(id) && point(item.position) &&
      distance(item.position, squad.position) <= 200 &&
      threats.every((group) => distance(group.position, item.position) > 55);
  }).sort((a, b) => distance(a.position, squad.position) - distance(b.position, squad.position))[0];
  return target
    ? order('scavenge_building', { building_id: String(target.id) }, '本地脚本搜刮附近建筑')
    : wait('no_safe_target');
}

// The executor is the existing IFZ Runtime ExecService interface. The caller owns
// the world connection and reports explicit exceptions to the player or a model.
export class SquadAutopilot {
  constructor({ assignment, readState, readTargets, executor, onException, maxNotifications = 5 }) {
    const keys = ['sessionId', 'headquartersId', 'squadId', 'playerSquadId'];
    if (!keys.every((key) => typeof assignment?.[key] === 'string' && assignment[key]) ||
        assignment.squadId === assignment.playerSquadId) throw new TypeError('需要指定当前会话、总部和两支不同的小队。');
    if (!Number.isInteger(maxNotifications) || maxNotifications < 0) throw new TypeError('无效的通知上限。');
    this.assignment = { ...assignment };
    this.readState = readState;
    this.readTargets = readTargets;
    this.executor = executor;
    this.onException = onException;
    this.maxNotifications = maxNotifications;
    this.notified = new Set();
    this.avoided = new Set();
    this.failures = 0;
  }

  async tick() {
    if (this.running) return wait('decision_in_progress');
    this.running = true;
    try {
      return await this.step();
    } finally {
      this.running = false;
    }
  }

  async step() {
    const state = this.readState();
    if (!assignmentReady(state, this.assignment)) return wait('assignment_unavailable');
    if (this.blocked) return this.report(this.blocked);
    if (this.pending) {
      const { requestId, decision, cargoBefore } = this.pending;
      let task;
      try {
        task = await this.executor.get(requestId);
      } catch {
        return this.report(attention('order_unconfirmed', `order:${requestId}`), { requestId });
      }
      if (task?.state === 'unknown' || !task?.state)
        return this.report(attention('order_unconfirmed', `order:${requestId}`), { requestId });
      if (inProgress.has(task.state)) {
        const situation = planNext(state, this.assignment);
        return situation.type === 'attention' ? this.report(situation) : wait('awaiting_order_receipt');
      }
      if (!['succeeded', 'failed', 'canceled'].includes(task.state))
        return this.report(attention('order_unconfirmed', `order:${requestId}`), { requestId });
      const observed = this.readState();
      if (!assignmentReady(observed, this.assignment)) return wait('assignment_unavailable');
      this.pending = null;
      if (task.state === 'canceled') {
        this.blocked = attention('order_canceled');
        return this.report(this.blocked);
      }
      if (task.state === 'failed') {
        this.failures++;
        if (decision.command === 'scavenge_building') this.avoided.add(decision.params.building_id);
        if (this.failures >= 2) {
          this.blocked = attention('repeated_failure');
          return this.report(this.blocked, { code: task.error?.code });
        }
      } else {
        this.failures = 0;
        if (decision.command === 'return_to_stockroom') {
          const remaining = carriedCargo(observed, squadById(observed, this.assignment.squadId));
          if (remaining == null || remaining >= cargoBefore) {
            this.blocked = attention('unload_unconfirmed');
            return this.report(this.blocked);
          }
        }
      }
      return wait('order_receipt_processed');
    }

    let current = state;
    let decision = planNext(current, this.assignment);
    if (decision.type === 'query_targets') {
      const oldPosition = decision.position;
      let targets;
      try {
        targets = await this.readTargets(oldPosition, this.assignment.sessionId);
      } catch {
        return wait('map_unavailable');
      }
      if (!Array.isArray(targets)) return wait('map_unavailable');
      current = this.readState();
      const newPosition = squadById(current ?? {}, this.assignment.squadId)?.position;
      if (!point(newPosition) || distance(oldPosition, newPosition) > 5) return wait('map_query_stale');
      decision = planNext(current, this.assignment, { targets, avoided: this.avoided });
    }
    if (decision.type === 'attention') return this.report(decision);
    if (decision.type !== 'order') return decision;

    const { sessionId, squadId } = this.assignment;
    const requestId = randomUUID();
    this.pending = { requestId, decision, cargoBefore: carriedCargo(current, squadById(current, squadId)) };
    const { command, params, notice } = decision;
    try {
      await this.executor.submit({ requestId, sessionId, squadId, command, params, notice });
    } catch {
      return this.report(attention('order_unconfirmed', `order:${requestId}`), { requestId });
    }
    return { ...decision, requestId };
  }

  async report(decision, detail = {}) {
    if (!this.onException || this.notified.has(decision.key) || this.notified.size >= this.maxNotifications)
      return { ...decision, notified: false };
    const state = this.readState();
    if (!assignmentReady(state, this.assignment)) return wait('assignment_unavailable');
    const squad = squadById(state, this.assignment.squadId);
    this.notified.add(decision.key);
    try {
      await this.onException({ sessionId: this.assignment.sessionId, squadId: this.assignment.squadId,
        reason: decision.reason, position: squad.position, activeOrder: squad.active_order ?? null,
        choiceId: state.dialogue?.choice_id ?? null, ...detail });
      return { ...decision, notified: true };
    } catch {
      return { ...decision, notified: false, notificationFailed: true };
    }
  }
}
