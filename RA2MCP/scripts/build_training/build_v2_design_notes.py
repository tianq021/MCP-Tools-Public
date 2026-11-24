"""建设训练 V2 阶段一：BUILD_ONLY 动作掩码 + 预部署基地 + V2 奖励的插件式补丁说明。

本目录仅为 V2 实现代码；正式模块见 build_env_v2.py / build_reward_v2.py /
train_build_v2.py / run_build_v2.py。此文件用于记录设计要点，便于审计与复现。
"""

DESIGN_NOTES = """
V2 实现要点（任务书二/三/四/五/六/七节）：
1. 动作空间不变（ACTION_N=301），BUILD_ONLY 掩码由真实常量推导：
   allowed = {0} ∪ [PRODUCE_START, UNIT_START) = 等待 + 建筑/单位生产(2..97) + 建筑放置(98..129)；
   在此基础上叠加 full_spec 的原生合法性掩码，无效动作一律屏蔽。
2. 预部署基地：因多人模式不实例化地图 [Houses]（实测地图预置 player 建筑 owner=Americans 被丢弃），
   改为环境 warmup 用真实游戏命令完成 MCV 展开 + 电厂生产/放置；模型控制从已部署基地开始，
   warmup 期间无任何固定脚本代替模型决策。
3. 模型：复制 R1（不覆盖），保留特征提取网络，重初始化 action_net / value_net 与优化器状态。
4. 奖励 reward_build_v2：建筑 +1.0（连续 2 帧确认）、单位 +0.2（连续 2 帧、每类型每局上限 20）；
   只奖励本 Episode 新产生实体，prime() 排除初始基地/单位；处理地址复用（存活世代）与所有权校验。
5. 训练：默认 3000 transitions / 最多 25 分钟；每 250 步 checkpoint，每 500 步评估；
   前 500 步无任何正奖励 → 暂停并输出 early_stop_diagnosis.json；奖励与建设行为改善 → 自动扩展至 5000~10000 步。
6. 资源：5 秒采样；提交内存 85% / 物理可用 3GB / GPU 剩余 500MB / 2 分钟提交增长 2GB → STOP 优雅暂停。
"""

if __name__ == '__main__':
    print(DESIGN_NOTES)
