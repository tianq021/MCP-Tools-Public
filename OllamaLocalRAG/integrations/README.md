# integrations：外部系统接入

这个目录保存本项目与外部平台之间的适配层。适配层只转换协议和参数，不复制 RAG 核心实现。

- `openclaw-local-rag/`：把本地知识库注册为 OpenClaw 工具。

如果 RAG HTTP API 正常但外部平台调用失败，优先检查这里以及外部平台的插件和工具白名单配置。
