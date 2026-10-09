#!/usr/bin/env python3
"""Run every standalone Email Watchdog regression file deterministically."""
from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    tests = sorted(root.glob("test_*.py"))
    if not tests:
        raise SystemExit("no test files discovered")
    for path in tests:
        print(f"RUN={path.name}", flush=True)
        env = dict(os.environ)
        existing_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(
            value for value in (str(root.parent / "scripts"), existing_path) if value
        )
        result = subprocess.run(
            [sys.executable, str(path)], cwd=root.parent, env=env, check=False
        )
        if result.returncode:
            print(f"FAIL={path.name} rc={result.returncode}", file=sys.stderr)
            return result.returncode
    print(f"EMAIL_TEST_FILES_PASS={len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
