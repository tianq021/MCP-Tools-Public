"""Entry point: RA2 episode collector v2 (raw observations + raw actions).

See scripts/dsh_episode/runner.py for the implementation and options.

Examples:
  .venv\\Scripts\\python.exe scripts\\dsh_episode_runner.py
  .venv\\Scripts\\python.exe scripts\\dsh_episode_runner.py --attach --keep-game
  .venv\\Scripts\\python.exe scripts\\dsh_episode_runner.py --max-seconds 240
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from dsh_episode.runner import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
