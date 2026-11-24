import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { LiveFiles } from "../live-files.mjs";
import { StateService } from "../state-service.mjs";
import { FakeBridge } from "./fake-bridge.mjs";

test("live files summarize cached state, retain short radio changes and label stale data", async (t) => {
  const local = new URL("../../.local/", import.meta.url);
  await mkdir(local, { recursive: true });
  const directory = await mkdtemp(fileURLToPath(new URL("live-files-test-", local)));
  const bridge = new FakeBridge();
  const stateService = new StateService({ pipe: bridge });
  const errors = [];
  const files = new LiveFiles({ stateService, directory, logger: (message) => errors.push(message) });
  t.after(async () => {
    await files.stop();
    await rm(directory, { recursive: true, force: true });
  });
  const snapshot = async () => {
    await files.writing;
    files.tick();
    await files.writing;
    assert.deepEqual(errors, []);
    return readFile(files.currentPath, "utf8");
  };
  files.start();
  assert.match(await snapshot(), /等待首次游戏采样/);

  bridge.world.squads[1].vehicle_id = "781";
  bridge.baseState.dialogue = {
    transmission_id: "radio-a",
    text: "这里是无线电。",
    choice_id: "choice-a",
    options: [{ text: "电台测试选项", selectable: true, effect_hints: [{ keys: ["res_cans"], value: 12 }] }],
  };
  await stateService.pollOnce();
  const calls = bridge.calls.length;
  const current = await snapshot();
  assert.equal(bridge.calls.length, calls);
  assert.match(current, /这里是无线电[\s\S]*电台测试选项，游戏提示 罐头 12/);
  assert.match(current, /\| 罐头 \| 120 \| 110 \|/);
  assert.match(current, /小队（3）[\s\S]*MoveToBuildingOrder[\s\S]*油量 90%[\s\S]*血量 100\/100/);
  assert.match(current, /敌群 g1/);
  assert.doesNotMatch(current, /敌群 g3|\[object Object\]/);

  bridge.baseState.dialogue = { ...bridge.baseState.dialogue, choice_id: "choice-b",
    text: "再见。", options: [{ text: "告别", selectable: true }] };
  await stateService.pollOnce();
  bridge.baseState.dialogue = null;
  await stateService.pollOnce();
  assert.match(await snapshot(), /最近一通已结束的通话[\s\S]*再见。/);

  bridge.goOffline();
  await assert.rejects(stateService.pollOnce(), { code: "bridge_offline" });
  assert.match(await snapshot(), /游戏桥接离线，以下为上次缓存，已过期/);
  bridge.goOnline();
  bridge.setSession("next-session");
  await stateService.pollOnce();
  const next = await snapshot();
  assert.match(next, /游戏会话 next-session/);
  assert.doesNotMatch(next, /再见。|游戏桥接断开/);

  const events = (await readFile(files.eventPath, "utf8")).trim().split("\n").map(JSON.parse);
  assert.deepEqual(events.map(({ type }) => type),
    ["dialogue_options_available", "radio_options_changed", "radio_options_changed", "radio_ended",
      "bridge_offline", "session_changed", "bridge_restored"]);
  assert.equal(events[1].data.dialogue.text, "这里是无线电。");
  assert.equal(events[2].data.dialogue.options[0].text, "告别");
  assert.deepEqual(events[3].data.observed_lines, ["这里是无线电。", "再见。"]);
  assert.deepEqual(errors, []);
});
