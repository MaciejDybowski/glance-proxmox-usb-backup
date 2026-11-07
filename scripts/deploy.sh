#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
PROJECT_DIR="$SCRIPT_DIR/.."
SETTINGS_FILE="${DEPLOY_SETTINGS:-$PROJECT_DIR/local/deploy.env}"
if [[ -f "$SETTINGS_FILE" ]]; then
  # This is your trusted, local shell configuration; it is never uploaded.
  source "$SETTINGS_FILE"
fi
PVE_HOST="${1:-${PVE_HOST:-}}"
if [[ -z "$PVE_HOST" ]]; then
  read -r -p "Proxmox IPv4 address or hostname: " PVE_HOST
fi
if [[ ! "$PVE_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]]; then
  printf 'Use a hostname or IPv4 address without a scheme or port.\n' >&2
  exit 1
fi

args=()
append_option() {
  local flag="$1" value="$2"
  if [[ -n "$value" ]]; then args+=("$flag" "$value"); fi
}
append_option --uuid "${USB_UUID:-}"
append_option --storage "${PVE_STORAGE:-}"
append_option --filesystem "${USB_FILESYSTEM:-}"
append_option --drive-name "${USB_DRIVE_NAME:-}"
append_option --glance-origin "${GLANCE_ORIGIN:-}"
append_option --glance-origin "${GLANCE_EXTRA_ORIGIN:-}"
append_option --port "${PANEL_PORT:-}"
append_option --language "${PANEL_LANGUAGE:-}"
remote_command='python3 /root/glance-backup-upload/scripts/install.py'
single_quote="'"
quote_escape="'\"'\"'"
if [[ "${#args[@]}" -gt 0 ]]; then
  for argument in "${args[@]}"; do
    # POSIX shell single-quote encoding, including display names containing quotes.
    escaped="${argument//"$single_quote"/$quote_escape}"
    remote_command+=" '$escaped'"
  done
fi

rsync -az --exclude='__pycache__/' --exclude='*.pyc' \
  --include='/app/' --include='/app/*.py' --include='/app/static/***' \
  --include='/scripts/' --include='/scripts/*.py' \
  --include='/deployment/***' --exclude='*' \
  "$PROJECT_DIR/" "root@$PVE_HOST:/root/glance-backup-upload/"
ssh -t "root@$PVE_HOST" "$remote_command"

# Updating a separate Glance host is optional and configured only in local/.
if [[ -n "${GLANCE_SSH_TARGET:-}" ]]; then
  GLANCE_CONFIG_FILE="${GLANCE_CONFIG_FILE:-$PROJECT_DIR/local/glance.yml}"
  GLANCE_CONFIG_PATH="${GLANCE_CONFIG_PATH:-}"
  if [[ ! -f "$GLANCE_CONFIG_FILE" || ! "$GLANCE_CONFIG_PATH" =~ ^/[a-zA-Z0-9_./-]+$ ]]; then
    printf 'Set a valid GLANCE_CONFIG_FILE and absolute GLANCE_CONFIG_PATH in your local deployment settings.\n' >&2
    exit 1
  fi
  scp "$GLANCE_CONFIG_FILE" "$GLANCE_SSH_TARGET:glance-backup-upload.yml"
  ssh -t "$GLANCE_SSH_TARGET" \
    "sudo tee '$GLANCE_CONFIG_PATH' < ~/glance-backup-upload.yml > /dev/null && rm ~/glance-backup-upload.yml"
fi
printf '\nDeployment complete. Use the HTTPS address and port reported by the installer.\n'
