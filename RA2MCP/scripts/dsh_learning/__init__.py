"""RA2MCP 自主学习与技能成长系统 V1（离线、只读为主）。

模块：
  common.py        共享工具与当前契约读取
  model_registry.py 模型资产登记（哈希/结构/历史）
  compatibility.py  兼容性判定（加载/推理/续训/只读评估）
  trace_parser.py   既有对局轨迹离线指标解析
  skill_metrics.py  S1-S5 技能定义与掌握判定
  skill_eval.py     可运行的技能评价 CLI
  efficiency.py     学习效率记录
  forgetting.py     遗忘检测 + 持续学习方法兼容性分析
  manager.py        自主学习管理器（决策层，不启动训练）
"""

VERSION = '0.1.0'
