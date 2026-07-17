#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
export HERMES_EMAIL_WATCHDOG_OPERATION=upgrade
exec bash "${ROOT}/install.sh"
