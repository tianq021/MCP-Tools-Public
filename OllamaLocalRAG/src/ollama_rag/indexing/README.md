# 索引构建模块

本目录负责把文档批量写入正式向量库。

- `full_index.py`：扫描文档目录，提取并切分内容，调用 Ollama 向量模型，再写入 SQLite，同时生成导入和验证信息。
- `__init__.py`：导出索引构建接口。

输入通常是 `data/raw/downloads/`，输出位于 `storage/vector_db/`。新增文档后应通过这里或 `scripts/` 中的索引脚本更新，不要手工修改数据库。

