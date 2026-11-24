"""统一随机种子（P4/W1 前置）：训练可复现所需的 seed 设置（不启动训练）。"""
import random

try:  # numpy 在训练/评测环境必装；测试环境也可能没有
    import numpy as _np
except Exception:  # noqa: BLE001
    _np = None


def seed_everything(seed):
    """设置 Python random 与 numpy 全局种子；torch 存在时一并设置。

    返回实际应用的组件，便于在 run.json 记录可复现信息。
    """
    if seed is None:
        return {'seed': None, 'applied': []}
    seed = int(seed)
    applied = ['random']
    random.seed(seed)
    if _np is not None:
        _np.random.seed(seed)
        applied.append('numpy')
    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        applied.append('torch')
    except Exception:  # noqa: BLE001
        pass
    return {'seed': seed, 'applied': applied}
