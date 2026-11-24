const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const root = path.resolve(__dirname, '..');
const repo = path.join(root, 'FastGPT-main');
const outputDir = path.join(root, 'deepseek_fastgpt_all_nodes_10files');

const read = (relativePath) =>
  fs.readFileSync(path.join(root, relativePath.replaceAll('/', path.sep)), 'utf8');

const hash = (text) => crypto.createHash('sha256').update(text).digest('hex');

const sourceSection = (relativePath) => {
  const text = read(relativePath);
  const extension = path.extname(relativePath).slice(1);
  const language = extension === 'tsx' ? 'tsx' : extension === 'ts' ? 'ts' : 'json';
  return [
    `## SOURCE: ${relativePath}`,
    '',
    `SHA256: \`${hash(text)}\``,
    '',
    `\`\`\`${language}`,
    text.trimEnd(),
    '```',
    ''
  ].join('\n');
};

const bundle = (title, purpose, sources, extra = '') => {
  const header = [
    `# ${title}`,
    '',
    '本文件由本地 FastGPT 源码机械提取生成。源码仓库只读，未被修改。',
    '',
    `用途：${purpose}`,
    '',
    '阅读规则：源码定义优先于自然语言说明；不要凭空补字段；动态资源节点必须在导入后绑定真实资源。',
    '',
    extra.trim(),
    ''
  ].join('\n');
  return header + sources.map(sourceSection).join('\n');
};

