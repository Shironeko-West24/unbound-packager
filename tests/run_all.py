#!/usr/bin/python3 -I
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "lib"))


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for sub in ("unit", "integration"):
        suite.addTests(loader.discover(os.path.join(HERE, sub), pattern="test_*.py", top_level_dir=HERE))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    total = result.testsRun
    bad = len(result.failures) + len(result.errors)
    print(f"自動テスト: {total} 件中 {total - bad} 件合格、{bad} 件失敗、{len(result.skipped)} 件スキップ")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
