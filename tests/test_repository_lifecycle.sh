#!/usr/bin/env bash
set -Eeuo pipefail

REPO="${1:-/repo}"
DATA="$(mktemp -d /tmp/hermes-email-watchdog-lifecycle.XXXXXX)"
cleanup(){ rm -rf "${DATA}"; }
trap cleanup EXIT

export HERMES_EMAIL_WATCHDOG_DATA_ROOT="${DATA}/data"

echo STEP=initial-install
bash "${REPO}/install.sh"
bash "${DATA}/data/skills/hermes-email-watchdog/verify.sh"
[[ "$(cat "${DATA}/data/.hermes-home/.hermes/email_watchdog_enabled")" == "false" ]]

home="${DATA}/data/.hermes-home/.hermes"
mkdir -p "${home}/email_learning" "${home}/email_cache" \
  "${home}/email_watchdog_onboarding_backups" "${home}/email_watchdog_himalaya"
printf '{}\n' > "${home}/email_watchdog_config.json"
printf '{}\n' > "${home}/email_watch_seen.json"
printf '{"entries":{}}\n' > "${home}/email_watchdog_outbox.json"
printf '{"state":"disabled"}\n' > "${home}/email_watchdog_status.json"
printf '{"configured":true}\n' > "${home}/email_watchdog_onboarding.json"
printf 'lock\n' > "${home}/email_watchdog_onboarding.lock"
printf 'lock\n' > "${home}/.email_watchdog_onboarding.json.lock"
printf 'lock\n' > "${home}/.email_watchdog_outbox.json.lock"
printf 'backup\n' > "${home}/email_watchdog_onboarding_backups/state.txt"
printf 'imap-only\n' > "${home}/email_watchdog_himalaya/primary.toml"
printf 'db-placeholder\n' > "${home}/email.db"
printf 'learning\n' > "${home}/email_learning/state.txt"

echo STEP=fake-user-data-created
# Idempotent install.
echo STEP=idempotent-install
bash "${REPO}/install.sh"
bash "${DATA}/data/skills/hermes-email-watchdog/verify.sh"

# Upgrade from a modified, checksummed checkout and roll back.
cp -a "${REPO}" "${DATA}/repo-v2"
printf '0.2.0-rc.3\n' > "${DATA}/repo-v2/VERSION"
python3 "${DATA}/repo-v2/scripts/generate_checksums.py" "${DATA}/repo-v2"
echo STEP=upgrade
bash "${DATA}/repo-v2/upgrade.sh"
[[ "$(cat "${DATA}/data/skills/hermes-email-watchdog/VERSION")" == "0.2.0-rc.3" ]]
echo STEP=rollback
bash "${DATA}/data/skills/hermes-email-watchdog/rollback.sh"
[[ "$(cat "${DATA}/data/skills/hermes-email-watchdog/VERSION")" == "0.1.0-rc.3" ]]
bash "${DATA}/data/skills/hermes-email-watchdog/verify.sh"

echo STEP=rollback-verified
# Default uninstall removes code and hook, preserving user data.
echo STEP=uninstall
bash "${DATA}/data/skills/hermes-email-watchdog/uninstall.sh"
[[ ! -e "${DATA}/data/skills/hermes-email-watchdog" ]]
[[ ! -e "${DATA}/data/hooks/hermes-email-watchdog" ]]
for p in email_watchdog_config.json email_watch_seen.json email_watchdog_outbox.json email_watchdog_status.json email_watchdog_onboarding.json email_watchdog_onboarding.lock .email_watchdog_onboarding.json.lock .email_watchdog_outbox.json.lock email_watchdog_onboarding_backups email_watchdog_himalaya email.db email_learning; do
  [[ -e "${home}/${p}" ]]
done

echo STEP=uninstall-preserve-verified
# Reinstall after uninstall.
bash "${REPO}/install.sh"
bash "${DATA}/data/skills/hermes-email-watchdog/verify.sh"

echo STEP=reinstall-verified
# Purge refuses without explicit confirmation.
set +e
bash "${REPO}/purge.sh" >/tmp/purge-refused.out 2>&1
rc=$?
set -e
[[ "${rc}" -ne 0 ]]

bash "${DATA}/data/skills/hermes-email-watchdog/uninstall.sh"
echo STEP=purge
bash "${REPO}/purge.sh" --confirm-purge-user-data
for p in email_watchdog_config.json email_watch_seen.json email_watchdog_outbox.json email_watchdog_status.json email_watchdog_onboarding.json email_watchdog_onboarding.lock .email_watchdog_onboarding.json.lock .email_watchdog_outbox.json.lock email_watchdog_onboarding_backups email_watchdog_himalaya email.db email_learning email_cache email_watchdog_install; do
  [[ ! -e "${home}/${p}" ]]
done

echo STEP=purge-verified
# Reinstall after purge.
bash "${REPO}/install.sh"
bash "${DATA}/data/skills/hermes-email-watchdog/verify.sh"

printf 'LIFECYCLE_MATRIX_OK\n'
