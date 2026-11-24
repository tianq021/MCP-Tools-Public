const fs = require('fs');

const file = process.argv[2];
if (!file) {
  console.error('Usage: node tools/validate_fastgpt_workflow.js <workflow.json>');
  process.exit(2);
}

const workflow = JSON.parse(fs.readFileSync(file, 'utf8'));
const errors = [];
const nodes = Array.isArray(workflow.nodes) ? workflow.nodes : [];
const edges = Array.isArray(workflow.edges) ? workflow.edges : [];
const nodeIds = new Set(nodes.map((node) => node.nodeId));
const nodeMap = new Map(nodes.map((node) => [node.nodeId, node]));

if (nodes.length !== nodeIds.size) errors.push('存在重复 nodeId');

for (const node of nodes) {
  if (!node.nodeId) errors.push('存在空 nodeId');
  if (!node.flowNodeType) errors.push(`${node.nodeId}: 缺少 flowNodeType`);
  if (!Array.isArray(node.inputs) || !Array.isArray(node.outputs)) {
    errors.push(`${node.nodeId}: inputs/outputs 必须为数组`);
  }
}

for (const edge of edges) {
  if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
    errors.push(`边引用未知节点: ${edge.source} -> ${edge.target}`);
  }
  const sourceType = nodeMap.get(edge.source)?.flowNodeType;
  const isBranchSource = sourceType === 'classifyQuestion' || sourceType === 'ifElseNode';
  const validSourceHandle = isBranchSource
    ? edge.sourceHandle?.startsWith(`${edge.source}-source-`)
    : edge.sourceHandle === `${edge.source}-source-right`;
  if (!validSourceHandle) {
    errors.push(`普通 sourceHandle 错误: ${edge.sourceHandle}`);
  }
  if (edge.targetHandle !== `${edge.target}-target-left`) {
    errors.push(`普通 targetHandle 错误: ${edge.targetHandle}`);
  }
}

const outputMap = new Map(
  nodes.map((node) => [node.nodeId, new Set((node.outputs || []).map((output) => output.key))])
);
const references = [];

for (const node of nodes) {
  for (const input of node.inputs || []) {
    const value = input.value;
    if (Array.isArray(value) && value.length === 2 && typeof value[0] === 'string') {
      references.push([node.nodeId, value[0], value[1]]);
    }
    if (Array.isArray(value) && Array.isArray(value[0])) {
      for (const reference of value) {
        if (reference.length === 2) references.push([node.nodeId, reference[0], reference[1]]);
      }
    }
    if (typeof value === 'string') {
      for (const match of value.matchAll(/\{\{\$([^.]+)\.([^$]+)\$\}\}/g)) {
        references.push([node.nodeId, match[1], match[2]]);
      }
    }
  }
}

for (const [target, source, key] of references) {
  if (!outputMap.get(source)?.has(key)) {
    errors.push(`变量引用不存在: ${target} <- ${source}.${key}`);
  }
}

const nodeTypes = nodes.map((node) => node.flowNodeType);
for (const requiredType of [
  'userGuide',
  'workflowStart',
  'readFiles',
  'textEditor',
  'chatNode',
  'answerNode'
]) {
  if (!nodeTypes.includes(requiredType)) errors.push(`缺少节点类型: ${requiredType}`);
}

const fileConfig = workflow.chatConfig?.fileSelectConfig;
if (fileConfig?.canSelectFile !== true) errors.push('未开启文件上传');
if (fileConfig?.maxFiles !== 10) errors.push(`maxFiles 应为 10，实际为 ${fileConfig?.maxFiles}`);

console.log(
  JSON.stringify(
    {
      file,
      nodeCount: nodes.length,
      edgeCount: edges.length,
      nodeTypes,
      maxFiles: fileConfig?.maxFiles,
      referenceCount: references.length,
      errors,
      pass: errors.length === 0
    },
    null,
    2
  )
);

process.exit(errors.length === 0 ? 0 : 1);
