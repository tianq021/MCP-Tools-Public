import assert from "node:assert/strict";
import test from "node:test";
import { createRadioReceiver } from "../assistant-radio.mjs";

const state = { sessionId: "game", time: { day: 2 }, squads: [], events: [] };
const call = { id: "request", target: "Codex" };

test("one player click dispatches once despite overlapping snapshots", async () => {
  const writes = [];
  const pipe = { async call(method, args) { writes.push([method, args]); return { ok: true }; } };
  let prompts = 0;
  const receive = createRadioReceiver({ pipe, codex: async () => { prompts++; return "收到。"; } });
  await Promise.all([receive(call, state), receive(call, state)]);
  assert.equal(prompts, 1);
  assert.deepEqual(writes, [
    ["claim_assistant_call", { session_id: "game", request_id: "request" }],
    ["finish_assistant_call", { session_id: "game", request_id: "request", text: "收到。" }],
  ]);
});

test("routes the DSH request to the bound session only after a successful claim", async () => {
  const messages = [];
  let claim = false;
  const receive = createRadioReceiver({ gameSessionId: "game",
    pipe: { async call() { return { ok: claim }; } },
    scheduler: { async sendUserMessage(message) { messages.push(message); } } });
  const request = { ...call, target: "DSH" };
  await receive(request, state);
  claim = true;
  await receive(request, { ...state, sessionId: "different" });
  await receive(request, state);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].requestId, call.id);
});
