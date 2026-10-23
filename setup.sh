#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# Bare Windows has no python3 (python.org installs python.exe only), so resolve
# the first interpreter available; keeps this wrapper working under Git-Bash too.
PYTHON="$(command -v python3 || command -v python || command -v py || true)"
if [ -z "${PYTHON}" ]; then
  echo "setup.sh: no python3/python/py interpreter found on PATH" >&2
  exit 127
fi
exec "${PYTHON}" "${ROOT}/scripts/email_onboarding.py" "$@"
