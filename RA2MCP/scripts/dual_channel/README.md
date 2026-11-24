# RA2MCP V3 第一阶段

这是教师候选和真实数据基础设施，不是已经训练好的自主学习模型。

- `ai_teacher/strategic_teacher.py`：保持现有301映射、保证经济服务、命令去抖、单槽轮换的教师候选。
- `skills.py`：技能实例生命周期；观测状态条件满足只标 STATE_DERIVED。
- `data.py`：追加式 sidecar、来源/终局/用途准入及哈希校验。
- `collect.py` / `run_teacher.py`：每次调用至多一局，必须有新 run 目录与未消耗记录；默认只做预检。
- `dataset.py`：导出精确301教师标签候选，保持1801特征；不自动设置训练资格或随机逐行切分。
- `evolution.py`：从记录产生受限数值参数候选，不能自动晋升、执行生成代码或训练模型。

## 离线使用

在项目根目录执行（输出路径必须未存在）：

```powershell
& '.venv/Scripts/python.exe' -B -m unittest scripts.ai_teacher.tests.test_strategic_teacher -v
& '.venv/Scripts/python.exe' -B scripts/dual_channel/dataset.py --run _runs_g0l1/v3-teacher-04 --out NEW_DATASET_DIRECTORY
& '.venv/Scripts/python.exe' -B scripts/dual_channel/evolution.py --run _runs_g0l1/v3-teacher-04 --out NEW_CANDIDATE_JSON
```

## 实机入口

使用项目已有的基础 Python 3.10 环境：`F:\conda\envs\python_mmx_LLM\python.exe`。`collect.py` 仅追加项目 venv 的依赖查找路径，不安装包、不改权限。实际启动前核验共享锁、无既有游戏进程、端口可 **bind**、隔离副本文件、可用磁盘、授权run/路径/时长和一次性claim。

不带 `--execute` 只预检。真实运行必须在现有授权登记和实验预算内，不能复用已消费的条目或目录。当前本轮授权已消费；本 README 不创建新授权。

数据里的候选 BC label 与 PPO actor/effect label 分开。所有教师轨迹的 PPO actor 准入为false；当前单局候选集不具备正式BC训练资格。已有回报来自未改变的V4环境，不能声称每条回报均有直接动作因果证据。

本轮证据见 `reports/dual-channel-v3-implementation-2026-11-02/REPORT.md`。后续核心问题是301的8端点控制覆盖，不能靠持续增加同一接口上的训练步数解决。
