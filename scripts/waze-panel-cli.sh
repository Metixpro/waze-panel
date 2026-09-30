#!/usr/bin/env bash
#
# waze-panel - server-side management command for Waze Panel.
# Installed to /usr/local/bin/waze-panel by install.sh.
#
#   waze-panel                 interactive menu
#   waze-panel status          services, URL and user counts
#   waze-panel restart         restart the panel, both OpenVPN instances and Xray
#   waze-panel logs [panel|udp|tcp|xray]
#   waze-panel xray [install|update|status|restart]
#   waze-panel reset-password [username]
#   waze-panel backup [dir]
#   waze-panel restore <file>
#   waze-panel update
#   waze-panel version
#   waze-panel uninstall
#
set -uo pipefail

APP_DIR="/opt/waze-panel"
DATA_DIR="/etc/waze-panel"
ENV_FILE="${DATA_DIR}/panel.env"
INSTALL_URL="https://raw.githubusercontent.com/Metixpro/waze-panel/main/install.sh"

C_RESET="\033[0m"; C_BLUE="\033[1;34m"; C_GREEN="\033[1;32m"
C_YELLOW="\033[1;33m"; C_RED="\033[1;31m"; C_BOLD="\033[1m"; C_DIM="\033[2m"

ok()   { echo -e "  ${C_GREEN}OK${C_RESET} $*"; }
warn() { echo -e "  ${C_YELLOW}!${C_RESET} $*"; }
die()  { echo -e "  ${C_RED}FAIL${C_RESET} $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root: sudo waze-panel $*"
[ -f "$ENV_FILE" ] || die "Waze Panel is not installed (missing ${ENV_FILE})."

env_get() {
  sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}$/\1/p" "$ENV_FILE" | tail -n1
}

PREFIX="$(env_get OVPN_SERVICE_PREFIX)"; PREFIX="${PREFIX:-openvpn-server@}"
SVC_PANEL="waze-panel.service"
SVC_UDP="${PREFIX}waze-udp"
SVC_TCP="${PREFIX}waze-tcp"
SVC_XRAY="waze-xray.service"
XRAY_BIN="/usr/local/share/waze-panel/xray/xray"
PY="${APP_DIR}/venv/bin/python"

cli() { (cd "$APP_DIR" && "$PY" -m app.cli "$@"); }

svc_line() {
  local name="$1" label="$2" state
  state="$(systemctl is-active "$name" 2>/dev/null || true)"
  if [ "$state" = "active" ]; then
    echo -e "  ${C_GREEN}*${C_RESET} ${label}  ${C_DIM}(${name})${C_RESET}  running"
  else
    echo -e "  ${C_RED}*${C_RESET} ${label}  ${C_DIM}(${name})${C_RESET}  ${state:-stopped}"
  fi
}

panel_url() {
  local sub port addr
  sub="$(env_get SUBSCRIPTION_BASE_URL)"
  port="$(env_get PANEL_PORT)"; addr="$(env_get SERVER_ADDRESS)"
  if [ -n "$sub" ]; then echo "$sub"; else echo "http://${addr}:${port}"; fi
}

cmd_status() {
  echo -e "\n${C_BOLD}$(cli version 2>/dev/null || echo 'Waze Panel')${C_RESET}  $(panel_url)\n"
  svc_line "$SVC_PANEL" "Web panel     "
  svc_line "$SVC_UDP"   "OpenVPN UDP $(env_get OVPN_UDP_PORT)"
  svc_line "$SVC_TCP"   "OpenVPN TCP $(env_get OVPN_TCP_PORT)"
  if [ -x "$XRAY_BIN" ]; then
    svc_line "$SVC_XRAY" "Xray $("$XRAY_BIN" version 2>/dev/null | awk 'NR==1 {print $2}')   "
  else
    echo -e "  ${C_DIM}*${C_RESET} Xray            ${C_DIM}not installed (waze-panel xray install)${C_RESET}"
  fi
  local db="${DATA_DIR}/waze-panel.db"
  if command -v sqlite3 >/dev/null 2>&1 && [ -f "$db" ]; then
    local total active
    total="$(sqlite3 "$db" 'select count(*) from vpn_users;' 2>/dev/null || echo '?')"
    active="$(sqlite3 "$db" 'select count(*) from vpn_users where enabled=1 and revoked=0;' 2>/dev/null || echo '?')"
    echo -e "\n  Users: ${C_BOLD}${total}${C_RESET} (${active} enabled)"
  fi
  local crl="$(env_get OPENVPN_SERVER_DIR)/crl.pem"
  if [ -f "$crl" ]; then
    echo -e "  CRL valid until: $(openssl crl -in "$crl" -noout -nextupdate 2>/dev/null | cut -d= -f2)"
  fi
  echo
}

