import test from "node:test";
import assert from "node:assert/strict";
import { EventEngine } from "../events.mjs";

test("任务新增、目标变化及移除立即报告，旧完成目标和列表重排不误报", () => {
  const engine = new EventEngine();
  const mission = (id, done = false) => ({ id, objectives: [{ id: "goal", completed: done }] });
  const sample = (events) => {
    const output = [];
    engine.detectMissions(null, { events }, (type, severity, dedupeKey, data) => {
      if (engine.dedupe({ type, dedupeKey, at: 100 })) output.push(data);
    });
    return output;
  };
  assert.deepEqual(sample([mission("old", true)]), []);
  assert.deepEqual(sample([mission("old", true), mission("new")])[0].added, ["new"]);
  assert.deepEqual(sample([mission("new"), mission("old", true)]), []);
  assert.deepEqual(sample([mission("new", true), mission("old", true)])[0].updated, ["new"]);
  assert.deepEqual(sample([mission("old", true)])[0].removed, ["new"]);
  assert.deepEqual(sample([mission("old", true)]), []);
  engine.reset();
  assert.deepEqual(sample([mission("fresh")]), []);
});
