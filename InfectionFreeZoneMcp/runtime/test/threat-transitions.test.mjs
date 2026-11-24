import test from "node:test";
import assert from "node:assert/strict";
import { EventEngine } from "../events.mjs";

test("持续敌情不刷屏，升级及解除后重现立即报告", () => {
  const engine = new EventEngine();
  let at = 0;
  const sample = (x, count = 5) => engine.detect(null, {
    at: at += 1000,
    headquarters: { x: 1000, z: 1000 },
    squads: [{ id: "s", position: { x: 0, z: 0 } }],
    groups: [{ id: "g", visible: true, hostile_to_player: true,
      member_count: count, position: { x, z: 0 } }],
  }).filter(e => e.type === "squad_threat");
  assert.equal(sample(40).length, 1);
  for (let i = 0; i < 180; i++) assert.equal(sample(40).length, 0);
  assert.equal(sample(46).length, 0);
  assert.equal(sample(44).length, 0);
  assert.equal(sample(19)[0].data.reason, "escalated");
  assert.equal(sample(30).length, 0);
  assert.equal(sample(19).length, 0);
  assert.equal(sample(19, 10)[0].data.reason, "escalated");
  assert.equal(sample(60).length, 0);
  assert.equal(sample(40)[0].data.reason, "entered");
  engine.reset();
  assert.equal(sample(40).length, 1);
});

test("最近敌群交换不重复提醒，新敌群仍即时通知", () => {
  const engine = new EventEngine();
  const sample = (positions) => engine.detect(null, {
    at: Date.now(), headquarters: { x: 1000, z: 1000 },
    squads: [{ id: "s", position: { x: 0, z: 0 } }],
    groups: positions.map((x, id) => ({ id, visible: true, hostile_to_player: true,
      member_count: 5, position: { x, z: 0 } })),
  }).filter(e => e.type === "squad_threat");
  assert.equal(sample([30, 35]).length, 2);
  assert.equal(sample([35, 30]).length, 0);
  assert.equal(sample([35, 30, 40]).length, 1);
  sample([]);
  assert.equal(sample([35]).length, 1);
});
