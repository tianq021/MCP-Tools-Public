"""从任意工作目录启动本项目的独立 HTTP API。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ds_agents.api_server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
