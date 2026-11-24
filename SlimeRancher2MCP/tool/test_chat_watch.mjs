import assert from 'node:assert/strict';
import { appendFileSync, mkdtempSync, renameSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { inboxEnd, installWatch } from '../mcp/dsh-chat-watch.mjs';

const dir = mkdtempSync(join(tmpdir(), 'sr2-chat-test-'));
const inbox = join(dir, 'inbox.jsonl');
let startPoll, onDone, hooks, dispose;
let jobs = 0;
const owner = { id: 'local-test' };
const ctx = {
    logger: console, on(_, fn) { startPoll = fn; }, effect(fn) { dispose = fn(); },
    jobs: {
        start(spec) { jobs++; hooks = spec.run(); return 'sr2chat-' + jobs; },
        events: { subscribe(_, fn) { onDone = fn; } }, kill() { hooks.cancel(); }
    }
};
try {
    installWatch(ctx, dir);
    const poll = () => startPoll({name:'mcp__slime_rancher2__sr2_chat_pending', agent:owner}, () => {});
    poll(); poll();
    assert.equal(jobs, 1);
    appendFileSync(inbox, '{"text":"甲"}');
    assert.equal(inboxEnd(dir), 0);
    appendFileSync(inbox, '\n');
    const first = inboxEnd(dir);
    const timeout = ms => new Promise((_, reject) => {
        const timer = setTimeout(() => reject(new Error('watch timed out')), ms);
        timer.unref();
    });
    // Keep the test alive across unref'ed production timers.
    const keepAlive = setInterval(() => {}, 100);
    try {
        const firstResult = await Promise.race([hooks.done, timeout(4000)]);
        assert.match(firstResult.result, /cursor=0/);
        assert.match(firstResult.result, /"甲"/);
        assert.match(firstResult.result, /ranch\.py/);
        renameSync(inbox, join(dir, 'inbox.20261121_120000.jsonl'));
        appendFileSync(inbox, '{"text":"乙"}\n');
        onDone({type:'settled', job:{id:'sr2chat-1', status:'completed', owner:owner.id}});
        const second = await Promise.race([hooks.done, timeout(4000)]);
        assert.ok(second.result.includes('cursor=' + first + ' '));
        assert.match(second.result, /"乙"/);
        assert.equal(jobs, 2);
        appendFileSync(inbox, '{"text":"丙"}\n{"text":"丁"}\n');
        onDone({type:'settled', job:{id:'sr2chat-2', status:'completed', owner:owner.id}});
        const batch = await Promise.race([hooks.done, timeout(4000)]);
        assert.match(batch.result, /sr2_chat_history/);
    } finally { clearInterval(keepAlive); }
    console.log('PASS duplicate polling, partial record, rotation and automatic rearm');
} finally {
    dispose?.();
    rmSync(dir, {recursive:true, force:true});
}

