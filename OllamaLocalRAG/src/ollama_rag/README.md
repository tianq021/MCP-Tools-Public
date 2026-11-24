# `ollama_rag` 核心包

这里是项目的主要 Python 代码，负责把文档解析、向量化、检索、工具调用和模型回答串成完整的本地 RAG 流程。

## 子模块

- `config/`：读取模型名称、服务地址和项目路径等设置。
- `models/`：访问 Ollama 对话模型和向量模型。
- `documents/`：读取 PDF、Word、TXT、Markdown，并切分文本。
- `vectorstore/`：保存和检索 SQLite 向量数据。
- `indexing/`：构建全量或增量文档索引。
- `tools/`：定义模型可调用的知识库工具。
- `agents/`：判断是否检索、调用工具并生成带来源的回答。
- `api/`：向命令行、OpenClaw 和其他客户端提供统一接口。
- `ingestion/`、`retrieval/`、`generation/`：为旧调用路径保留的兼容模块。

`__init__.py` 只放包级信息或稳定导出。运行数据应写入项目根目录的 `data/`、`storage/`，不要写进本目录。

