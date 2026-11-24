"""V1 自动化测试入口：离线单元测试（不启动游戏/训练）。

用法（在 E:\\RA2MCP 下）：
  .venv\\Scripts\\python.exe scripts\\dsh_learning\\run_tests.py
或（需要 torch/sb3 的深度检查另行执行 model_registry.py --deep）：
  PYTHONPATH=.venv-rl\\Lib\\site-packages python scripts/dsh_learning/run_tests.py
"""
import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / 'scripts'))


def main():
    loader = unittest.TestLoader()
    suite = loader.discover(str(_ROOT / 'scripts/dsh_learning/tests'),
                            top_level_dir=str(_ROOT / 'scripts'))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print(f"TESTS_RUN={result.testsRun} FAILURES={len(result.failures)} "
          f"ERRORS={len(result.errors)} SKIPPED={len(result.skipped)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
