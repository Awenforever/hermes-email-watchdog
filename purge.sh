#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${1:-}" == "--confirm-purge-user-data" ]] || {
  printf 'PURGE_REFUSED=explicit --confirm-purge-user-data required\n' >&2
  exit 2
}
HERMES_HOME_DIR="${HERMES_HOME:-${HERMES_EMAIL_WATCHDOG_DATA_ROOT:-/opt/data}}"
HOME_ROOT="${HERMES_EMAIL_WATCHDOG_STATE_ROOT:-${HERMES_HOME_DIR}/plugin-data/hermes-email-watchdog}"
STATE_DIR="${HERMES_EMAIL_WATCHDOG_INSTALL_STATE_DIR:-${HOME_ROOT}/install}"
LEGACY_ROOT="${HERMES_EMAIL_WATCHDOG_LEGACY_STATE_ROOT:-${HERMES_HOME_DIR}/.hermes-home/.hermes}"
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
  "${HOME_ROOT}/config.json"
  "${HOME_ROOT}/enabled"
  "${HOME_ROOT}/interval_seconds"
  "${HOME_ROOT}/status.json"
  "${HOME_ROOT}/outbox.json"
  "${HOME_ROOT}/seen.json"
  "${HOME_ROOT}/email.db"
  "${HOME_ROOT}/learning"
  "${HOME_ROOT}/email_cache"
  "${HOME_ROOT}/email_threads.json"
  "${HOME_ROOT}/email_contacts.json"
  "${HOME_ROOT}/onboarding.json"
  "${HOME_ROOT}/onboarding.lock"
  "${HOME_ROOT}/.onboarding.json.lock"
  "${HOME_ROOT}/.outbox.json.lock"
  "${HOME_ROOT}/onboarding-backups"
  "${HOME_ROOT}/himalaya"
  "${STATE_DIR}"
)
for path in "${owned[@]}"; do
  case "${path}" in
    "${HOME_ROOT}"/*) rm -rf -- "${path}" ;;
    *) printf 'PURGE_REFUSED=path escaped ownership root: %s\n' "${path}" >&2; exit 3 ;;
  esac
done
legacy_owned=(
  email_watchdog_config.json email_watchdog_enabled email_watchdog_interval_seconds
  email_watchdog_status.json email_watchdog_outbox.json email_watch_seen.json
  email.db email_learning email_cache email_threads.json email_contacts.json
  email_decision_engine_config.json email_watchdog_onboarding.json
  email_watchdog_onboarding.lock .email_watchdog_onboarding.json.lock
  .email_watchdog_outbox.json.lock email_watchdog_onboarding_backups
  email_watchdog_himalaya email_watchdog_install
)
for name in "${legacy_owned[@]}"; do
  rm -rf -- "${LEGACY_ROOT}/${name}"
done
printf 'PURGE_OK\nowned_user_data_removed=true\nweixin_modified=false\n'
