"""P0-3 恢复器测试运行器（推荐入口）。

为什么需要独立 runner：Windows spawn 会重新导入 `__main__` 模块；若直接把 unittest
测试脚本当主模块运行，子进程导入主模块时可能与测试运行器互相干扰。用本 runner
作为主模块（带 `if __name__ == '__main__'` 守卫），测试模块只在父进程由 loader 加载，
与本项目其余基于 spawn 的训练/评测入口保持一致。

运行：
  PYTHONPATH=E:\\RA2MCP\\.venv-rl\\Lib\\site-packages;E:\\RA2MCP\\scripts
  python scripts/dsh_rl/run_recovering_tests.py
"""
import sys
import unittest


def main():
    loader = unittest.TestLoader()
    names = sys.argv[1:] or ["dsh_rl.test_recovering_vec"]
    suite = loader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
