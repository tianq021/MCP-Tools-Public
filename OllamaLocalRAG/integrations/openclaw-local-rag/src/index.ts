import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";


const DEFAULT_BASE_URL = "http://127.0.0.1:8765";
const DEFAULT_TIMEOUT_MS = 60_000;

const configSchema = Type.Object(
  {
    baseUrl: Type.Optional(
      Type.String({ description: "Loopback URL of the local RAG HTTP service." }),
    ),
    timeoutMs: Type.Optional(
      Type.Integer({
        description: "Maximum time for one vector search request.",
        minimum: 1_000,
        maximum: 300_000,
      }),
    ),
  },
  { additionalProperties: false },
);

export default defineToolPlugin({
  id: "local-rag",
  name: "Local RAG",
  description: "Search the local Ollama and SQLite document vector database.",
  configSchema,
  tools: (tool) => [
    tool({
      name: "search_knowledge_base",
      label: "Search Local Knowledge Base",
      description:
        "搜索用户电脑上的本地 PDF、Word、TXT 和 Markdown 知识库。" +
        "问题涉及学校、开学、课程、实习、通知、协议、简历、报告、本地文件，" +
        "或者要求核对原文、文件名、页码和段落时，必须先调用此工具再回答。",
      parameters: Type.Object(
        {
          query: Type.String({
            description: "适合语义检索的完整问题，不要只传零散关键词。",
            minLength: 1,
            maxLength: 2_000,
          }),
          top_k: Type.Optional(
            Type.Integer({
              description: "返回最相关的文本块数量，默认 5。",
              minimum: 1,
              maximum: 8,
            }),
          ),
          collection: Type.Optional(
            Type.Union(
              [
                Type.Literal("all"),
                Type.Literal("personal_school"),
                Type.Literal("skills_technical"),
                Type.Literal("test_archive"),
              ],
              { description: "检索集合，默认 all。" },
            ),
          ),
          source_types: Type.Optional(
            Type.Array(
              Type.Union([
                Type.Literal("pdf"),
                Type.Literal("docx"),
                Type.Literal("txt"),
                Type.Literal("md"),
              ]),
              { description: "可选的文档类型过滤。", maxItems: 4 },
            ),
          ),
        },
        { additionalProperties: false },
      ),
      async execute(params, config, context) {
        const baseUrl = (config.baseUrl ?? DEFAULT_BASE_URL).replace(/\/$/, "");
        const timeoutMs = config.timeoutMs ?? DEFAULT_TIMEOUT_MS;
        const signals = [AbortSignal.timeout(timeoutMs)];
        if (context.signal) {
          signals.push(context.signal);
        }

        try {
          const response = await fetch(`${baseUrl}/search`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json; charset=utf-8",
              Accept: "application/json",
            },
            body: JSON.stringify(params),
            signal: AbortSignal.any(signals),
          });
          const payload = (await response.json()) as Record<string, unknown>;
          if (!response.ok || payload.ok !== true) {
            return {
              ok: false,
              error_code: String(payload.error_code ?? `RAG_HTTP_${response.status}`),
              error: String(payload.error ?? "本地知识库检索失败。"),
              retryable: response.status >= 500,
            };
          }
          return payload;
        } catch (error) {
          return {
            ok: false,
            error_code: "RAG_UNAVAILABLE",
            error: `无法连接本地知识库：${error instanceof Error ? error.message : String(error)}`,
            retryable: true,
          };
        }
      },
    }),
  ],
});