const files = [
  {
    name: '01_总协议_节点枚举_模板注册表.md',
    content: bundle(
      '总协议、40 种节点枚举与模板注册表',
      '确定合法 flowNodeType、系统模板注册范围和生成边界。',
      [
        'FastGPT-main/packages/global/core/workflow/node/constant.ts',
        'FastGPT-main/packages/global/core/workflow/template/constants.ts'
      ],
      [
        '## 强制生成协议',
        '',
        '- 必须先阅读全部 10 个文件。',
        '- 最终工作流顶层使用 `nodes`、`edges`、`chatConfig`。',
        '- 只加入业务需要的节点，不能机械塞入全部 40 种类型。',
        '- `FlowNodeTypeEnum` 是合法类型的唯一基线。',
        '- 系统模板、内部节点、动态资源节点、旧版兼容节点必须区别对待。',
        '- 文件业务设置 `canSelectFile: true`、`maxFiles: 10`。',
        '- 禁止编造模型、知识库、插件、应用、工具、数据库、Webhook 和密钥。',
        '- 最终只输出一个可由 `JSON.parse` 解析的 JSON 对象。',
        '',
        '## 节点类别',
        '',
        '源码核对结论：`FlowNodeTypeEnum` 共 40 种；`systemNodes` 公共业务模板 20 个；应用模板列表 25 个；插件模板列表 23 个；去重并追加动态、内部和旧版模板后，`moduleTemplatesFlat` 覆盖 38 种。剩余两个特殊类型是 `comment`（有源码模板但未注册进该扁平列表）和 `globalVariable`（由前端 `adapt.ts` 根据系统变量合成）。',
        '',
        '- 静态系统模板：可从源码对象构造。',
        '- 编辑器合成节点：由适配代码生成，不一定在模板注册表中。',
        '- 动态资源节点：结构取决于用户安装的应用、插件、工具或工具集。',
        '- 内部容器节点：只能作为循环或并行容器的子节点。',
        '- 旧版兼容节点：只用于读取或修复旧工作流，不用于新建。'
      ].join('\n')
    )
  },
  {
    name: '02_基础节点_入口_回复_文本_编辑器适配.md',
    content: bundle(
      '基础节点、入口、回复、文本与编辑器适配',
      '提供 userGuide、workflowStart、answerNode、textEditor、comment、emptyNode、customFeedback、globalVariable 的真实依据。',
      [
        'FastGPT-main/packages/global/core/workflow/template/system/systemConfig.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/workflowStart.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/assignedAnswer.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/textEditor.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/comment.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/emptyNode.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/customFeedback.ts',
        'FastGPT-main/projects/app/src/web/core/workflow/adapt.ts'
      ]
    )
  },
  {
    name: '03_AI_Agent_分类_提取_问题优化.md',
    content: bundle(
      'AI、Agent、分类、内容提取与问题优化',
      '提供 chatNode、agent、classifyQuestion、contentExtract、cfr 的完整源码模板和专用类型。',
      [
        'FastGPT-main/packages/global/core/workflow/template/system/aiChat/index.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/aiChat/type.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/agent/index.ts',
        'FastGPT-main/packages/global/core/workflow/node/agent/constants.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/classifyQuestion/index.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/classifyQuestion/type.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/contextExtract/index.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/contextExtract/type.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/queryExtension.ts'
      ]
    )
  },
  {
    name: '04_知识库_文件解析_十文件规则.md',
    content: bundle(
      '知识库、文件解析与十文件规则',
      '提供 datasetSearchNode、datasetConcatNode、readFiles 的真实源码，并固定十文件上传的数据流要求。',
      [
        'FastGPT-main/packages/global/core/workflow/template/system/datasetSearch.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/datasetConcat.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/readFiles/index.tsx',
        'FastGPT-main/packages/global/core/workflow/template/system/readFiles/type.ts',
        'FastGPT-main/packages/service/core/workflow/dispatch/tools/readFiles.ts',
        'FastGPT-main/packages/service/core/workflow/utils/file.ts'
      ],
      [
        '## 十文件硬规则',
        '',
        '- `chatConfig.fileSelectConfig.canSelectFile = true`。',
        '- `chatConfig.fileSelectConfig.maxFiles = 10`。',
        '- `workflowStart.outputs` 必须包含 `userFiles`。',
        '- `readFiles.fileUrlList.value` 使用二维引用 `[[\"start\",\"userFiles\"]]`。',
        '- 允许选 10 个文件不等于模型上下文无限；超长内容必须分段摘要或报告截断。',
        '- 知识库 ID 未提供时不得伪造。'
      ].join('\n')
    )
  },
  {
    name: '05_判断_变量_用户选择_表单.md',
    content: bundle(
      '判断、变量、用户选择与表单',
      '提供 ifElseNode、variableUpdate、userSelect、formInput 的真实结构、条件类型和交互类型。',
      [
        'FastGPT-main/packages/global/core/workflow/template/system/ifElse/index.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/ifElse/constant.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/ifElse/type.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/variableUpdate/index.tsx',
        'FastGPT-main/packages/global/core/workflow/template/system/variableUpdate/type.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/interactive/constants.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/interactive/type.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/interactive/userSelect.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/interactive/formInput.ts'
      ]
    )
  },
  {
    name: '06_代码运行_HTTP请求.md',
    content: bundle(
      '代码运行与 HTTP 请求',
      '提供 code、httpRequest468 的真实系统模板、沙箱语言定义和输入输出要求。',
      [
        'FastGPT-main/packages/global/core/workflow/template/system/sandbox/index.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/sandbox/constants.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/http468.ts'
      ],
      [
        '## 代码生成额外规则',
        '',
        '- `params.xxx` 必须有同名 input。',
        '- 代码返回字段必须与 outputs 的 key 一致。',
        '- 代码写入 JSON 字符串时，正则反斜杠必须双重转义。',
        '- HTTP URL、Header 密钥和连接信息未知时必须留空。'
      ].join('\n')
    )
  },
  {
    name: '07_循环_并行_内部节点_旧版循环.md',
    content: bundle(
      '循环、并行、内部节点与旧版循环',
      '提供 parallelRun、loopRun、loopRunStart、loopRunBreak、loopStart、loopEnd、旧版 loop 的真实依据。',
      [
        'FastGPT-main/packages/global/core/workflow/template/system/parallelRun/parallelRun.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/loopRun/loopRun.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/loopRun/loopRunStart.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/loopRun/loopRunBreak.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/loop/loopStart.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/loop/loopEnd.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/abandoned/loop/index.ts',
        'FastGPT-main/packages/global/core/workflow/node/constant.ts'
      ],
      [
        '## 容器规则',
        '',
        '- 新工作流优先 `loopRun`，不要新建旧版 `loop`。',
        '- `loopRunStart`、`loopRunBreak` 只能在合法循环容器内。',
        '- `loopStart`、`loopEnd` 是嵌套/旧版内部节点。',
        '- 并行与循环必须复制真实父子关系和特殊 handle，禁止只改 flowNodeType。'
      ].join('\n')
    )
  },
  {
    name: '08_插件_工具_应用_动态资源节点.md',
    content: bundle(
      '插件、工具、应用与动态资源节点',
      '提供 pluginConfig、pluginInput、pluginOutput、tools、stopTool、toolParams、tool、toolSet、appModule、pluginModule、旧版 app 的真实生成依据。',
      [
        'FastGPT-main/packages/global/core/workflow/template/system/pluginConfig.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/pluginInput.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/pluginOutput.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/toolCall.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/stopTool.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/toolParams.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/runTool.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/runToolSet.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/runApp.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/runPlugin.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/abandoned/runApp/index.ts',
        'FastGPT-main/packages/global/core/workflow/template/system/abandoned/runApp/type.ts'
      ],
      [
        '## 动态资源硬规则',
        '',
        '- `tool`、`toolSet`、`appModule`、`pluginModule` 的 inputs/outputs 会由真实资源定义生成。',
        '- 没有真实资源详情时只能保留待配置骨架或省略，不能伪造 ID 与参数。',
        '- `app` 是旧版兼容节点，不用于新建。',
        '- 插件输入输出节点只用于插件工作流。'
      ].join('\n')
    )
  },
  {
    name: '09_IO类型_节点类型_边_公共输入输出.md',
    content: bundle(
      'IO 类型、节点类型、边与公共输入输出',
      '提供所有模板共用的 key、valueType、renderType、节点 schema、边 schema、公共输入和公共输出。',
      [
        'FastGPT-main/packages/global/core/workflow/constants.ts',
        'FastGPT-main/packages/global/core/workflow/type/io.ts',
        'FastGPT-main/packages/global/core/workflow/type/node.ts',
        'FastGPT-main/packages/global/core/workflow/type/edge.ts',
        'FastGPT-main/packages/global/core/workflow/type/index.ts',
        'FastGPT-main/packages/global/core/workflow/template/input.ts',
        'FastGPT-main/packages/global/core/workflow/template/output.ts',
        'FastGPT-main/packages/global/core/workflow/runtime/constants.ts'
      ],
      [
        '## 连线与引用规则',
        '',
        '- 普通边：`${source}-source-right` → `${target}-target-left`。',
        '- 边只表示执行顺序，变量通过 input.value 或 `{{$node.output$}}` 传递。',
        '- 所有引用必须指向真实 nodeId 和真实 output key。',
        '- 特殊分支、循环、工具 handle 以源码和真实导出为准。'
      ].join('\n')
    )
  },
  {
    name: '10_真实导出样例_校验清单_最终提示词.md',
    content: ''
  }
];

