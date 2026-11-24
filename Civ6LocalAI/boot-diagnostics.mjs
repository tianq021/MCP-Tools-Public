import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
const root = dirname(fileURLToPath(import.meta.url));
export const name = 'civ6-local-boot-diagnostics';
export function apply(ctx) {
  const timer = setTimeout(async () => {
    const loader = ctx.get('loader');
    if (!loader) return;
    await loader.await();
    const rows = await Promise.all([...loader.entries()].map(async entry => {
      let disabled;
      try { disabled = entry.disabled; } catch { disabled = 'expression_error'; }
      if (disabled) return null;
      const fiber = entry.fiber;
      let error = null;
      if (fiber) {
        error = await Promise.race([
          fiber.await().then(() => null, e => String(e)),
          new Promise(resolve => setTimeout(() => resolve('pending'), 30))
        ]);
      }
      return { id: entry.id, state: fiber?.state, error,
        missing: fiber ? Object.keys(fiber.inject || {}).filter(key => fiber.ctx.get(key) === undefined) : [] };
    }));
    mkdirSync(join(root, 'evidence'), { recursive: true });
    writeFileSync(join(root, 'evidence', 'dsh-boot-status.json'), JSON.stringify(rows.filter(Boolean), null, 2));
    writeFileSync(join(root, 'evidence', 'dsh-tools.json'), JSON.stringify({
      names: [...ctx.get('tools').view().visible.keys()],
      defaultModel: ctx.get('agentDefaultModel').currentSelection(),
      providers: ctx.get('llm').listProviders().map(p => p.id)
    }, null, 2));
  }, 5000);
  timer.unref();
  ctx.on('dispose', () => clearTimeout(timer));
}
