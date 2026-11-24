// Runtime entry point: state service, optional action queue and optional DSH scheduling.
// Usage: node runtime/runtime.mjs [--steam] [--port 8790] [--buildings]
import { createPipeClient } from "./pipe-client.mjs";
import { StateService } from "./state-service.mjs";
import { createHub } from "./hub.mjs";
import { ExecService } from "./exec-service.mjs";
import { Scheduler } from "./scheduler.mjs";
import { DshClient } from "./dsh-client.mjs";
import { runtimeToken } from "./token.mjs";
import { createDialogueChooser } from "./dialogue.mjs";
import { LiveFiles } from "./live-files.mjs";
import { createRadioReceiver } from "./assistant-radio.mjs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { z } from "zod";

const args = process.argv.slice(2);
const hasFlag = (name) => args.includes(name);
const option = (name, fallback) => {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] != null ? args[index + 1] : fallback;
};

const steam = hasFlag("--steam");
const includeBuildings = hasFlag("--buildings");
const port = Number(option("--port", "8790"));
const fastIntervalMs = Number(option("--poll-ms", "500"));
const gameSessionId = option("--game-session");
const playerSquadId = option("--player-squad");
const squadIds = option("--squads")?.split(",").map((id) => id.trim()).filter(Boolean) ?? [];
const dshSessionId = option("--dsh-session");
const codexSessionId = option("--codex-session");
const dshHome = option("--dsh-home", process.env.DSH_HOME);
const enableActions = hasFlag("--enable-actions");
const enableDialogueChoices = enableActions || hasFlag("--enable-dialogue-choices");
const uuid = z.string().uuid();
if (codexSessionId !== undefined && !uuid.safeParse(codexSessionId).success)
  throw new Error("--codex-session requires a Codex chat UUID.");
if (enableActions && (!uuid.safeParse(gameSessionId).success || !playerSquadId || !squadIds.length || squadIds.includes(playerSquadId)))
  throw new Error("--enable-actions requires --game-session UUID, --player-squad ID and --squads ID,ID excluding the player.");
if (dshSessionId && (!uuid.safeParse(dshSessionId).success || !uuid.safeParse(gameSessionId).success || !dshHome))
  throw new Error("--dsh-session requires a DSH session UUID, --game-session UUID and --dsh-home or DSH_HOME.");

const log = (event, detail = {}) => console.log(JSON.stringify({ at: new Date().toISOString(), event, ...detail }));

const pipe = createPipeClient({ steam });
const service = new StateService({
  pipe,
  config: { fastIntervalMs, includeBuildings },
  logger: { error: (message) => log("state_error", { message }) },
});
const defaultLiveDirectory = fileURLToPath(new URL(steam ? "../.local/live/" : "../.local/live-test/", import.meta.url));
const liveFiles = new LiveFiles({
  stateService: service,
  directory: resolve(option("--live-dir", defaultLiveDirectory)),
  logger: (message) => log("live_files_error", { message }),
});
const executor = enableActions ? new ExecService({ pipe, stateService: service, sessionId: gameSessionId, playerSquadId, squadIds }) : null;
const scheduler = dshSessionId ? new Scheduler({
  stateService: service, gameSessionId,
  liveStatusPath: liveFiles.currentPath,
  dsh: new DshClient({ home: dshHome, sessionId: dshSessionId, url: option("--dsh-url") }),
  getAssignment: executor ? () => executor.assignment() : undefined,
}) : null;
const requireToken = hasFlag("--require-token");
service.on("assistant_call", createRadioReceiver({ pipe, scheduler, gameSessionId, codexSessionId,
  logger: (message) => log("assistant_radio_error", { message }) }));
const chooseDialogue = enableDialogueChoices ? createDialogueChooser({ pipe, stateService: service, gameSessionId }) : null;
const token = executor || scheduler || chooseDialogue || requireToken ? runtimeToken({ create: true }) : undefined;
const hub = createHub({ stateService: service, executor, scheduler, chooseDialogue, port, token, requireToken });

service.on("event", (event) => {
  if (["bridge_offline", "bridge_restored", "bridge_error", "session_changed"].includes(event.type)) {
    log("event", { type: event.type, severity: event.severity, cursor: event.cursor, code: event.data?.code });
  }
});

const boundPort = await hub.listen();
liveFiles.start();
scheduler?.start();
await service.start();
log("runtime_started", {
  url: `http://127.0.0.1:${boundPort}`,
  steam,
  includeBuildings,
  executorEnabled: Boolean(executor),
  dshEnabled: Boolean(scheduler),
  fastIntervalMs,
  liveStatusPath: liveFiles.currentPath,
  health: service.health(),
});

let stopping = false;
const stop = async () => {
  if (stopping) return;
  stopping = true;
  log("runtime_stopping");
  service.stop();
  await Promise.allSettled([scheduler?.stop(), executor?.stop(), liveFiles.stop(), hub.close()]);
  process.exit(0);
};
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
