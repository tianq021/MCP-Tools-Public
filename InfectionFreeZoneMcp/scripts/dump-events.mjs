// 从游戏 resources.assets 中提取内嵌的事件/触发器/动作/条件/任务 JSON 定义。
// 做法：单次扫描所有"行首 { 换行" 的顶层对象，字符串感知配对，JSON.parse 后按字段分类。
// 只读游戏文件，输出到 .local/events-dump。
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const ASSETS =
  "F:\\SteamLibrary\\steamapps\\common\\Infection Free Zone\\Infection Free Zone_Data\\resources.assets";
const OUT = "E:\\InfectionFreeZoneMcp\\.local\\events-dump";

console.log("[read] loading assets...");
const buf = readFileSync(ASSETS);
console.log(`[read] ${(buf.length / 1024 / 1024).toFixed(0)} MB`);
const text = buf.toString("latin1");
buf.fill(0);
const n = text.length;
console.log(`[decode] len=${n}`);

// 顶层 JSON 对象的特征：`{` 换行后紧跟两个空格 + 引号（游戏内 JSON 以 2 空格缩进）
const TOP_OBJECT = '{\n  "';

function findObjectEnd(start) {
  let depth = 0;
  let i = start;
  let inString = false;
  let escape = false;
  for (; i < n; i++) {
    const c = text.charCodeAt(i);
    if (inString) {
      if (escape) escape = false;
      else if (c === 92) escape = true; // backslash
      else if (c === 34) inString = false; // quote
      continue;
    }
    if (c === 34) inString = true;
    else if (c === 123) depth++;
    else if (c === 125) {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
}

const buckets = { events: [], triggers: [], actions: [], conditions: [], missions: [] };
let candidates = 0;
let parsed = 0;
let bad = 0;
let pos = 0;

while (pos < n) {
  const i = text.indexOf(TOP_OBJECT, pos);
  if (i < 0) break;
  candidates++;
  const end = findObjectEnd(i);
  if (end < 0) {
    pos = i + 1;
    continue;
  }
  const raw = text.slice(i, end + 1);
  let obj;
  try {
    obj = JSON.parse(raw);
  } catch {
    bad++;
    pos = i + 1;
    continue;
  }
  parsed++;
  pos = end + 1;
  if (obj && typeof obj === "object") {
    if ("actionsOnStartIds" in obj || "conditionsExpressions" in obj) buckets.events.push(obj);
    else if ("triggerType" in obj) buckets.triggers.push(obj);
    else if ("actionType" in obj) buckets.actions.push(obj);
    else if ("conditionType" in obj) buckets.conditions.push(obj);
    else if ("objectiveIds" in obj || "ObjectiveExpressions" in obj) buckets.missions.push(obj);
  }
  if (parsed % 500 === 0) console.log(`[scan] pos=${(pos / 1e6).toFixed(0)}M candidates=${candidates} parsed=${parsed} bad=${bad}`);
}

console.log(`[scan] candidates=${candidates} parsed=${parsed} bad=${bad}`);
const summary = {};
for (const [dir, list] of Object.entries(buckets)) {
  const target = join(OUT, dir);
  mkdirSync(target, { recursive: true });
  let count = 0;
  for (const obj of list) {
    const id =
      obj.name || obj.Id || obj.id || obj.actionType || obj.triggerType || obj.conditionType || `obj_${count}`;
    const safe = String(id).replace(/[^\w.\-]/g, "_");
    writeFileSync(join(target, `${safe}.json`), JSON.stringify(obj, null, 2), "utf8");
    count++;
  }
  summary[dir] = count;
}
writeFileSync(join(OUT, "summary.json"), JSON.stringify(summary, null, 2), "utf8");
console.log("[done]", JSON.stringify(summary));
