# DSH 本地训练框架（RA2 学习）

框架与具体深度学习库解耦：**数据/特征/动作规范 → 训练后端（torch/numpy 可切换）→ 导出框架无关权重 → 控制器侧轻量推理**。
设计依据见 `reports/dsh-env-survey.md` 与 `reports/dsh-train-framework-decision.md`。

## 目录

| 文件 | 作用 |
|---|---|
| `spec.py` | 特征布局 v1（126 维）与动作空间（12 类），改布局必须升 `FEATURE_VERSION` |
| `episode_io.py` | 读/写对局 episode JSONL（与 `dsh_episode_runner` 输出一致） |
| `features.py` | 单行状态 → obs 向量；动作标签映射；**可见性卫生检查**（不对未见目标打标签） |
| `dataset.py` | episodes → 训练/验证 `dataset.npz` + `info.json`（按 episode 划分） |
| `model.py` | MLP 定义与三种后端：torch（训练）、numpy（训练/推理）、stdlib（零依赖推理） |
| `train_bc.py` | 行为克隆训练入口（`--backend auto/torch/numpy`），导出 `weights.npz/.json`、`norm.json`、`metrics.json` |
| `eval_offline.py` | 离线评估（acc/macro-F1/混淆矩阵）+ 单样本推理延迟（p50/p95） |
| `infer.py` | 控制器侧推理插件：**默认 disabled**，`enabled=True` 才允许输出动作 |
| `export_onnx.py` | 可选导出 ONNX（torch 环境）；不装 onnx 也能用 numpy 推理 |
| `make_demo_episodes.py` | 合成 demo 轨迹（非游戏数据），用于无游戏时冒烟测试 |
| `configs/baseline.json` | 默认超参 |

## 推荐环境（已在本机实测可用）

| 用途 | 环境 |
|---|---|
| 训练 / 导出 | `F:\conda\envs\python_mmx_LLM\python.exe`（Python 3.10.20，torch 2.5.1+cu121，CUDA 可用） |
| 备选训练 | `F:\conda\envs\qiaohaotian_new\python.exe`（torch 2.10.0+cu126） |
| 控制器推理 | `E:\RA2MCP\.venv`（只需 numpy；不装 torch） |

## 冒烟流程（无游戏也能跑通）

```powershell
$py = 'F:\conda\envs\python_mmx_LLM\python.exe'
$env:PYTHONPATH='E:\RA2MCP\scripts\dsh_train'

# 1) 合成 demo 轨迹（非游戏数据）
& $py scripts\dsh_train\make_demo_episodes.py --out reports\dsh-learning\episodes-demo

# 2) 构建数据集
& $py scripts\dsh_train\dataset.py --episodes reports\dsh-learning\episodes-demo `
    --out reports\dsh-learning\dataset-demo

# 3) 训练（自动选 torch+CUDA）
& $py scripts\dsh_train\train_bc.py --data reports\dsh-learning\dataset-demo `
    --out reports\dsh-learning\models\bc-demo --backend auto --epochs 40 --verbose

# 4) 离线评估 + 延迟
& $py scripts\dsh_train\eval_offline.py --model reports\dsh-learning\models\bc-demo `
    --data reports\dsh-learning\dataset-demo

# 5) 控制器侧推理（不启用会拒绝输出动作）
E:\RA2MCP\.venv\Scripts\python.exe scripts\dsh_train\infer.py `
    --model reports\dsh-learning\models\bc-demo --bench 1000 --enabled
```

## 真实数据接入（后续步骤）

1. 用对局采集器把真实 episode 写到 `reports/dsh-learning/episodes/`（schema 同 demo）；
2. `dataset.py --episodes reports/dsh-learning/episodes --out ...`；
3. `train_bc.py` 训练 → `eval_offline.py` 只信**按 episode 划分的验证集**；
4. 接控制器：`LocalModel(model_dir, enabled=配置开关)`，每 tick 取 `act(obs)`，
   置信度低于阈值/模型未启用时**回退规则策略**；所有动作仍过可见性契约与安全层。

## 安全与合规

- 训练标签只来自 `visible_enemies`（已按 `Object.cell_shrouded==False` 过滤），
  `features.label_from_row` 会丢弃"攻击不可见目标"的样本；
- `infer.py` 默认 disabled，必须显式允许才输出动作；不联网；
- 不修改游戏文件、不卖基地、不伪造状态；训练只用本机离线副本数据。
