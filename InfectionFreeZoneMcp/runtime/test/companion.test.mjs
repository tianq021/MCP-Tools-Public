import test from "node:test";
import assert from "node:assert/strict";
import { Scheduler } from "../scheduler.mjs";

test("空闲陪伴只提供一次机会，不打断忙碌、无线电或离线状态", () => {
  const stateService = { ready: true, online: true, state: { sessionId: "game" } };
  const s = new Scheduler({ stateService, dsh: {}, gameSessionId: "game" });
  const jobs = [];
  s.enqueue = job => jobs.push(job);
  s.connected = true;
  s.idleSince = 0;
  s.offerCompanionship(100000);
  assert.equal(jobs.length, 0);
  s.turnBusy = false;
  s.offerCompanionship(80000);
  assert.equal(jobs.length, 0);
  stateService.state.dialogue = {};
  s.offerCompanionship(100000);
  assert.equal(jobs.length, 0);
  stateService.state.dialogue = null;
  stateService.online = false;
  s.offerCompanionship(100000);
  assert.equal(jobs.length, 0);
  stateService.online = true;
  s.offerCompanionship(100000);
  s.offerCompanionship(300000);
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].companion, true);
});