cmd_restart() {
  for s in "$SVC_UDP" "$SVC_TCP" "$SVC_PANEL"; do
    systemctl restart "$s" && ok "restarted $s" || warn "could not restart $s"
  done
  if [ -x "$XRAY_BIN" ]; then
    systemctl restart "$SVC_XRAY" && ok "restarted $SVC_XRAY" || warn "could not restart $SVC_XRAY"
  fi
}

cmd_stop()  { for s in "$SVC_PANEL" "$SVC_UDP" "$SVC_TCP" "$SVC_XRAY"; do systemctl stop "$s" 2>/dev/null; done; ok "stopped"; }
cmd_start() { for s in "$SVC_UDP" "$SVC_TCP" "$SVC_PANEL" "$SVC_XRAY"; do systemctl start "$s" 2>/dev/null; done; ok "started"; }

cmd_logs() {
  case "${1:-panel}" in
    udp) tail -n 100 -f /var/log/openvpn/udp.log ;;
    tcp) tail -n 100 -f /var/log/openvpn/tcp.log ;;
    xray) journalctl -u "$SVC_XRAY" -n 100 -f ;;
    *)   journalctl -u "$SVC_PANEL" -n 100 -f ;;
  esac
}

cmd_reset_password() {
  local user="${1:-}" pass pass2
  [ -n "$user" ] || user="$(cli list-admins | head -n1)"
  [ -n "$user" ] || user="admin"
  read -r -p "New password for '${user}' (empty = generate one): " -s pass </dev/tty || true
  echo
  if [ -z "$pass" ]; then
    pass="$(tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 16 || true)"
  else
    read -r -p "Repeat it: " -s pass2 </dev/tty || true
    echo
    [ "$pass" = "$pass2" ] || die "Passwords do not match."
  fi
  if cli list-admins | grep -qx "$user"; then
    cli reset-password --username "$user" --password "$pass" >/dev/null || die "Could not update the password."
  else
    cli create-admin --username "$user" --password "$pass" >/dev/null || die "Could not create the admin."
  fi
  ok "Login: ${C_BOLD}${user}${C_RESET} / ${C_BOLD}${pass}${C_RESET}"
}

cmd_backup() {
  local dir="${1:-/root}" out
  mkdir -p "$dir"
  dir="$(readlink -f "$dir")"
  out="$(cli backup --out "$dir")" || die "Backup failed."
  ok "Backup written to ${C_BOLD}${out}${C_RESET}"
  echo -e "  ${C_DIM}Copy it off this server, e.g.: scp root@$(env_get SERVER_ADDRESS):${out} .${C_RESET}"
}

cmd_restore() {
  local file="${1:-}" answer
  [ -n "$file" ] && [ -f "$file" ] || die "Usage: waze-panel restore <backup.tar.gz>"
  tar -tzf "$file" | grep -q '^etc/waze-panel/panel.env$' || die "That does not look like a Waze Panel backup."

  # Security verification: strictly reject archives with traversal or paths outside allowed prefixes
  if tar -tzf "$file" | grep -qE '(\.\./|^/)'; then
    die "Archive contains dangerous path traversal patterns."
  fi
  local invalid_paths
  invalid_paths="$(tar -tzf "$file" | grep -v -E '^(etc/waze-panel/|etc/openvpn/)')"
  if [ -n "$invalid_paths" ]; then
    die "Archive contains unauthorized or dangerous paths outside allowed directories."
  fi

  echo -e "${C_YELLOW}This replaces the current database, settings and certificates with the backup.${C_RESET}"
  read -r -p "Continue? [y/N]: " answer </dev/tty || true
  case "$answer" in y|Y|yes) ;; *) die "Cancelled." ;; esac

  cli backup --out /root >/dev/null 2>&1 && ok "Safety backup of the current state saved in /root"
  for s in "$SVC_PANEL" "$SVC_UDP" "$SVC_TCP" "$SVC_XRAY"; do systemctl stop "$s" 2>/dev/null; done
  tar -xzf "$file" -C / || die "Extraction failed."
  ok "Backup extracted."
  # Re-render services, hook config and permissions from the restored settings.
  bash "${APP_DIR}/install.sh" --yes
  warn "If this is a different server/IP, update the server address in Settings;"
  warn "users then just re-download their config from their subscription link."
}

