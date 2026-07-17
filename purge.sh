#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${1:-}" == "--confirm-purge-user-data" ]] || {
  printf 'PURGE_REFUSED=explicit --confirm-purge-user-data required\n' >&2
  exit 2
}
DATA_ROOT="${HERMES_EMAIL_WATCHDOG_DATA_ROOT:-/opt/data}"
HOME_ROOT="${DATA_ROOT}/.hermes-home/.hermes"
STATE_DIR="${HERMES_EMAIL_WATCHDOG_INSTALL_STATE_DIR:-${HOME_ROOT}/email_watchdog_install}"
MANIFEST="${STATE_DIR}/install-manifest.json"
if [[ -f "${MANIFEST}" ]]; then
  python3 - "${MANIFEST}" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding="utf-8"))
assert d.get("owner")=="hermes-email-watchdog"
assert d.get("installed") is not True, "uninstall before purge"
PY
fi
owned=(
  "${HOME_ROOT}/email_watchdog_config.json"
  "${HOME_ROOT}/email_watchdog_enabled"
  "${HOME_ROOT}/email_watchdog_interval_seconds"
  "${HOME_ROOT}/email_watchdog_status.json"
  "${HOME_ROOT}/email_watchdog_outbox.json"
  "${HOME_ROOT}/email_watch_seen.json"
  "${HOME_ROOT}/email.db"
  "${HOME_ROOT}/email_learning"
  "${HOME_ROOT}/email_cache"
  "${HOME_ROOT}/email_threads.json"
  "${HOME_ROOT}/email_contacts.json"
  "${HOME_ROOT}/email_watchdog_onboarding.json"
  "${HOME_ROOT}/email_watchdog_onboarding.lock"
  "${HOME_ROOT}/.email_watchdog_onboarding.json.lock"
  "${HOME_ROOT}/.email_watchdog_outbox.json.lock"
  "${HOME_ROOT}/email_watchdog_onboarding_backups"
  "${HOME_ROOT}/email_watchdog_himalaya"
  "${STATE_DIR}"
)
for path in "${owned[@]}"; do
  case "${path}" in
    "${HOME_ROOT}"/*) rm -rf -- "${path}" ;;
    *) printf 'PURGE_REFUSED=path escaped ownership root: %s\n' "${path}" >&2; exit 3 ;;
  esac
done
printf 'PURGE_OK\nowned_user_data_removed=true\nweixin_modified=false\n'