const exampleFiles = [
  'new_fastgpt_clean_core/templates/01_file_upload_summary.json',
  'new_fastgpt_clean_core/templates/02_form_input.json',
  'new_fastgpt_clean_core/templates/03_user_select_branch.json',
  'new_fastgpt_clean_core/templates/04_loop_batch.json',
  'new_fastgpt_clean_core/templates/05_database_connection.json',
  'new_fastgpt_clean_core/templates/06_wecom_webhook.json',
  'new_fastgpt_clean_core/templates/07_dingtalk_webhook.json',
  'new_fastgpt_clean_core/templates/08_feishu_webhook.json',
  'new_fastgpt_clean_core/templates/09_variable_update.json',
  'new_fastgpt_clean_core/templates/10_tool_call_basic.json',
  'new_fastgpt_clean_core/templates/11_fetch_url_tool.json',
  'new_fastgpt_clean_core/examples/tool_call_complex_all_nodes_example.json',
  'new_fastgpt_clean_core/examples/tool_call_with_dataset_example.json',
  'new_fastgpt_clean_core/examples/app_module_chain_example_env_specific.json'
];

const finalInstructions = [
  '# 真实导出样例、校验清单与最终提示词',
  '',
  '本文件中的 JSON 是本地已有导出/样例，用来学习节点实例化后的形态。源码定义与真实导出冲突时，以当前源码和当前目标 FastGPT 版本为准。',
  '',
  '## 最终校验清单',
  '',
  '1. 最终输出是单个合法 JSON 对象。',
  '2. 顶层至少包含 `nodes`、`edges`、`chatConfig`。',
  '3. nodeId 唯一，flowNodeType 必须来自文件 01。',
  '4. inputs/outputs 都是数组，引用指向真实输出。',
  '5. 普通边 source/target 存在，handle 使用节点 ID。',
  '6. 分支、循环、并行和工具使用源码规定的特殊结构。',
  '7. 文件业务必须启用上传，maxFiles 必须为 10。',
  '8. readFiles 的文件引用必须是二维数组。',
  '9. chatNode 的普通上游引用是一维数组。',
  '10. answerNode 最终文本非空并引用真实最终输出。',
  '11. code 的 params、返回字段、inputs、outputs 完全一致。',
  '12. 模型、知识库、应用、插件、工具、数据库、Webhook 未知时不伪造。',
  '13. 所有运行节点从入口可达，所有分支都有终点。',
  '14. 输出前重新执行 JSON.parse 思维检查。',
  '',
  '## 上传十文件后发送给 DeepSeek 的最终提示词',
  '',
  '你已收到 01 至 10 共十个 FastGPT 源码资料文件。先完整读取全部文件，再根据我的业务需求生成 FastGPT 工作流。',
  '',
  '要求：',
  '',
  '- 源码定义优先，真实导出样例用于实例化参考。',
  '- 只选择业务需要的完整节点，不机械加入所有节点。',
  '- 文件业务允许一次上传 10 个文件。',
  '- 不得编造任何外部资源 ID、模型、密钥、URL 或连接。',
  '- 动态资源缺失时留空并在 welcomeText 提示导入后配置。',
  '- 在内部完成需求拆解、节点计划、变量映射、连线和全部校验。',
  '- 最终只输出一个能被 JSON.parse 解析的 JSON 对象，不要 Markdown，不要解释。',
  '',
  '我的业务需求：',
  '',
  '【在这里填写需求】',
  '',
  '---',
  '',
  '## 以下为真实导出与环境样例',
  ''
].join('\n');

files[9].content =
  finalInstructions + '\n\n' + exampleFiles.map(sourceSection).join('\n');

fs.mkdirSync(outputDir, { recursive: true });

for (const file of files) {
  fs.writeFileSync(path.join(outputDir, file.name), file.content, 'utf8');
}

const generated = fs.readdirSync(outputDir).filter((name) => fs.statSync(path.join(outputDir, name)).isFile());
if (generated.length !== 10) {
  throw new Error(`Expected exactly 10 files, found ${generated.length}`);
}

const repoStatusBeforeAfterGuard = fs.existsSync(path.join(repo, '.git'))
  ? 'FastGPT-main may be a nested repository; generator performs reads only.'
  : 'FastGPT-main source directory was read only.';

console.log(
  JSON.stringify(
    {
      outputDir,
      fileCount: generated.length,
      files: generated.sort(),
      note: repoStatusBeforeAfterGuard
    },
    null,
    2
  )
);
