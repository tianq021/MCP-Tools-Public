"""无主之地2（Borderlands 2）存档 / 武器代码工具包。

本包把 vendor/borderlands2 里的开源存档解析库（apocalyptech / loot-midget 的
borderlands2 项目，MIT 风格的个人工具项目）作为底层编解码器，在上层提供：

- 存档读取与摘要（角色、等级、技能点、物品数量）
- 技能点修改（未分配技能点）
- 物品 / 武器代码（BL2(...)）的导入导出（"同步武器"）
- 黑枪代码解码、改配件、重新编码（"制作黑枪"）
- 自动备份，所有写操作都可回滚

仅用于单机娱乐，不要在联机/竞技场景使用。
"""

from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.1.0"

_ROOT = Path(__file__).resolve().parent.parent
_VENDOR = _ROOT / "vendor" / "borderlands2"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    # 让 `import borderlands` 找到 vendored 的解析库
    sys.path.insert(0, str(_VENDOR))

DATA_DIR = _ROOT / "data"
LOCAL_DIR = _ROOT / ".local"
BACKUP_DIR = LOCAL_DIR / "backups"
