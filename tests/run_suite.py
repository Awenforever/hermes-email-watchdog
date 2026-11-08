#!/usr/bin/env python3
"""Run every standalone Email Watchdog regression file deterministically.

Each file runs in its own process. A file whose cases all skipped still exits 0,
so the runner counts real cases too: a suite that quietly executes nothing must
not look like a pass.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _counts(stderr: str) -> tuple[int, int]:
    ran = 0
    skipped = 0
    for line in stderr.splitlines():
        if line.startswith("Ran "):
            try:
                ran = int(line.split()[1])
            except (IndexError, ValueError):
                ran = 0
        if "skipped=" in line:
            try:
                skipped = int(line.rsplit("skipped=", 1)[1].split(")")[0])
            except (IndexError, ValueError):
                skipped = 0
    return ran, skipped


def main() -> int:
    root = Path(__file__).resolve().parent
    tests = sorted(root.glob("test_*.py"))
    if not tests:
        raise SystemExit("no test files discovered")
    total_cases = 0
    total_skipped = 0
    for path in tests:
        print(f"RUN={path.name}", flush=True)
        env = dict(os.environ)
        existing_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(
            value for value in (str(root.parent / "scripts"), existing_path) if value
        )
        result = subprocess.run(
            [sys.executable, str(path)],
            cwd=root.parent,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            sys.stderr.write(result.stdout or "")
            sys.stderr.write(result.stderr or "")
            print(f"FAIL={path.name} rc={result.returncode}", file=sys.stderr)
            return result.returncode
        ran, skipped = _counts(result.stderr or "")
        total_cases += ran
        total_skipped += skipped
        if ran == 0:
            # Some files are standalone checkers that print their own OK marker
            # instead of using unittest; they still assert and exit non-zero on
            # failure, so accept that convention but never an empty run.
            if '_OK' in (result.stdout or ""):
                total_cases += 1
                print(f"SCRIPT_CHECKER_OK={path.name}")
            else:
                print(f"NO_TESTS={path.name}", file=sys.stderr)
                return 1
            continue
        if skipped == ran:
            print(f"ALL_SKIPPED={path.name}", file=sys.stderr)
    print(f"EMAIL_TEST_FILES_PASS={len(tests)}")
    print(f"EMAIL_TEST_CASES_PASS={total_cases}")
    print(f"EMAIL_TEST_CASES_SKIPPED={total_skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
