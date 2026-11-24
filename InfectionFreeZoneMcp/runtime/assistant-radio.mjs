import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const exec = promisify(execFile);
const workspace = fileURLToPath(new URL("../", import.meta.url));
const prompt = "玩家在《无感染区》无线电呼叫你。请用中文简短应答，根据附带的游戏快照给一条当前建议，最多 80 字。只回应本次求助，无需执行游戏操作。";

async function callCodex(message, sessionId) {
  const args = ["exec", "--sandbox", "read-only", "--color", "never"];
  if (sessionId) args.push("resume", sessionId);
  else args.push("--ephemeral");
  const { stdout } = await exec(process.platform === "win32" ? "codex.exe" : "codex",
    [...args, message],
    { cwd: workspace, timeout: 120000, windowsHide: true, maxBuffer: 4 * 1024 * 1024 });
  if (!stdout.trim()) throw new Error("Codex returned an empty reply.");
  return stdout.trim().replace(/\s+/gu, " ").slice(0, 480);
}

export function createRadioReceiver({ pipe, scheduler, gameSessionId, codexSessionId, codex = callCodex, logger = console.error }) {
  const receiving = new Set();
  return async ({ id, target }, state) => {
    if (!["Codex", "DSH"].includes(target) || receiving.has(id) ||
        (gameSessionId && gameSessionId !== state.sessionId)) return;
    receiving.add(id);
    const args = { session_id: state.sessionId, request_id: id };
    try {
      const claim = await pipe.call("claim_assistant_call", args);
      if (!claim.ok) return;
      const summary = { day: state.time?.day, hour: state.time?.hour, squads: state.squads.length,
        missions: state.events.slice(0, 4).map(m => String(m.title ?? m.id).slice(0, 100)) };
      const message = prompt + "\n" + JSON.stringify(summary);
      let text;
      try {
        if (target === "Codex") text = await codex(message, codexSessionId);
        else if (!scheduler) text = "本局 Runtime 尚未绑定 DSH 陪玩会话。";
        else {
          await scheduler.sendUserMessage({ requestId: id, text: message });
          text = "呼叫已送达，请在 DSH 陪玩聊天中查看回复。";
        }
      } catch (error) {
        logger(target + " " + (error.code ?? error.name));
        text = target === "DSH" ? "未能确认送达，请检查 DSH 会话。" : "未取得回复，请检查本机 Codex CLI 和登录状态。";
      }
      const reply = await pipe.call("finish_assistant_call", { ...args, text });
      if (!reply.ok) logger(reply.code ?? "radio_reply_failed");
    } catch (error) {
      logger(error.code ?? error.message);
    } finally {
      receiving.delete(id);
    }
  };
}
