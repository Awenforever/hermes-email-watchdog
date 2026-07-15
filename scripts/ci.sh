#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
python3 "${ROOT}/scripts/repository_contract_check.py" "${ROOT}"
python3 "${ROOT}/scripts/verify_checksums.py" "${ROOT}" "${ROOT}/checksums/SHA256SUMS"
find "${ROOT}" -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
python3 - "${ROOT}" <<'PY'
from pathlib import Path
import py_compile,sys
root=Path(sys.argv[1])
for p in root.rglob("*.py"):
    py_compile.compile(str(p),doraise=True)
print("PY_COMPILE_OK")
PY
TMP_STATE="$(mktemp -d /tmp/hermes-email-watchdog-ci.XXXXXX)"
trap 'rm -rf "${TMP_STATE}"' EXIT
export PYTHONPATH="${ROOT}/scripts:${ROOT}/tests:${PYTHONPATH:-}"
export HERMES_EMAIL_WATCHDOG_SKILL_DIR="${ROOT}"
export HERMES_EMAIL_WATCHDOG_OUTBOX_FILE="${TMP_STATE}/outbox.json"
export EMAIL_WATCHDOG_CONFIG="${TMP_STATE}/config.json"
export HOME="${TMP_STATE}/home"
mkdir -p "${HOME}"
tests=(
  test_email_renderer_matrix.py
  test_email_production_router_matrix.py
  test_email_production_delivery_matrix.py
  test_email_outbox_idempotency_matrix.py
  test_email_outbox_nonblocking_backoff_matrix.py
  test_outbox_lifecycle.py
  test_email_semantic_matrix.py
  test_email_semantic_memory_matrix.py
  test_email_semantic_core_matrix.py
  test_email_semantic_grounding_matrix.py
  test_email_semantic_transport_matrix.py
  test_email_full_policy_matrix.py
)
for test in "${tests[@]}"; do
  python3 "${ROOT}/tests/${test}"
done
printf 'CI_MATRIX_OK count=%s\n' "${#tests[@]}"
