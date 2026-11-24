"""Compatibility API for metrics. The old auto-reset vector evaluation entry is retired.

Use scripts/dsh_learning/eval_v22_parallel.py for live evaluation or --plan-only.
"""
import hashlib
import pathlib
import sys

ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'scripts/dsh_learning'))
from eval_v22_metrics import (METRIC_KEYS,economy_observed,episode_metrics,paired_compare,summarize)


def sha256_file(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for chunk in iter(lambda:handle.read(1<<20),b''): digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    raise SystemExit('Legacy auto-reset evaluation is retired. Use scripts/dsh_learning/eval_v22_parallel.py; start with --plan-only.')


if __name__=='__main__': main()
