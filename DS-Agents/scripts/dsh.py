"""从任意工作目录调用本仓库的 DSH 入口（CLI）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ds_agents.dsh_entry import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
