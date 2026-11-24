# tests：自动化测试

这个目录保存基于 Python `unittest` 的模块和行为测试。

- `test_modular_architecture.py`：配置路径、工具注册表和兼容导入测试。
- `test_rag_tool_agent.py`：规则检索、模型自动工具调用和跳过工具测试。
- `test_http_api.py`：`/health`、`/search` 与错误参数测试。

运行全部测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试不应修改正式向量库；需要数据时优先使用假的服务对象或 `storage/vector_db/*_test/`。
