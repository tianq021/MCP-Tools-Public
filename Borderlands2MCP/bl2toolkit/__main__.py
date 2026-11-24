"""允许 `python -m bl2toolkit ...` 直接调用 CLI。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
