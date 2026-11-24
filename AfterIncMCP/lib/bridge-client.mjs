import { randomUUID } from 'node:crypto';

export class BridgeClient {
  constructor({ endpoint = process.env.INCMCP_ENDPOINT || 'http://127.0.0.1:17840/v1/call', clientId = randomUUID(), timeout = 28000 } = {}) {
    const url = new URL(endpoint);
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.pathname !== '/v1/call' || url.username || url.password || url.search || url.hash) {
      throw new Error('The bridge must be a loopback http://127.0.0.1:PORT/v1/call endpoint.');
    }
    this.endpoint = url.href;
    this.clientId = clientId;
    this.timeout = timeout;
  }

  async call(command, args = {}, requestId = randomUUID()) {
    try {
      const response = await fetch(this.endpoint, {
        method: 'POST', redirect: 'error',
        headers: { 'Content-Type': 'application/json', 'X-IncMcp': '1' },
        body: JSON.stringify({ command, args, request_id: requestId, client_id: this.clientId }),
        signal: AbortSignal.timeout(this.timeout),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      if (typeof result?.ok !== 'boolean') throw new Error('Invalid bridge response');
      return { ...result, request_id: requestId };
    } catch (error) {
      // Never retry a mutation automatically: it may already have been applied.
      return { ok: false, applied: null, request_id: requestId,
        error: { code: 'BRIDGE_UNREACHABLE', message: `Game bridge unavailable or response lost (${error.message}). Start the game with AfterIncBridge. For mutations, retry only with this same request_id from this same client.` } };
    }
  }
}
