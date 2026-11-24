# new_fastgpt_clean

这是整理后的 FastGPT 工作流 JSON 模板包。目标是：根目录干净、模板可复用、规则容易读取、旧资料不干扰主线。

## 推荐读取顺序

1. `docs/00_AI读取顺序.md`
2. `docs/01_FastGPT_JSON核心规范.md`
3. `docs/02_节点规范速查.md`
4. `docs/03_常见错误与自检清单.md`
5. 需要生成工作流时，从 `templates/` 选择母版。

## 目录说明

- `docs/`：规则、补丁、自检、提示词。
- `templates/`：可直接参考或组合的稳定 JSON 母版。
- `examples/`：完整案例或环境相关案例。
- `archive/`：授权、哈希、旧版资料、重复样例，默认不作为主线读取。

## 使用原则

- 新建工作流优先使用 `templates/`。
- 不写死数据库密码、API Key、Token、Webhook、知识库 ID、私有 appModule pluginId。
- 导入前先检查：`nodes`、`edges`、`chatConfig`、节点 ID、变量引用、开始节点输出。
- 旧版资料只在模板缺失时查看，避免被历史规则误导。
