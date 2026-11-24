"""Compatibility entry: delegates to the fixed per-case evaluator without global taskkill."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from eval_v22_parallel import main

if __name__=='__main__':
    raise SystemExit(main())
