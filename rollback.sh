#!/usr/bin/env bash
set -Eeuo pipefail
DATA_ROOT="${HERMES_EMAIL_WATCHDOG_DATA_ROOT:-/opt/data}"
SKILL_DIR="${HERMES_EMAIL_WATCHDOG_SKILL_DIR:-${DATA_ROOT}/skills/hermes-email-watchdog}"
ACTIVE_DIR="${HERMES_EMAIL_WATCHDOG_ACTIVE_HOOK_DIR:-${DATA_ROOT}/hooks/hermes-email-watchdog}"
STATE_DIR="${HERMES_EMAIL_WATCHDOG_INSTALL_STATE_DIR:-${DATA_ROOT}/.hermes-home/.hermes/email_watchdog_install}"
MANIFEST="${STATE_DIR}/install-manifest.json"
fail(){ printf 'ROLLBACK_FAILED=%s\n' "$1" >&2; exit 1; }
[[ -f "${MANIFEST}" ]] || fail "manifest missing"
backup="$(python3 - "${MANIFEST}" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding="utf-8"))
assert d.get("owner")=="hermes-email-watchdog"
print(d.get("last_backup_dir") or "")
PY
)"
[[ -n "${backup}" && -d "${backup}/skill.before" ]] || fail "usable backup missing"
rm -rf "${SKILL_DIR}" "${ACTIVE_DIR}"
cp -a "${backup}/skill.before" "${SKILL_DIR}"
if [[ -d "${backup}/active-hook.before" ]]; then
  cp -a "${backup}/active-hook.before" "${ACTIVE_DIR}"
else
  mkdir -p "${ACTIVE_DIR}"
  cp -a "${SKILL_DIR}/hooks/hermes-email-watchdog/." "${ACTIVE_DIR}/"
fi
python3 - "${MANIFEST}" <<'PY'
from pathlib import Path
from datetime import datetime
import json,os,sys
p=Path(sys.argv[1]); d=json.loads(p.read_text(encoding="utf-8"))
d["installed"]=True
d["last_rollback_at"]=datetime.now().astimezone().isoformat(timespec="seconds")
tmp=p.with_suffix(".tmp"); tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
os.replace(tmp,p)
PY
printf 'ROLLBACK_OK\nrestart_required=true\nweixin_modified=false\n'
