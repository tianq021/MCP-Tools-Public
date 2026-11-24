#!/usr/bin/env python3
"""Build the V6 FastGPT single-node-template generator workflow.

V6 intentionally avoids the V5 pattern where AI writes complete workflow JSON.
AI only writes a merge plan. Code nodes clone fixed single-node templates,
assemble nodes/edges/chatConfig, and run deterministic validation.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TEMPLATES_PATH = ROOT / "single_node_templates.json"
OUTPUT_PATH = ROOT / "fastgpt_workflow_template_generator_v6.json"


def text_editor(node_id: str, name: str, value: str, x: int, y: int) -> dict:
    return {
        "nodeId": node_id,
        "name": name,
        "avatar": "core/workflow/template/textConcat",
        "flowNodeType": "textEditor",
        "position": {"x": x, "y": y},
        "inputs": [
            {
                "key": "system_textareaInput",
                "renderTypeList": ["textarea"],
                "valueType": "string",
                "required": True,
                "label": "workflow:concatenation_text",
                "value": value,
            }
        ],
        "outputs": [
            {
                "id": "system_text",
                "key": "system_text",
                "label": "workflow:concatenation_result",
                "type": "static",
                "valueType": "string",
            }
        ],
    }


def chat_node(
    node_id: str,
    name: str,
    system_prompt: str,
    user_input_ref: list[str],
    x: int,
    y: int,
    response_format: str | None = None,
) -> dict:
    inputs = [
        {
            "key": "model",
            "renderTypeList": ["settingLLMModel", "reference"],
            "label": "common:core.module.input.label.aiModel",
            "valueType": "string",
            "value": "",
        },
        {
            "key": "temperature",
            "renderTypeList": ["hidden"],
            "label": "",
            "valueType": "number",
            "value": 0,
        },
        {
            "key": "isResponseAnswerText",
            "renderTypeList": ["hidden"],
            "label": "",
            "value": True,
            "valueType": "boolean",
        },
        {
            "key": "aiChatQuoteRole",
            "renderTypeList": ["hidden"],
            "label": "",
            "valueType": "string",
            "value": "system",
        },
        {
            "key": "systemPrompt",
            "renderTypeList": ["textarea", "reference"],
            "valueType": "string",
            "label": "common:core.ai.Prompt",
            "maxLength": 100000,
            "value": system_prompt,
        },
        {
            "key": "history",
            "renderTypeList": ["numberInput", "reference"],
            "valueType": "chatHistory",
            "label": "common:core.module.input.label.chat history",
            "required": True,
            "value": 0,
        },
        {
            "key": "userChatInput",
            "renderTypeList": ["reference", "textarea"],
            "valueType": "string",
            "label": "workflow:user_question",
            "required": True,
            "value": user_input_ref,
        },
    ]
    if response_format:
        inputs.append(
            {
                "key": "aiChatResponseFormat",
                "renderTypeList": ["hidden"],
                "label": "",
                "valueType": "string",
                "value": response_format,
            }
        )
    return {
        "nodeId": node_id,
        "name": name,
        "avatar": "core/workflow/template/aiChat",
        "flowNodeType": "chatNode",
        "showStatus": True,
        "position": {"x": x, "y": y},
        "version": "4.9.7",
        "inputs": inputs,
        "outputs": [
            {
                "id": "history",
                "key": "history",
                "type": "static",
                "valueType": "chatHistory",
                "label": "新上下文",
            },
            {
                "id": "answerText",
                "key": "answerText",
                "type": "static",
                "valueType": "string",
                "label": "AI 回复",
            },
            {
                "id": "system_error_text",
                "key": "system_error_text",
                "type": "error",
                "valueType": "string",
                "label": "错误信息",
            },
        ],
        "catchError": False,
    }


def code_node(
    node_id: str,
    name: str,
    code: str,
    inputs_extra: list[dict],
    outputs_extra: list[dict],
    x: int,
    y: int,
) -> dict:
    return {
        "nodeId": node_id,
        "name": name,
        "avatar": "core/workflow/template/codeRun",
        "flowNodeType": "code",
        "showStatus": True,
        "position": {"x": x, "y": y},
        "inputs": [
            {
                "key": "system_addInputParam",
                "renderTypeList": ["addInputParam"],
                "valueType": "dynamic",
                "label": "",
                "required": False,
            },
            *inputs_extra,
            {
                "key": "codeType",
                "renderTypeList": ["hidden"],
                "label": "",
                "valueType": "string",
                "value": "js",
            },
            {
                "key": "code",
                "renderTypeList": ["custom"],
                "label": "",
                "valueType": "string",
                "value": code,
            },
        ],
        "outputs": [
            {
                "id": "system_addOutputParam",
                "key": "system_addOutputParam",
                "type": "dynamic",
                "valueType": "dynamic",
                "label": "",
            },
            {
                "id": "system_rawResponse",
                "key": "system_rawResponse",
                "type": "static",
                "valueType": "object",
                "label": "system_rawResponse",
            },
            *outputs_extra,
            {
                "id": "error",
                "key": "error",
                "type": "error",
                "valueType": "string",
                "label": "error",
            },
        ],
        "catchError": False,
    }


def answer_node() -> dict:
    return {
        "nodeId": "answer",
        "name": "指定回复",
        "avatar": "core/workflow/template/reply",
        "flowNodeType": "answerNode",
        "position": {"x": 3300, "y": -320},
        "inputs": [
            {
                "key": "text",
                "renderTypeList": ["textarea", "reference"],
                "valueType": "any",
                "required": True,
                "isRichText": False,
                "maxLength": 100000,
                "label": "common:core.module.input.label.Response content",
                "value": "{{$final_validate_code.finalJsonText$}}",
            }
        ],
        "outputs": [],
    }


def edge(source: str, target: str) -> dict:
    return {
        "source": source,
        "target": target,
        "sourceHandle": f"{source}-source-right",
        "targetHandle": f"{target}-target-left",
    }


MERGE_JSON_CODE = r'''function main({ userRequirement, templatesText, mergePlanText }) {
  function stripFence(text) {
    return String(text || "").trim().replace(/^```json\s*/i, "").replace(/^```\s*/i, "").replace(/```$/i, "").trim();
  }
  function safeParse(text, fallback) {
    try { return JSON.parse(stripFence(text)); } catch (e) { return fallback; }
  }
  function clone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }
  function byId(templates) {
    const map = {};
    for (const item of templates.templates || []) map[item.templateId] = item;
    return map;
  }
  function inferPlan(req) {
    const text = String(req || "");
    const needFileUpload = /文件|上传|文档|pdf|doc|总结/.test(text);
    const needCodeAnalysis = needFileUpload && /分析|统计|行数|词|敏感/.test(text);
    if (needFileUpload) {
      return {
        workflowType: needCodeAnalysis ? "file_upload_code_summary" : "file_upload_summary",
        nodes: needCodeAnalysis
          ? ["workflowStart_file", "readFiles", "code_file_analysis", "textEditor", "chatNode", "answerNode"]
          : ["workflowStart_file", "readFiles", "textEditor", "chatNode", "answerNode"],
        needFileUpload,
        needCodeAnalysis,
        finalAnswerNodeId: "summarize",
        manualConfig: ["导入后选择模型", "根据业务修改 AI systemPrompt"]
      };
    }
    return {
      workflowType: "text_ai_reply",
      nodes: ["workflowStart_text", "textEditor", "chatNode", "answerNode"],
      needFileUpload: false,
      needCodeAnalysis: false,
      finalAnswerNodeId: "summarize",
      manualConfig: ["导入后选择模型", "根据业务修改 AI systemPrompt"]
    };
  }
  function getInput(node, key) {
    return (node.inputs || []).find(item => item.key === key);
  }
  function sanitizeNode(node) {
    if (node.flowNodeType === "chatNode") {
      const model = getInput(node, "model");
      if (model) {
        model.renderTypeList = ["settingLLMModel", "reference"];
        model.value = "";
      }
    }
    return node;
  }
  function makeEdge(source, target) {
    return {
      source,
      target,
      sourceHandle: source + "-source-right",
      targetHandle: target + "-target-left"
    };
  }
  const templates = safeParse(templatesText, { templates: [] });
  const templateMap = byId(templates);
  let plan = safeParse(mergePlanText, null);
  if (!plan || !Array.isArray(plan.nodes) || plan.nodes.some(id => !templateMap[id])) plan = inferPlan(userRequirement);

  const nodes = [];
  const add = (templateId) => {
    const item = templateMap[templateId];
    if (!item) return;
    nodes.push(sanitizeNode(clone(item.node)));
  };

  if (plan.needFileUpload) add("workflowStart_file");
  else add("workflowStart_text");

  if (plan.needFileUpload) add("readFiles");
  if (plan.needCodeAnalysis) add("code_file_analysis");
  add("textEditor");
  add("chatNode");
  add("answerNode");

  const existing = new Set(nodes.map(n => n.nodeId));
  const formatPrompt = nodes.find(n => n.nodeId === "formatPrompt");
  if (formatPrompt) {
    const input = getInput(formatPrompt, "system_textareaInput");
    if (input && !plan.needCodeAnalysis) {
      input.value = "请根据以下文件内容生成中文总结：\n\n{{$readFiles.system_text$}}\n\n请输出简洁、清晰的总结。";
    }
    if (input && !plan.needFileUpload) {
      input.value = "用户需求：\n{{$start.userChatInput$}}\n\n请根据用户需求直接回复。";
    }
  }
  const summarize = nodes.find(n => n.nodeId === "summarize");
  if (summarize && !plan.needFileUpload) {
    const userChatInput = getInput(summarize, "userChatInput");
    if (userChatInput) userChatInput.value = ["formatPrompt", "system_text"];
  }
  const answer = nodes.find(n => n.nodeId === "answer");
  if (answer) {
    const text = getInput(answer, "text");
    if (text) text.value = "{{$summarize.answerText$}}";
  }

  const sequence = ["start"];
  if (existing.has("readFiles")) sequence.push("readFiles");
  if (existing.has("analyzeContent")) sequence.push("analyzeContent");
  sequence.push("formatPrompt", "summarize", "answer");
  const edges = [];
  for (let i = 0; i < sequence.length - 1; i++) edges.push(makeEdge(sequence[i], sequence[i + 1]));

  const chatConfig = {
    welcomeText: "#### FastGPT V6 生成的工作流模板\n导入后请手动选择模型，并按业务调整提示词。",
    variables: [],
    scheduledTriggerConfig: { cronString: "", timezone: "Etc/GMT-8", defaultPrompt: "" },
    fileSelectConfig: plan.needFileUpload ? {
      canSelectFile: true,
      canSelectImg: false,
      maxFiles: 1,
      canSelectVideo: false,
      canSelectAudio: false,
      canSelectCustomFileExtension: false,
      customFileExtensionList: []
    } : {
      canSelectFile: false,
      canSelectImg: false,
      maxFiles: 0,
      canSelectVideo: false,
      canSelectAudio: false,
      canSelectCustomFileExtension: false,
      customFileExtensionList: []
    },
    autoExecute: { open: false, defaultPrompt: "" }
  };

  return {
    workflowJsonText: JSON.stringify({ nodes, edges, chatConfig }, null, 2),
    mergePlanJsonText: JSON.stringify(plan, null, 2)
  };
}'''


FINAL_VALIDATE_CODE = r'''function main({ workflowJsonText, reviewText }) {
  function stripFence(text) {
    return String(text || "").trim().replace(/^```json\s*/i, "").replace(/^```\s*/i, "").replace(/```$/i, "").trim();
  }
  function err(errors, msg) { errors.push(msg); }
  function getInput(node, key) {
    return (node.inputs || []).find(item => item.key === key);
  }
  function codeInput(node) {
    const item = getInput(node, "code");
    return item ? String(item.value || "") : "";
  }
  function extractParams(code) {
    const out = new Set();
    String(code || "").replace(/params\.([A-Za-z_][A-Za-z0-9_]*)/g, function(_, key) {
      out.add(key);
      return "";
    });
    return Array.from(out);
  }
  function extractReturnKeys(code) {
    const match = String(code || "").match(/return\s*\{([\s\S]*?)\};/);
    if (!match) return [];
    const body = match[1];
    const segments = [];
    let current = "";
    let depth = 0;
    for (let i = 0; i < body.length; i++) {
      const ch = body[i];
      if (ch === "{") depth++;
      if (ch === "}") depth = Math.max(0, depth - 1);
      if (ch === "," && depth === 0) {
        segments.push(current.trim());
        current = "";
      } else {
        current += ch;
      }
    }
    if (current.trim()) segments.push(current.trim());
    const keys = [];
    for (const segment of segments) {
      const colon = segment.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*:/);
      const shorthand = segment.match(/^([A-Za-z_][A-Za-z0-9_]*)$/);
      if (colon) keys.push(colon[1]);
      else if (shorthand) keys.push(shorthand[1]);
    }
    return Array.from(new Set(keys));
  }
  const errors = [];
  let workflow;
  const original = stripFence(workflowJsonText);
  try {
    workflow = JSON.parse(original);
  } catch (e) {
    return { finalJsonText: JSON.stringify({ pass: false, errors: ["JSON.parse failed: " + e.message], workflowJsonText: original }, null, 2) };
  }
  if (!workflow || typeof workflow !== "object") err(errors, "Top level must be object.");
  if (!Array.isArray(workflow.nodes)) err(errors, "nodes must be array.");
  if (!Array.isArray(workflow.edges)) err(errors, "edges must be array.");
  if (!workflow.chatConfig || typeof workflow.chatConfig !== "object") err(errors, "chatConfig must be object.");
  const nodes = Array.isArray(workflow.nodes) ? workflow.nodes : [];
  const edges = Array.isArray(workflow.edges) ? workflow.edges : [];
  const nodeIds = new Set();
  for (const node of nodes) {
    if (!node.nodeId) err(errors, "Node missing nodeId.");
    if (nodeIds.has(node.nodeId)) err(errors, "Duplicate nodeId: " + node.nodeId);
    nodeIds.add(node.nodeId);
    if (!Array.isArray(node.inputs || [])) err(errors, node.nodeId + " inputs must be array.");
    if (!Array.isArray(node.outputs || [])) err(errors, node.nodeId + " outputs must be array.");
  }
  for (const edge of edges) {
    if (!nodeIds.has(edge.source)) err(errors, "edge source not found: " + edge.source);
    if (!nodeIds.has(edge.target)) err(errors, "edge target not found: " + edge.target);
    if (edge.sourceHandle !== edge.source + "-source-right") err(errors, "bad sourceHandle: " + edge.sourceHandle);
    if (edge.targetHandle !== edge.target + "-target-left") err(errors, "bad targetHandle: " + edge.targetHandle);
  }
  const workflowForTextScan = JSON.parse(JSON.stringify(workflow));
  for (const node of workflowForTextScan.nodes || []) {
    for (const input of node.inputs || []) {
      if (input.key === "code") input.value = "";
    }
  }
  const text = JSON.stringify(workflowForTextScan);
  if (/\$\{[^}]+\}/.test(text)) err(errors, "Found ${...} variable format outside code.value.");
  if (/\{summarize\.answerText\}/.test(text)) err(errors, "Found single-brace summarize answer variable.");
  if (/REPLACE_ME|YOUR_WEBHOOK|\bxxx\b/i.test(text)) err(errors, "Found placeholder text.");
  if (/gpt-3\.5-turbo|qwen-plus|selectModel/i.test(text)) err(errors, "Found invented model or selectModel.");
  const readFiles = nodes.find(n => n.nodeId === "readFiles");
  if (readFiles) {
    const input = getInput(readFiles, "fileUrlList");
    if (JSON.stringify(input && input.value) !== JSON.stringify([["start", "userFiles"]])) {
      err(errors, "readFiles.fileUrlList must be [[\"start\",\"userFiles\"]].");
    }
  }
  const textEditor = nodes.find(n => n.nodeId === "formatPrompt");
  if (readFiles && textEditor) {
    const value = String((getInput(textEditor, "system_textareaInput") || {}).value || "");
    if (!value.includes("{{$readFiles.system_text$}}")) err(errors, "textEditor must reference {{$readFiles.system_text$}}.");
  }
  const answer = nodes.find(n => n.flowNodeType === "answerNode");
  if (!answer) err(errors, "Missing answerNode.");
  else {
    const value = (getInput(answer, "text") || {}).value;
    if (!value) err(errors, "answerNode.text is empty.");
    if (typeof value !== "string" || !/\{\{\$[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\$\}\}/.test(value)) {
      err(errors, "answerNode.text must use {{$node.output$}} format.");
    }
  }
  for (const node of nodes.filter(n => n.flowNodeType === "code")) {
    const code = codeInput(node);
    if (/split\('\\n'\)/.test(code)) err(errors, node.nodeId + " code.value contains split('\\n').");
    const inputKeys = new Set((node.inputs || []).map(item => item.key));
    for (const key of extractParams(code)) {
      if (!inputKeys.has(key)) err(errors, node.nodeId + " params." + key + " has no same-key input.");
    }
    const outputKeys = new Set((node.outputs || []).map(item => item.key || item.id));
    for (const key of extractReturnKeys(code)) {
      if (!outputKeys.has(key)) err(errors, node.nodeId + " return field " + key + " has no same-key output.");
    }
  }
  if (errors.length) {
    return { finalJsonText: JSON.stringify({ pass: false, errors, reviewText, workflowJsonText: original }, null, 2) };
  }
  return { finalJsonText: JSON.stringify(workflow, null, 2) };
}'''


def build() -> dict:
    templates_text = TEMPLATES_PATH.read_text(encoding="utf-8")
    templates = json.loads(templates_text)
    user_guide_template = next(item for item in templates["templates"] if item["templateId"] == "userGuide")

    nodes = [
        deepcopy(user_guide_template["node"]),
        {
            "nodeId": "start",
            "name": "流程开始",
            "avatar": "core/workflow/template/workflowStart",
            "flowNodeType": "workflowStart",
            "position": {"x": 500, "y": -320},
            "inputs": [
                {
                    "key": "userChatInput",
                    "renderTypeList": ["reference", "textarea"],
                    "valueType": "string",
                    "label": "workflow:user_question",
                    "required": True,
                }
            ],
            "outputs": [
                {
                    "id": "userChatInput",
                    "key": "userChatInput",
                    "type": "static",
                    "valueType": "string",
                    "label": "用户需求",
                }
            ],
        },
        text_editor(
            "text_single_node_templates",
            "单节点模板库",
            templates_text,
            900,
            -520,
        ),
        chat_node(
            "merge_plan_ai",
            "合并方案 AI",
            (
                "你是 FastGPT V6 合并方案规划器。只输出合并方案 JSON，不要输出完整 FastGPT 工作流 JSON。\n"
                "只允许从 single_node_templates.json 的 templateId 中选择节点。\n"
                "第一阶段只支持 file_upload_summary、file_upload_code_summary、text_ai_reply。\n"
                "禁止输出 nodes/edges/chatConfig 的完整工作流结构。"
            ),
            ["start", "userChatInput"],
            1300,
            -320,
            "json_object",
        ),
        code_node(
            "merge_json_code",
            "代码合并 JSON",
            MERGE_JSON_CODE,
            [
                {
                    "key": "userRequirement",
                    "renderTypeList": ["reference"],
                    "valueType": "string",
                    "required": True,
                    "label": "userRequirement",
                    "value": ["start", "userChatInput"],
                },
                {
                    "key": "templatesText",
                    "renderTypeList": ["reference"],
                    "valueType": "string",
                    "required": True,
                    "label": "templatesText",
                    "value": ["text_single_node_templates", "system_text"],
                },
                {
                    "key": "mergePlanText",
                    "renderTypeList": ["reference"],
                    "valueType": "string",
                    "required": True,
                    "label": "mergePlanText",
                    "value": ["merge_plan_ai", "answerText"],
                },
            ],
            [
                {
                    "id": "workflowJsonText",
                    "key": "workflowJsonText",
                    "type": "dynamic",
                    "valueType": "string",
                    "label": "workflowJsonText",
                },
                {
                    "id": "mergePlanJsonText",
                    "key": "mergePlanJsonText",
                    "type": "dynamic",
                    "valueType": "string",
                    "label": "mergePlanJsonText",
                },
            ],
            1700,
            -320,
        ),
        chat_node(
            "review_ai",
            "整理检查 AI",
            (
                "你是 FastGPT V6 结果检查器。只输出检查报告 JSON："
                "{\"pass\":true,\"errors\":[],\"warnings\":[],\"fixSuggestion\":\"\"}。\n"
                "不要自由重写业务，不要输出完整工作流 JSON。重点检查是否符合用户需求、是否需要人工选择模型、是否仍有明显变量链路问题。"
            ),
            ["merge_json_code", "workflowJsonText"],
            2100,
            -320,
            "json_object",
        ),
        code_node(
            "final_validate_code",
            "最终代码校验",
            FINAL_VALIDATE_CODE,
            [
                {
                    "key": "workflowJsonText",
                    "renderTypeList": ["reference"],
                    "valueType": "string",
                    "required": True,
                    "label": "workflowJsonText",
                    "value": ["merge_json_code", "workflowJsonText"],
                },
                {
                    "key": "reviewText",
                    "renderTypeList": ["reference"],
                    "valueType": "string",
                    "required": True,
                    "label": "reviewText",
                    "value": ["review_ai", "answerText"],
                },
            ],
            [
                {
                    "id": "finalJsonText",
                    "key": "finalJsonText",
                    "type": "dynamic",
                    "valueType": "string",
                    "label": "finalJsonText",
                }
            ],
            2500,
            -320,
        ),
        answer_node(),
    ]
    edges = [
        edge("start", "text_single_node_templates"),
        edge("text_single_node_templates", "merge_plan_ai"),
        edge("merge_plan_ai", "merge_json_code"),
        edge("merge_json_code", "review_ai"),
        edge("review_ai", "final_validate_code"),
        edge("final_validate_code", "answer"),
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "chatConfig": {
            "welcomeText": "#### FastGPT V6 单节点模板合并生成器\n输入工作流目标后，系统会先生成合并方案，再由代码合并固定单节点模板。",
            "variables": [],
            "scheduledTriggerConfig": {
                "cronString": "",
                "timezone": "Etc/GMT-8",
                "defaultPrompt": "",
            },
            "fileSelectConfig": {
                "canSelectFile": False,
                "canSelectImg": False,
                "maxFiles": 0,
                "canSelectVideo": False,
                "canSelectAudio": False,
                "canSelectCustomFileExtension": False,
                "customFileExtensionList": [],
            },
            "autoExecute": {"open": False, "defaultPrompt": ""},
        },
        "_v6Metadata": {
            "schema": "v6_single_node_template_generator",
            "description": "AI only creates merge plan; code merges fixed node templates and validates final JSON.",
        },
    }


def main() -> None:
    workflow = build()
    OUTPUT_PATH.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
