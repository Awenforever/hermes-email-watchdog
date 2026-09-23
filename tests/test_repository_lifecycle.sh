#!/usr/bin/env bash
set -Eeuo pipefail

REPO="${1:-/repo}"
DATA="$(mktemp -d /tmp/hermes-email-watchdog-lifecycle.XXXXXX)"
cleanup(){ rm -rf "${DATA}"; }
trap cleanup EXIT

export HERMES_EMAIL_WATCHDOG_DATA_ROOT="${DATA}/data"
# An enclosing Hermes image may export its own production HERMES_HOME. The
# isolated lifecycle matrix must only touch its temporary data root.
unset HERMES_HOME
unset EMAIL_WATCHDOG_CONFIG
base_version="$(cat "${REPO}/VERSION")"

legacy="${DATA}/data/.hermes-home/.hermes"
mkdir -p "${legacy}/email_learning" "${legacy}/email_cache" \
  "${legacy}/email_watchdog_onboarding_backups" "${legacy}/email_watchdog_himalaya"
printf '{"version":1,"sentinel":"legacy"}\n' > "${legacy}/email_watchdog_config.json"
printf 'true\n' > "${legacy}/email_watchdog_enabled"
printf '{}\n' > "${legacy}/email_watch_seen.json"
printf 'imap-only\n' > "${legacy}/email_watchdog_himalaya/primary.toml"

echo STEP=initial-install
bash "${REPO}/install.sh"
bash "${DATA}/data/skills/hermes-email-watchdog/verify.sh"
home="${DATA}/data/plugin-data/hermes-email-watchdog"
[[ "$(cat "${home}/enabled")" == "true" ]]
grep -q '"sentinel":"legacy"' "${home}/config.json"
[[ -f "${home}/himalaya/primary.toml" ]]

mkdir -p "${home}/learning" "${home}/email_cache" \
  "${home}/onboarding-backups" "${home}/himalaya"
printf '{"entries":{}}\n' > "${home}/outbox.json"
printf '{"state":"disabled"}\n' > "${home}/status.json"
printf '{"configured":true}\n' > "${home}/onboarding.json"
printf 'lock\n' > "${home}/onboarding.lock"
printf 'lock\n' > "${home}/.onboarding.json.lock"
printf 'lock\n' > "${home}/.outbox.json.lock"
printf 'backup\n' > "${home}/onboarding-backups/state.txt"
printf 'db-placeholder\n' > "${home}/email.db"
printf 'learning\n' > "${home}/learning/state.txt"

echo STEP=fake-user-data-created
# Idempotent install.
echo STEP=idempotent-install
bash "${REPO}/install.sh"
bash "${DATA}/data/skills/hermes-email-watchdog/verify.sh"

# Upgrade from a modified, checksummed checkout and roll back.
cp -a "${REPO}" "${DATA}/repo-v2"
printf '0.4.1\n' > "${DATA}/repo-v2/VERSION"
python3 "${DATA}/repo-v2/scripts/generate_checksums.py" "${DATA}/repo-v2"
cat > "${home}/config.json" <<'JSON'
{"version":2,"sentinel":"preserve-me","semantic_engine":{"model":"deepseek-flash","fallback_model":"qwen3.6-chat","protocol":"readable_grounded_core_v1u"},"notification":{"fast_lane_enabled":true},"delivery":{"auto_download_attachments":false}}
JSON
if [[ "$(id -u)" == "0" ]]; then chown 65534:65534 "${home}/config.json"; fi
config_uid_before="$(stat -c %u "${home}/config.json")"
cp -a "${home}/config.json" "${DATA}/config.before-upgrade.json"
echo STEP=upgrade
bash "${DATA}/repo-v2/upgrade.sh"
[[ "$(cat "${DATA}/data/skills/hermes-email-watchdog/VERSION")" == "0.4.1" ]]
grep -q '"version": 3' "${home}/config.json"
grep -q '"sentinel": "preserve-me"' "${home}/config.json"
[[ "$(stat -c %u "${home}/config.json")" == "${config_uid_before}" ]]
echo STEP=rollback
bash "${DATA}/data/skills/hermes-email-watchdog/rollback.sh"
[[ "$(cat "${DATA}/data/skills/hermes-email-watchdog/VERSION")" == "${base_version}" ]]
cmp -s "${home}/config.json" "${DATA}/config.before-upgrade.json"
[[ "$(stat -c %u "${home}/config.json")" == "${config_uid_before}" ]]
bash "${DATA}/data/skills/hermes-email-watchdog/verify.sh"

echo STEP=rollback-verified
# Default uninstall removes code and hook, preserving user data.
echo STEP=uninstall
bash "${DATA}/data/skills/hermes-email-watchdog/uninstall.sh"
[[ ! -e "${DATA}/data/skills/hermes-email-watchdog" ]]
[[ ! -e "${DATA}/data/hooks/hermes-email-watchdog" ]]
for p in config.json seen.json outbox.json status.json onboarding.json onboarding.lock .onboarding.json.lock .outbox.json.lock onboarding-backups himalaya email.db learning; do
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
for p in config.json seen.json outbox.json status.json onboarding.json onboarding.lock .onboarding.json.lock .outbox.json.lock onboarding-backups himalaya email.db learning email_cache install; do
  [[ ! -e "${home}/${p}" ]]
done

echo STEP=purge-verified
# Reinstall after purge.
bash "${REPO}/install.sh"
bash "${DATA}/data/skills/hermes-email-watchdog/verify.sh"

printf 'LIFECYCLE_MATRIX_OK\n'
