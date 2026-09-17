#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DATA_ROOT="${HERMES_EMAIL_WATCHDOG_DATA_ROOT:-/opt/data}"
SKILL_DIR="${HERMES_EMAIL_WATCHDOG_SKILL_DIR:-${DATA_ROOT}/skills/hermes-email-watchdog}"
ACTIVE_DIR="${HERMES_EMAIL_WATCHDOG_ACTIVE_HOOK_DIR:-${DATA_ROOT}/hooks/hermes-email-watchdog}"
STATE_DIR="${HERMES_EMAIL_WATCHDOG_INSTALL_STATE_DIR:-${DATA_ROOT}/.hermes-home/.hermes/email_watchdog_install}"
MANIFEST="${STATE_DIR}/install-manifest.json"
CHECKSUMS="${ROOT}/checksums/SHA256SUMS"
OPERATION="${HERMES_EMAIL_WATCHDOG_OPERATION:-install}"

fail() { printf 'INSTALL_FAILED=%s\n' "$1" >&2; exit 1; }

python3 "${ROOT}/scripts/repository_contract_check.py" "${ROOT}" >/dev/null
python3 "${ROOT}/scripts/verify_checksums.py" "${ROOT}" "${CHECKSUMS}"

mkdir -p "$(dirname "${SKILL_DIR}")" "$(dirname "${ACTIVE_DIR}")" "${STATE_DIR}/backups"
root_real="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${ROOT}")"
skill_real="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${SKILL_DIR}")"

old_manifest_owner=""
if [[ -f "${MANIFEST}" ]]; then
  old_manifest_owner="$(python3 - "${MANIFEST}" <<'PY'
import json,sys
try:
    d=json.load(open(sys.argv[1],encoding="utf-8"))
except Exception:
    d={}
print(d.get("owner") or "")
PY
)"
fi

if [[ -e "${SKILL_DIR}" && "${root_real}" != "${skill_real}" && "${old_manifest_owner}" != "hermes-email-watchdog" ]]; then
  fail "target exists without an owned install manifest"
fi

backup_dir=""
if [[ -e "${SKILL_DIR}" && "${root_real}" != "${skill_real}" ]]; then
  backup_dir="${STATE_DIR}/backups/$(date +%Y%m%d-%H%M%S)-${OPERATION}"
  mkdir -p "${backup_dir}"
  cp -a "${SKILL_DIR}" "${backup_dir}/skill.before"
  [[ ! -e "${ACTIVE_DIR}" ]] || cp -a "${ACTIVE_DIR}" "${backup_dir}/active-hook.before"
fi

if [[ "${root_real}" != "${skill_real}" ]]; then
  staging="${SKILL_DIR}.staging.$$"
  old="${SKILL_DIR}.old.$$"
  rm -rf "${staging}" "${old}"
  mkdir -p "${staging}"
  (
    cd "${ROOT}"
    tar --exclude='./.git' --exclude='./diagnostics' --exclude='*/__pycache__' --exclude='*.pyc' --exclude='*.pyo' -cf - .
  ) | tar -C "${staging}" -xf -
  python3 "${staging}/scripts/verify_checksums.py" "${staging}" "${staging}/checksums/SHA256SUMS"
  if [[ -e "${SKILL_DIR}" ]]; then
    mv "${SKILL_DIR}" "${old}"
  fi
  mv "${staging}" "${SKILL_DIR}"
  rm -rf "${old}"
fi

install -d -m 0755 "${ACTIVE_DIR}"
install -m 0644 "${SKILL_DIR}/hooks/hermes-email-watchdog/handler.py" "${ACTIVE_DIR}/.handler.py.tmp.$$"
install -m 0644 "${SKILL_DIR}/hooks/hermes-email-watchdog/HOOK.yaml" "${ACTIVE_DIR}/.HOOK.yaml.tmp.$$"
mv -f "${ACTIVE_DIR}/.handler.py.tmp.$$" "${ACTIVE_DIR}/handler.py"
mv -f "${ACTIVE_DIR}/.HOOK.yaml.tmp.$$" "${ACTIVE_DIR}/HOOK.yaml"

enabled_file="${DATA_ROOT}/.hermes-home/.hermes/email_watchdog_enabled"
if [[ ! -e "${enabled_file}" ]]; then
  mkdir -p "$(dirname "${enabled_file}")"
  printf 'false\n' > "${enabled_file}"
  chmod 0600 "${enabled_file}" 2>/dev/null || true
fi

python3 - "${MANIFEST}" "${SKILL_DIR}" "${ACTIVE_DIR}" "${backup_dir}" "${OPERATION}" <<'PY'
from pathlib import Path
from datetime import datetime
import hashlib,json,os,sys
manifest,skill,active,backup,operation=sys.argv[1:]
skill=Path(skill); active=Path(active)
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
data={
  "schema_version":2,
  "owner":"hermes-email-watchdog",
  "version":(skill/"VERSION").read_text(encoding="utf-8").strip(),
  "installed":True,
  "skill_dir":str(skill),
  "active_hook_dir":str(active),
  "installed_handler_sha256":sha(active/"handler.py"),
  "installed_hook_sha256":sha(active/"HOOK.yaml"),
  "installed_at":datetime.now().astimezone().isoformat(timespec="seconds"),
  "last_operation":operation,
  "user_data_preserved_on_uninstall":True,
  "weixin_modified":False,
}
if backup:
  data["last_backup_dir"]=backup
path=Path(manifest)
path.parent.mkdir(parents=True,exist_ok=True)
tmp=path.with_suffix(".tmp")
tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
os.replace(tmp,path)
os.chmod(path,0o600)
PY

printf 'INSTALL_OK\n'
printf 'operation=%s\n' "${OPERATION}"
printf 'version=%s\n' "$(cat "${SKILL_DIR}/VERSION")"
printf 'scheduler_enabled=%s\n' "$(cat "${enabled_file}")"
printf 'restart_required=true\n'
printf 'mailbox_read_only=true\n'
printf 'weixin_modified=false\n'
