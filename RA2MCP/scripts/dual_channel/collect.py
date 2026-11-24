"""Use the existing Python 3.10 control runtime without changing installed packages.

Recommended interpreter: F:/conda/envs/python_mmx_LLM/python.exe. The project
venv's pure Python dependencies are appended only as a fallback. The collector
still requires its full preflight and a fresh explicitly recorded run entry.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if sys.version_info[:2] != (3, 10):
    raise SystemExit("Use the existing Python 3.10 control runtime for collection")
sys.path.append(str(ROOT / ".venv-rl/Lib/site-packages"))
sys.path.insert(0, str(ROOT / "scripts"))
from dual_channel.run_teacher import main

if __name__ == "__main__":
    sys.exit(main())
