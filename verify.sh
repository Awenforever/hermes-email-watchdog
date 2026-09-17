#!/usr/bin/env bash
set -Eeuo pipefail
DATA_ROOT="${HERMES_EMAIL_WATCHDOG_DATA_ROOT:-/opt/data}"
SKILL_DIR="${HERMES_EMAIL_WATCHDOG_SKILL_DIR:-${DATA_ROOT}/skills/hermes-email-watchdog}"
ACTIVE_DIR="${HERMES_EMAIL_WATCHDOG_ACTIVE_HOOK_DIR:-${DATA_ROOT}/hooks/hermes-email-watchdog}"
STATE_DIR="${HERMES_EMAIL_WATCHDOG_INSTALL_STATE_DIR:-${DATA_ROOT}/.hermes-home/.hermes/email_watchdog_install}"
MANIFEST="${STATE_DIR}/install-manifest.json"
fail(){ printf 'VERIFY_FAILED=%s\n' "$1" >&2; exit 1; }
for p in "${SKILL_DIR}/checksums/SHA256SUMS" "${ACTIVE_DIR}/handler.py" "${ACTIVE_DIR}/HOOK.yaml" "${MANIFEST}"; do
  [[ -f "$p" ]] || fail "missing $p"
done
python3 "${SKILL_DIR}/scripts/verify_checksums.py" "${SKILL_DIR}" "${SKILL_DIR}/checksums/SHA256SUMS"
python3 "${SKILL_DIR}/scripts/repository_contract_check.py" "${SKILL_DIR}"
python3 - "${MANIFEST}" "${SKILL_DIR}" "${ACTIVE_DIR}" <<'PY'
from pathlib import Path
import hashlib,json,sys
m,skill,active=sys.argv[1:]
d=json.load(open(m,encoding="utf-8"))
assert d.get("owner")=="hermes-email-watchdog"
assert d.get("installed") is True
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert sha(Path(skill)/"hooks/hermes-email-watchdog/handler.py")==sha(Path(active)/"handler.py")
assert sha(Path(skill)/"hooks/hermes-email-watchdog/HOOK.yaml")==sha(Path(active)/"HOOK.yaml")
PY
printf 'VERIFY_OK\nmailbox_read_only=true\nweixin_modified=false\n'
