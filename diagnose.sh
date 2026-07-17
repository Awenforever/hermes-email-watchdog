#!/usr/bin/env bash
set -Eeuo pipefail
DATA_ROOT="${HERMES_EMAIL_WATCHDOG_DATA_ROOT:-/opt/data}"
SKILL_DIR="${HERMES_EMAIL_WATCHDOG_SKILL_DIR:-${DATA_ROOT}/skills/hermes-email-watchdog}"
ACTIVE_DIR="${HERMES_EMAIL_WATCHDOG_ACTIVE_HOOK_DIR:-${DATA_ROOT}/hooks/hermes-email-watchdog}"
HOME_ROOT="${DATA_ROOT}/.hermes-home/.hermes"
OUT_BASE="${HERMES_EMAIL_WATCHDOG_DIAGNOSTICS_DIR:-${PWD}}"
TS="$(date +%Y%m%d-%H%M%S)"
WORK="${OUT_BASE}/hermes-email-watchdog-diagnostics-${TS}.work"
PKG="${OUT_BASE}/hermes-email-watchdog-diagnostics-${TS}.tar.gz"
mkdir -p "${WORK}"
python3 - "${SKILL_DIR}" "${ACTIVE_DIR}" "${HOME_ROOT}" "${WORK}/diagnostics.json" <<'PY'
from pathlib import Path
from datetime import datetime
import hashlib,json,os,sys
skill,active,home,out=map(Path,sys.argv[1:])
def meta(p):
    row={"path":str(p),"exists":p.exists()}
    if p.is_file():
        row["size"]=p.stat().st_size
        row["sha256"]=hashlib.sha256(p.read_bytes()).hexdigest()
    return row
paths=[
 skill/"VERSION", skill/"checksums/SHA256SUMS",
 active/"handler.py", active/"HOOK.yaml",
 home/"email_watchdog_config.json", home/"email_watchdog_enabled",
 home/"email_watchdog_status.json", home/"email_watchdog_outbox.json",
 home/"email_watch_seen.json", home/"email.db",
 home/"email_watchdog_onboarding.json",
 home/"email_watchdog_himalaya",
]
json.dump({"captured_at":datetime.now().astimezone().isoformat(timespec="seconds"),
           "read_only":True,"files":[meta(p) for p in paths]},
          open(out,"w",encoding="utf-8"),ensure_ascii=False,indent=2)
PY
tar -czf "${PKG}" -C "${WORK}" .
rm -rf "${WORK}"
printf 'DIAGNOSE_OK\n'
printf 'upload=%s\n' "${PKG}"
