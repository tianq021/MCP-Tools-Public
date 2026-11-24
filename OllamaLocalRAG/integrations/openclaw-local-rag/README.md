# openclaw-local-rag：OpenClaw 工具插件

这个目录是独立的 TypeScript 插件包，把本项目的本地向量检索注册为 OpenClaw 工具 `search_knowledge_base`。

- `src/`：插件源码和工具 JSON Schema。
- `dist/`：TypeScript 构建产物，不手工修改。
- `openclaw.plugin.json`：插件发现清单和 `contracts.tools` 声明。
- `package.json`、`package-lock.json`：依赖、构建命令和 OpenClaw 入口。

工具通过回环地址调用 Python RAG 服务，不直接读取 SQLite，也不重复调用回答模型。

```powershell
npm install
npm run plugin:build
npm run plugin:validate
openclaw plugins install --link .
openclaw plugins inspect local-rag --runtime --json
```

Python 服务默认位于 `http://127.0.0.1:8765`。如需改变地址，应修改 `plugins.entries.local-rag.config.baseUrl`，并保持为可信的本机服务。

插件加载正常但模型看不到工具时，依次检查 `plugins.allow`、模型工具白名单、Agent 工具白名单和沙箱工具白名单。
