# FastGPT 单节点模板合并生成器 V6

## 新定位

V6 = FastGPT 单节点模板合并生成器。

V6 不再使用 V5 那种“大资料包 + AI 直接写完整 JSON”的路线。V6 改为：

1. 内置单节点模板。
2. AI 根据用户需求输出合并方案。
3. 代码节点根据合并方案拼接完整 JSON。
4. AI 对生成结果做整理和问题检查。
5. 最后代码节点做确定性校验和归一化。

V6 不再让 AI 直接生成完整 FastGPT 工作流 JSON。AI 负责理解需求和选择节点；程序负责套用固定节点模板、生成 `nodes`、`edges`、`chatConfig`；AI 只做整理检查，不直接自由改完整 JSON；最终由代码校验器拦截错误。

## 重点解决的问题

- edges handle 写错。
- 变量引用格式错误。
- textEditor.value 被写坏。
- code.value JSON 字符串转义错误。
- answer.text 为空。
- 模型、Webhook、Token 被编造。
- readFiles 没有真正拿到 userFiles。
- code params 和 inputs 不匹配。
- code return 和 outputs 不匹配。

## 主线文件

```text
workflow_template_generator_V6/
├─ README.md
├─ build_v6_single_node_template_generator.py
├─ single_node_templates.json
├─ fastgpt_workflow_template_generator_v6.json
└─ V6_设计说明.md
```

如果目录中以后出现其它研究文件，主线仍以上面 5 个文件为准。

## 当前支持场景

第一阶段只支持三个稳定场景：

1. 文件上传总结。
2. 文件上传 + 代码分析 + AI 总结。
3. 纯文本输入 + AI 回复。

暂不支持复杂分支、循环、Webhook、数据库、知识库。

## 构建方式

在仓库根目录运行：

```powershell
python workflow_template_generator_V6\build_v6_single_node_template_generator.py
```

输出：

```text
workflow_template_generator_V6/fastgpt_workflow_template_generator_v6.json
```

## V6 工作流结构

生成器自身工作流结构：

```text
userGuide
-> start
-> text_single_node_templates
-> merge_plan_ai
-> merge_json_code
-> review_ai
-> final_validate_code
-> answer
```

`merge_plan_ai` 只输出合并方案 JSON，不输出完整 FastGPT 工作流 JSON。

`merge_json_code` 负责：

- 克隆单节点模板。
- 设置 nodeId。
- 设置 position。
- 生成 edges。
- 生成 sourceHandle / targetHandle。
- 固定 answer.text。
- 固定 readFiles.fileUrlList。
- 固定 chatConfig.fileSelectConfig。
- 移除假模型、假 Webhook、假 Token。

`review_ai` 只输出检查报告，不自由重写业务。

`final_validate_code` 做确定性校验，通过则输出最终 JSON，失败则输出错误报告和原 JSON。

## 禁止事项

禁止在 V6 生成器里继续使用 V5 的“大资料包让 AI 直接写完整 JSON”方式。

禁止让 AI 自由生成：

- nodes
- edges
- chatConfig
- sourceHandle
- targetHandle
- answer.text
- readFiles.fileUrlList
- code.value

这些必须由模板和代码生成。

禁止编造模型 ID、知识库 ID、Webhook、Token、API Key、数据库连接、插件 ID、secret。

## 后续要补

1. 扩展更多单节点模板。
2. 增加更多场景合并策略。
3. 增加真实导入测试样例。
4. 增加对判断器、表单、工具节点的第二阶段支持。
