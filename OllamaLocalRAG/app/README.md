# app：运行入口

这个目录只放用户直接运行的程序入口，不实现向量检索、模型通信或文档解析等业务逻辑。

- `rag_chat.py`：命令行问答入口，可单次提问或进入交互模式。
- `rag_api.py`：启动只监听 `127.0.0.1` 的 RAG HTTP 服务，供 OpenClaw 调用。

入口程序统一通过 `ollama_rag.api` 组装服务。命令行参数或启动失败先检查这里；检索错误应继续检查 `src/ollama_rag/api/`、`tools/` 和 `vectorstore/`。
