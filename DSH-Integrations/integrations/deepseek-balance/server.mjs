import { readFile } from 'node:fs/promises';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { parse } from 'yaml';

const credentialsFile = new URL('../../dsh-home-020-20261007/.credentials.yaml', import.meta.url);
const balanceUrl = 'https://api.deepseek.com/user/balance';
const amountFields = ['total_balance', 'granted_balance', 'topped_up_balance'];

async function readApiKey() {
  let credentials;
  try {
    credentials = parse(await readFile(credentialsFile, 'utf8'));
  } catch {
    throw new Error('无法读取 DSH 的 DeepSeek 凭据配置。');
  }

  const key = credentials?.refs?.DEEPSEEK_API_KEY;
  if (typeof key !== 'string' || !/^sk-[A-Za-z0-9_-]+$/.test(key.trim())) {
    throw new Error('DSH 的 DeepSeek API Key 缺失或格式无效。');
  }
  return key.trim();
}

async function getBalance(signal) {
  const key = await readApiKey();
  let response;
  try {
    response = await fetch(balanceUrl, {
      headers: { Accept: 'application/json', Authorization: `Bearer ${key}` },
      redirect: 'error',
      signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
    });
  } catch {
    throw new Error('连接 DeepSeek 官方余额接口失败或超时。');
  }
  if (response.status === 401 || response.status === 403) {
    throw new Error(`DeepSeek 拒绝当前 API Key（HTTP ${response.status}）。`);
  }
  if (!response.ok) {
    throw new Error(`DeepSeek 余额查询失败（HTTP ${response.status}）。`);
  }

  const data = await response.json().catch(() => null);
  if (typeof data?.is_available !== 'boolean' || !Array.isArray(data.balance_infos)) {
    throw new Error('DeepSeek 余额接口返回的格式有误。');
  }

  const balances = data.balance_infos.map(info => {
    if (typeof info?.currency !== 'string' || !/^[A-Z]{3}$/.test(info.currency) ||
        !amountFields.every(field => typeof info[field] === 'string' && /^-?\d+(\.\d+)?$/.test(info[field]))) {
      throw new Error('DeepSeek 余额接口返回的币种或金额格式有误。');
    }
    return {
      currency: info.currency,
      total_balance: info.total_balance,
      granted_balance: info.granted_balance,
      topped_up_balance: info.topped_up_balance,
    };
  });
  return { is_available: data.is_available, balance_infos: balances };
}

const server = new McpServer({ name: 'deepseek-balance', version: '1.0.0' });
server.registerTool('get_balance', {
  title: '查询 DeepSeek API 余额',
  description: '实时查询本机 DSH 配置的 DeepSeek 官方 API 账户余额，返回币种、总余额、赠金、充值余额，以及余额是否可供 API 调用。',
  inputSchema: {},
  annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
}, async (_args, { signal }) => {
  try {
    const balance = await getBalance(signal);
    return { content: [{ type: 'text', text: JSON.stringify(balance) }], structuredContent: balance };
  } catch (error) {
    return { isError: true, content: [{ type: 'text', text: error.message }] };
  }
});

await server.connect(new StdioServerTransport());
