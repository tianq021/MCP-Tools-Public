# scripts：建库与运维脚本

这个目录保存可以独立执行的维护入口。

- `build_full_index.py`：扫描原始文档并重建正式全量向量库。
- `validate_full_index.py`：检查 SQLite 完整性、统计和真实检索结果。
- `validate_document_loaders.py`：验证各类文档解析器。
- `estimate_index_capacity.py`：估算向量数量和数据库容量。
- `minimal_vector_test.py`：TXT 最小向量闭环。
- `minimal_pdf_docx_vector_test.py`：PDF/DOCX 混合检索测试。
- `windows_ocr.ps1`：通过 Windows 本地能力处理扫描型 PDF。
- `stop_rag_api.ps1`：按 PID 和命令行双重校验后停止当前项目的 RAG API。

脚本负责调用 `src/ollama_rag/` 中的模块，不应在脚本内复制业务实现。脚本报错时根据堆栈定位到具体源码目录。
