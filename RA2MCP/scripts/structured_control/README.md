# structured-ground/1

独立于旧301的结构化控制原型。当前实现接口/网络/串行教师/数据reader；保存的模型是**未训练初始化**，不声称模型会对战。

| 文件 | 职责 |
|---|---|
| `contract.py` | 冻结类型字典、实体出现epoch、全军固定分组、条件掩码、动作解码与过期绑定拒绝 |
| `policy.py` | 实体与生产编码、GRU、条件动作头与可重算联合log_prob；严格checkpoint命名空间 |
| `serial_teacher.py` | 每次新观测重新规划，最久未选角色优先；未选命令不冒充已发送 |
| `collect_serial.py` | 默认预检；每次至多一条原生发送再真实capture；一次性独立run授权与清理 |
| `data.py` | 串行连续性、实际发送关联、快照hash与用途准入 |
| `audit_serial.py` | 从原始观测、真实历史动作/ACK和原有生产冷却重建输入 |
| `dataset.py` | 整局reader、标签候选mask、整局分区防泄漏；不执行训练 |

## 数据与概率

实体上限256，组上限8（当前固定5种角色，余3槽关闭），生产96，待放置建筑4，地图坐标各256。超容量明确拒绝，不截掉单位。类型ID来自排序冻结manifest，UNK=0；原生地址不作为网络特征。实体身份仍是启发式epoch，不是引擎UniqueID。

动作family为NOOP/DEPLOY/PRODUCE/PLACE/STOP/MOVE/ATTACK。只采样对应的参数；log_prob只累加活跃因子。目标使用实体指针分布，坐标y条件依赖x；NONE目标槽对当前ATTACK关闭。DEPLOY绑定观测中首个合法MCV，不增加未批准的多MCV选择头。

固定组构建器是公开的脚本贡献：前排、Prism、步兵/IFV、首个GI常驻侦察、受伤撤退。编队成员尚未学习。MOVE只检查地图边界；攻击仅检查合法可见敌方身份；射程/路径可达性不冒充已验证。PLACE输出anchor，原生PlaceQuery解析后的坐标单独保存，因此放置标签暂时不准入候选loss。

reader直接给新模型提供该决策保存的snapshot和action，不拼造1801特征。实际拒绝/NOOP仍保留在循环历史内，但候选标签mask关闭；每局从h0=0开始，中途chunk状态必须来自实际模型历史。数据目前只有一个独立serial episode，未划分训练/验证/测试，所有formal training/PPO标记false。

## 只读验证

在项目根运行：

```powershell
& 'F:/conda/envs/python_mmx_LLM/python.exe' -B -m unittest scripts.structured_control.tests.test_control -v
& '.venv/Scripts/python.exe' -B scripts/structured_control/audit_serial.py --run _runs_g0l1/v3-structured-01 --out NEW_AUDIT_PATH.json
```

实机collector使用既有Python3.10与14621，在`test-game*`中运行；先新建run与一次性条目，通过不带`--execute`的预检。当前STRUCTURED-01已消费，不能复用。现有保护锁、fail-closed传输与无自动重试规则继续生效。

`collect_serial`复用既有raw发送helper，因此底层action记录的decision_source仍可能是formation_teacher；新structured schema、命令上下文与control metadata才是该批数据的正式来源说明。底层启动日志里的“PPO launched”也不是训练证据。

详见`reports/structured-control-v1-2026-11-02/REPORT.md`。