cmd_update() {
  echo -e "Updating Waze Panel from GitHub (users, certificates and settings are kept)..."
  local tmp; tmp="$(mktemp)"
  curl -fsSL "$INSTALL_URL" -o "$tmp" || die "Could not download the installer."
  bash "$tmp" --yes
  rm -f "$tmp"
}

cmd_xray() {
  case "${1:-status}" in
    install|update)
      shift
      bash "${APP_DIR}/scripts/xray-install.sh" "$@" ;;
    restart)
      cli xray-apply >/dev/null && ok "Xray restarted" || die "Xray did not start; see: waze-panel logs xray" ;;
    status)
      if [ -x "$XRAY_BIN" ]; then
        "$XRAY_BIN" version 2>/dev/null | head -n1
        svc_line "$SVC_XRAY" "Xray"
      else
        warn "Xray is not installed: waze-panel xray install"
      fi ;;
    *) die "Usage: waze-panel xray [install|update|status|restart]  (update takes --version vX.Y.Z)" ;;
  esac
}

cmd_uninstall() {
  [ -f "${APP_DIR}/uninstall.sh" ] || die "uninstall.sh not found in ${APP_DIR}."
  bash "${APP_DIR}/uninstall.sh"
}

usage() {
  sed -n '3,17p' "$0" | sed 's/^# \{0,1\}//'
}

menu() {
  while true; do
    cmd_status
    echo -e "  ${C_BOLD}1)${C_RESET} Restart services      ${C_BOLD}5)${C_RESET} Backup"
    echo -e "  ${C_BOLD}2)${C_RESET} Panel logs            ${C_BOLD}6)${C_RESET} Restore backup"
    echo -e "  ${C_BOLD}3)${C_RESET} OpenVPN logs (UDP)    ${C_BOLD}7)${C_RESET} Update to latest"
    echo -e "  ${C_BOLD}4)${C_RESET} Reset admin password  ${C_BOLD}8)${C_RESET} Uninstall"
    echo -e "  ${C_BOLD}9)${C_RESET} Xray logs"
    echo -e "  ${C_BOLD}0)${C_RESET} Exit\n"
    read -r -p "Choose: " choice </dev/tty || exit 0
    case "$choice" in
      1) cmd_restart ;;
      2) cmd_logs panel ;;
      3) cmd_logs udp ;;
      4) cmd_reset_password ;;
      5) cmd_backup ;;
      6) read -r -p "Path to backup file: " f </dev/tty; cmd_restore "$f" ;;
      7) cmd_update; exit 0 ;;
      8) cmd_uninstall; exit 0 ;;
      9) cmd_logs xray ;;
      0|q|"") exit 0 ;;
      *) warn "Unknown option." ;;
    esac
    read -r -p "Press Enter to continue..." _ </dev/tty || true
  done
}

case "${1:-menu}" in
  menu) menu ;;
  status|info) cmd_status ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  logs) cmd_logs "${2:-panel}" ;;
  reset-password|password) cmd_reset_password "${2:-}" ;;
  backup) cmd_backup "${2:-/root}" ;;
  restore) cmd_restore "${2:-}" ;;
  update) cmd_update ;;
  xray) shift; cmd_xray "$@" ;;
  version|-v|--version) cli version ;;
  uninstall) cmd_uninstall ;;
  -h|--help|help) usage ;;
  *) usage; exit 1 ;;
esac
