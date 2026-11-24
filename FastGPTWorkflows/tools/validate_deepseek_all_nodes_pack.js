const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const root = path.resolve(__dirname, '..');
const packDir = path.join(root, 'deepseek_fastgpt_all_nodes_10files');
const enumFile = path.join(
  root,
  'FastGPT-main/packages/global/core/workflow/node/constant.ts'
);

const errors = [];
const packFiles = fs
  .readdirSync(packDir)
  .filter((name) => fs.statSync(path.join(packDir, name)).isFile())
  .sort();

if (packFiles.length !== 10) errors.push(`资料包文件数应为 10，实际为 ${packFiles.length}`);

const allText = packFiles
  .map((name) => fs.readFileSync(path.join(packDir, name), 'utf8'))
  .join('\n');

const enumText = fs.readFileSync(enumFile, 'utf8');
const enumBody = enumText.match(/export enum FlowNodeTypeEnum\s*\{([\s\S]*?)\n\}/)?.[1] || '';
const nodeTypes = [...enumBody.matchAll(/=\s*'([^']+)'/g)].map((match) => match[1]);
const missingTypes = nodeTypes.filter((type) => !allText.includes(type));
if (missingTypes.length) errors.push(`缺少节点类型: ${missingTypes.join(', ')}`);

const sourcePattern =
  /## SOURCE: ([^\r\n]+)\r?\n\r?\nSHA256: `([a-f0-9]{64})`\r?\n\r?\n```(?:ts|tsx|json)\r?\n([\s\S]*?)\r?\n```/g;
let sourceCount = 0;
const sourcePaths = new Set();

for (const match of allText.matchAll(sourcePattern)) {
  sourceCount += 1;
  const [, relativePath, expectedHash, embeddedText] = match;
  sourcePaths.add(relativePath);
  const sourcePath = path.join(root, relativePath.replaceAll('/', path.sep));
  if (!fs.existsSync(sourcePath)) {
    errors.push(`源码路径不存在: ${relativePath}`);
    continue;
  }
  const sourceText = fs.readFileSync(sourcePath, 'utf8').trimEnd();
  const actualHash = crypto.createHash('sha256').update(fs.readFileSync(sourcePath)).digest('hex');
  if (actualHash !== expectedHash) errors.push(`源码哈希不匹配: ${relativePath}`);
  if (sourceText !== embeddedText) errors.push(`嵌入源码内容不一致: ${relativePath}`);
}

const jsonFile = packFiles.find((name) => name.startsWith('10_'));
const jsonText = fs.readFileSync(path.join(packDir, jsonFile), 'utf8');
let jsonExamples = 0;
for (const match of jsonText.matchAll(/```json\r?\n([\s\S]*?)\r?\n```/g)) {
  jsonExamples += 1;
  try {
    JSON.parse(match[1]);
  } catch (error) {
    errors.push(`真实 JSON 示例无法解析: ${error.message}`);
  }
}

const maxFileSizes = packFiles.map((name) => {
  const stat = fs.statSync(path.join(packDir, name));
  return { name, bytes: stat.size };
});
for (const item of maxFileSizes) {
  if (item.bytes > 100 * 1024 * 1024) errors.push(`文件超过 100MB: ${item.name}`);
}

console.log(
  JSON.stringify(
    {
      packDir,
      fileCount: packFiles.length,
      nodeTypeCount: nodeTypes.length,
      missingNodeTypes: missingTypes,
      embeddedSourceCount: sourceCount,
      uniqueSourceCount: sourcePaths.size,
      jsonExampleCount: jsonExamples,
      maxFileSizes,
      errors,
      pass: errors.length === 0
    },
    null,
    2
  )
);

process.exit(errors.length === 0 ? 0 : 1);
