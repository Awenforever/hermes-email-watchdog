#!/usr/bin/env bash
set -Eeuo pipefail
HERMES_HOME_DIR="${HERMES_HOME:-${HERMES_EMAIL_WATCHDOG_DATA_ROOT:-/opt/data}}"
SKILL_DIR="${HERMES_EMAIL_WATCHDOG_SKILL_DIR:-${HERMES_HOME_DIR}/skills/hermes-email-watchdog}"
ACTIVE_DIR="${HERMES_EMAIL_WATCHDOG_ACTIVE_HOOK_DIR:-${HERMES_HOME_DIR}/hooks/hermes-email-watchdog}"
PLUGIN_STATE="${HERMES_EMAIL_WATCHDOG_STATE_ROOT:-${HERMES_HOME_DIR}/plugin-data/hermes-email-watchdog}"
STATE_DIR="${HERMES_EMAIL_WATCHDOG_INSTALL_STATE_DIR:-${PLUGIN_STATE}/install}"
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
if [[ -f "${backup}/config.before" ]]; then
  install -m 0600 "${backup}/config.before" "${PLUGIN_STATE}/config.json"
  chown --reference="${backup}/config.before" "${PLUGIN_STATE}/config.json"
fi
python3 - "${MANIFEST}" "${SKILL_DIR}" "${ACTIVE_DIR}" <<'PY'
from pathlib import Path
from datetime import datetime
import hashlib,json,os,sys
p,skill,active=map(Path,sys.argv[1:])
d=json.loads(p.read_text(encoding="utf-8"))
d["installed"]=True
d["last_rollback_at"]=datetime.now().astimezone().isoformat(timespec="seconds")
d["last_operation"]="rollback"
d["version"]=(skill/"VERSION").read_text(encoding="utf-8").strip()
d["installed_handler_sha256"]=hashlib.sha256((active/"handler.py").read_bytes()).hexdigest()
d["installed_hook_sha256"]=hashlib.sha256((active/"HOOK.yaml").read_bytes()).hexdigest()
tmp=p.with_suffix(".tmp"); tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
os.replace(tmp,p)
PY
printf 'ROLLBACK_OK\nrestart_required=true\nweixin_modified=false\n'
