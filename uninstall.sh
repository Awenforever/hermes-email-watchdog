#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${1:-}" != "--stage2" ]]; then
  tmp="$(mktemp "${TMPDIR:-/tmp}/hermes-email-watchdog-uninstall.XXXXXX.sh")"
  cp "$0" "${tmp}"
  chmod 0700 "${tmp}"
  exec bash "${tmp}" --stage2
fi
DATA_ROOT="${HERMES_EMAIL_WATCHDOG_DATA_ROOT:-/opt/data}"
SKILL_DIR="${HERMES_EMAIL_WATCHDOG_SKILL_DIR:-${DATA_ROOT}/skills/hermes-email-watchdog}"
ACTIVE_DIR="${HERMES_EMAIL_WATCHDOG_ACTIVE_HOOK_DIR:-${DATA_ROOT}/hooks/hermes-email-watchdog}"
STATE_DIR="${HERMES_EMAIL_WATCHDOG_INSTALL_STATE_DIR:-${DATA_ROOT}/.hermes-home/.hermes/email_watchdog_install}"
MANIFEST="${STATE_DIR}/install-manifest.json"
fail(){ printf 'UNINSTALL_FAILED=%s\n' "$1" >&2; exit 1; }
[[ -f "${MANIFEST}" ]] || fail "owned install manifest missing"
python3 - "${MANIFEST}" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding="utf-8"))
assert d.get("owner")=="hermes-email-watchdog"
PY
rm -rf "${ACTIVE_DIR}"
rm -rf "${SKILL_DIR}"
python3 - "${MANIFEST}" <<'PY'
from pathlib import Path
from datetime import datetime
import json,os,sys
p=Path(sys.argv[1]); d=json.loads(p.read_text(encoding="utf-8"))
d["installed"]=False
d["last_uninstalled_at"]=datetime.now().astimezone().isoformat(timespec="seconds")
d["user_data_deleted"]=False
tmp=p.with_suffix(".tmp"); tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
os.replace(tmp,p)
PY
printf 'UNINSTALL_OK\nsource_removed=true\nactive_hook_removed=true\nuser_data_preserved=true\nweixin_modified=false\n'
