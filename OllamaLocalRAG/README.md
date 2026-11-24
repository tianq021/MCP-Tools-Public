# Ollama 本地 RAG

## 当前结论

- 本机已安装 Ollama `0.32.4`；直接运行 `ollama serve` 已成功启动服务并监听 `127.0.0.1:11434`。桌面程序自动拉起时仍存在日志文件访问被拒绝的问题，后续需要单独处理持久启动。
- 工作区 `2026-08-17` 中已有可复用的中文向量检索实现：
  - 嵌入模型：`quentinz/bge-large-zh-v1.5:f32`
  - 向量维度：1024
  - 已生成 108 个文本块
  - 已支持 Ollama `/api/embed`、SQLite 持久化和余弦相似度检索
- 旧实现面向单个视频转录，不是完整的通用 RAG；缺少多格式文档导入、可替换向量库、生成模型调用、引用回溯和端到端测试。

## 暂定架构

```text
资料 -> 解析与清洗 -> 分块 -> Ollama 嵌入模型 -> 向量库
                                                |
问题 -> 查询向量 -> Top-K 检索 -----------------+
                                                |
                         上下文 + 问题 -> Ollama 生成模型 -> 带来源回答
```

第一阶段采用现有 SQLite + NumPy 方案，并定义统一向量库接口。这样可以先完成可靠的本地闭环，且不需要新增数据库服务；资料量或过滤需求增长后，再替换为专用本地向量库。

嵌入模型与回答模型分开配置。已确认使用 `quentinz/bge-large-zh-v1.5:f32` 生成中文检索向量，第一版回答模型使用现有 `gemma4:12b-it-qat`。详细实测数据与选型理由见 `docs/方案分析.md`。

Python 依赖可用 `python -m pip install -r requirements.txt` 安装；开发时也可以执行 `python -m pip install -e .` 以可编辑模式安装模块。Ollama 模型仍由 Ollama 单独管理。

## 当前进度

- 已从实际下载目录筛选并复制 375 个去重文档，共 44.29MB。
- 已完成两份真实 TXT 的最小向量闭环：18 个分块、1024 维向量、SQLite 持久化、Top-K 检索。
- 两道段落级检索测试均通过。详细记录见 `docs/导入与最小向量测试.md`。
- 已加入 PDF 与 DOCX 解析：PDF 保留页码，DOCX 保留段落和表格行定位。
- 29 份 PDF 和 10 份 DOCX 已全部进入正式库；其中 10 份扫描 PDF 使用 Windows 本地 OCR，解析错误为 0。
- PDF/DOCX混合向量检索测试4/4通过。详细记录见 `docs/PDF与Word支持.md`。
- 已完成全库容量测算：预计7430条1024维向量，原始向量约29.02MiB；第一版确定继续使用SQLite，并升级为NumPy矩阵检索。详见 `docs/向量库选型.md`。
- 已加入 `search_knowledge_base` 工具和 Gemma4 工具调用循环：本地资料类问题由规则触发检索，其他问题由模型判断是否调用知识库，并返回文件名与页码/段落来源。
- 已建立正式全量库 `storage/vector_db/full/full_vectors.sqlite3`：375/375 份文档、7,698 个文本块、1,024 维向量，导入失败 0。SQLite 完整性和 4 类真实检索测试全部通过。

## 自动 RAG 工具

使用正式全量向量库启动单次问答：

```powershell
python app/rag_chat.py "酷课网里搜索不到实习企业时应该怎么办？"
```

不带问题时进入交互模式：

```powershell
python app/rag_chat.py
```

程序会自动选择格式兼容且文本块最多的 SQLite 索引，因此现在默认连接正式全量库；也可以用 `--database` 指定索引。模型可调用的工具是 `search_knowledge_base`，支持按 `personal_school`、`skills_technical`、`test_archive` 三个资料集合以及 PDF、DOCX、TXT、MD 文件类型过滤。

重建正式库和运行验收：

```powershell
python scripts/build_full_index.py --batch-size 64
python scripts/validate_full_index.py
```

完整库结构、统计与维护方式见 `docs/全量向量库.md`。

## 本地检索 API

OpenClaw 等本机客户端通过只监听回环地址的 HTTP 服务调用向量库：

```powershell
python app/rag_api.py --host 127.0.0.1 --port 8765
```

接口：

- `GET /health`：检查向量库、Ollama 和嵌入模型状态。
- `POST /search`：调用 `search_knowledge_base`，请求体支持 `query`、`top_k`、`collection` 和 `source_types`。

示例：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8765/search' `
  -Method Post `
  -ContentType 'application/json; charset=utf-8' `
  -Body '{"query":"学校什么时候开学","top_k":5,"collection":"personal_school"}'
```

为保护本地文档，服务会拒绝监听非回环地址。

后台常驻由当前用户启动文件夹中的 `OpenClaw Local RAG.lnk` 完成；快捷方式直接运行项目虚拟环境的 `pythonw.exe`，不使用 PowerShell 执行策略绕过。

## 目录说明

```text
app/                         命令行等用户入口
config/                      部署配置说明
data/raw/                    原始知识库资料
docs/                        设计、架构与使用说明
scripts/                     建库、验证和诊断脚本
src/ollama_rag/config/       路径与模型配置
src/ollama_rag/models/       Ollama 对话和向量模型客户端
src/ollama_rag/documents/    文档解析、分块与 OCR
src/ollama_rag/vectorstore/  SQLite 向量存储和检索
src/ollama_rag/indexing/     全量和增量建库流程
src/ollama_rag/tools/        模型可调用工具及注册表
src/ollama_rag/agents/       工具调用与回答编排
src/ollama_rag/api/          统一 RAGService API
storage/                     可重建向量库、报告与缓存
tests/                       模块与行为测试
```

详细职责、依赖方向和故障定位表见 `docs/模块化架构.md`。旧的 `ingestion`、`retrieval`、`generation` 导入路径暂时作为兼容层保留。

每个由项目维护的目录都包含自己的 `README.md`，用于说明职责、主要文件和排错入口。第三方依赖、缓存、日志、运行状态、构建产物以及原始文档子目录不单独写入说明文件，以免污染依赖包或被文档索引误收录；它们的用途由最近一级的项目目录 README 统一说明。

## 后续可选优化

1. 资料规模明显增大或 Top-K 精度不足时，再评估重排模型或专用向量数据库。
2. 如果以后加入旧版 `.doc` 文件，再接入 LibreOffice/Word 格式转换。
3. 需要开机常驻时，再处理 Ollama 桌面程序的日志权限问题。
