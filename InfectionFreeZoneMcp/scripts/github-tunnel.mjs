// 本地 CONNECT 隧道：把 github.com 的 443 转发到可达的备用 IP，
// 供 git 通过 http.proxy 使用（TLS SNI 仍是 github.com，证书校验正常）。
// 用法: node scripts/github-tunnel.mjs [listenPort]
import net from "node:net";
import dns from "node:dns";

const listenPort = Number(process.argv[2] ?? 8899);
const argIp = process.argv[3] && /^(\d{1,3}\.){3}\d{1,3}$/.test(process.argv[3]) ? process.argv[3] : null;
const FALLBACK_IPS = argIp ? [argIp] : ["140.82.112.3", "140.82.113.3", "140.82.114.3", "140.82.121.3", "140.82.112.4"];
let rotation = 0;

function pickTarget(host) {
  if (host === "github.com") {
    const ip = FALLBACK_IPS[rotation % FALLBACK_IPS.length];
    rotation++;
    return ip;
  }
  return host;
}

const server = net.createServer((client) => {
  client.once("data", (chunk) => {
    const header = chunk.toString("latin1");
    const match = header.match(/^CONNECT\s+([^\s:]+):(\d+)\s+HTTP\/1\.[01]/i);
    if (!match) {
      client.end("HTTP/1.1 400 Bad Request\r\n\r\n");
      return;
    }
    const host = match[1];
    const port = Number(match[2]);
    const target = pickTarget(host);
    const upstream = net.createConnection({ host: target, port }, () => {
      client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
      upstream.write(chunk.subarray(header.indexOf("\r\n\r\n") + 4));
      upstream.pipe(client);
      client.pipe(upstream);
    });
    upstream.on("error", () => client.destroy());
    client.on("error", () => upstream.destroy());
  });
  client.on("error", () => {});
});
server.listen(listenPort, "127.0.0.1", () => {
  process.stderr.write(`[github-tunnel] listening on 127.0.0.1:${listenPort}\n`);
});