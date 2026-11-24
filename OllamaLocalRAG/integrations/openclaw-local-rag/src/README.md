# src：OpenClaw 插件源码

这个目录保存 `local-rag` 插件的 TypeScript 源码。

- `index.ts`：声明插件身份、配置 Schema、`search_knowledge_base` 参数和 HTTP 调用实现。

修改工具名、参数或配置后，必须重新运行 `npm run plugin:build` 和 `npm run plugin:validate`，确保生成的 `openclaw.plugin.json` 与源码一致。
