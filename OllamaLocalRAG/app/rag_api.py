from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ollama_rag.api.http_server import main  # noqa: E402


if __name__ == "__main__":
    if "--project-root" not in sys.argv:
        sys.argv[1:1] = ["--project-root", str(PROJECT_ROOT)]
    main()
