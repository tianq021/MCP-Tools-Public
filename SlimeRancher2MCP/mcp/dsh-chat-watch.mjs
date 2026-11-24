import { closeSync, fstatSync, openSync, readSync, readdirSync, statSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'sr2-chat-watch';
export const inject = ['jobs', 'tools'];
const ranchScript = fileURLToPath(new URL('../tool/ranch.py', import.meta.url));

// The journal rotates by rename. Incomplete final lines become visible on the next tick.
function inboxUpdate(directory, cursor) {
    let names;
    try { names = readdirSync(directory); }
    catch (error) { if (error.code === 'ENOENT') return { end: 0 }; throw error; }
    const archives = names.filter(n => /^inbox\.\d{8}_\d{6}(?:_\d+)?\.jsonl$/.test(n));
    let bytes = 0;
    for (const archive of archives) bytes += statSync(join(directory, archive)).size;
    if (!names.includes('inbox.jsonl')) return { end: bytes };
    const fd = openSync(join(directory, 'inbox.jsonl'), 'r');
    try {
        let end = fstatSync(fd).size;
        const tail = Buffer.alloc(4096);
        while (end > 0) {
            const start = Math.max(0, end - tail.length);
            const n = readSync(fd, tail, 0, end - start, start);
            const last = tail.subarray(0, n).lastIndexOf(10);
            if (last >= 0) {
                const end = bytes + start + last + 1;
                const local = cursor - bytes - start;
                if (local >= 0 && local <= last && end - cursor <= 2048) {
                    // Only a single, complete, small message is carried in the notification.
                    try {
                        const record = JSON.parse(tail.toString('utf8', local, last + 1));
                        if (typeof record?.text === 'string' && record.text.length > 0 && record.text.length <= 400)
                            return { end, message: record.text };
                    } catch (error) {
                        if (!(error instanceof SyntaxError)) throw error;
                    }
                }
                return { end };
            }
            end = start;
        }
        return { end: bytes };
    } finally { closeSync(fd); }
}

export function inboxEnd(directory) { return inboxUpdate(directory).end; }

export function apply(ctx) {
    const config = fileURLToPath(new URL('../config/bridge.json', import.meta.url));
    const log = JSON.parse(readFileSync(config, 'utf8').replace(/^\uFEFF/, '')).log_path;
    installWatch(ctx, join(dirname(dirname(log)), 'chat'));
}

export function installWatch(ctx, directory) {
    const states = new Map();

    function arm(owner, state) {
        state.job = ctx.jobs.start({
            kind: 'sr2chat', owner, label: '等待游戏内玩家消息',
            run() {
                let finish;
                const done = new Promise(resolve => { finish = resolve; });
                const timer = setInterval(() => {
                    let update;
                    try { update = inboxUpdate(directory, state.cursor); }
                    catch (e) {
                        if (e.code === 'ENOENT') return; // file is being renamed
                        clearInterval(timer);
                        finish({ status: 'failed', detail: String(e) });
                        return;
                    }
                    if (update.end <= state.cursor) return;
                    const cursor = state.cursor;
                    state.cursor = update.end;
                    clearInterval(timer);
                    const source = update.message === undefined
                        ? '用 sr2_chat_history 从 cursor=' + cursor + ' 补读玩家消息。'
                        : '游戏内玩家消息 cursor=' + cursor + ' ' + JSON.stringify(update.message);
                    const detail = source + '\n动作明确时直接运行 python -B "' + ranchScript
                        + '" <子命令> [参数] --reply。操作包括 heal、energy、slot、feed、craft、store、take、store-all、take-all、'
                        + 'transfer、consolidate、plorts、tidy、refine、refine-bag、'
                        + 'start-plort-collector、start-resource-collector、silo-next、feeder-speed、collect、pin-recipe、clear-recipe、save。'
                        + '默认预演，执行加 --apply <该消息的稳定请求ID>。需要查状态再判断时用 MCP，快捷命令参数可查 --help。成功回信后无需再发。';
                    finish({ status: 'completed', detail, result: detail });
                }, 1000);
                timer.unref();
                return { done, cancel() { clearInterval(timer); finish({ status: 'killed' }); } };
            }
        });
    }

    ctx.on('tools/pre-execute', (exec, next) => {
        const owner = exec.agent?.id;
        if (exec.name === 'mcp__slime_rancher2__sr2_chat_pending' && owner && !states.has(owner)) {
            try {
                const state = { cursor: inboxEnd(directory) };
                arm(owner, state);
                states.set(owner, state);
            } catch (e) { ctx.logger.warn('SR2 chat watcher: ' + String(e)); }
        }
        return next();
    });
    ctx.jobs.events.subscribe({ owners: 'scope' }, event => {
        if (event.type !== 'settled') return;
        const snapshot = event.job;
        const owner = snapshot.owner;
        const state = states.get(owner);
        if (!state || snapshot.id !== state.job) return;
        if (snapshot.status !== 'completed' || event.cause === 'teardown') { states.delete(owner); return; }
        try { arm(owner, state); }
        catch (e) { states.delete(owner); ctx.logger.warn('SR2 chat watcher: ' + String(e)); }
    });
    ctx.effect(() => () => {
        for (const [owner, state] of states) {
            states.delete(owner);
            try { ctx.jobs.kill(state.job, owner, 'SR2 chat watcher unloaded'); } catch {}
        }
    });
}

