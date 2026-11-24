# src：Python 源码根目录

这个目录采用 `src layout`，正式 Python 包位于 `ollama_rag/`。这样可以避免测试误用项目根目录里的同名文件。

- 安装项目后通过 `import ollama_rag` 使用。
- 应用入口放在 `app/`，维护脚本放在 `scripts/`。
- 运行数据和向量库不得写入这里。

新增 Python 业务模块时，应放入 `src/ollama_rag/` 的对应职责目录。
