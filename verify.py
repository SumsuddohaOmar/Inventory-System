"""Run the portable, socket-free checks with this project's Python environment.

The separate HTTP integration suite still needs a normal Windows session.
This runner never opens or modifies data/inventory.sqlite3.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parent


def main():
    os.chdir(ROOT)
    loader=unittest.TestLoader()
    suite=unittest.TestSuite()
    for name in ("categories","stock","launcher","workflows","imports_templates","monthly_report","history_imports","release"):
        suite.addTests(loader.discover(str(ROOT/"tests"),pattern=f"test_{name}.py"))
    success=unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()
    node=shutil.which("node")
    if node:
        for script in sorted((ROOT/"app/static").glob("*.js")):
            if subprocess.run([node,"--check",str(script)]).returncode:success=False
        if subprocess.run([node,"--test","tests/offline.test.cjs"]).returncode:success=False
    else:
        print("Node.js not installed: JavaScript checks skipped. Node is optional for development, never required to run the app.")
    return 0 if success else 1


if __name__=="__main__":sys.exit(main())
