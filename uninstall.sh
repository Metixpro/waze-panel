#!/usr/bin/env bash
#
# Waze Panel uninstaller.
#
# Usage:
#   sudo bash uninstall.sh            # interactive, asks before deleting data
#   sudo bash uninstall.sh --purge    # remove everything, no prompts
#
set -euo pipefail

APP_DIR="/opt/waze-panel"
DATA_DIR="/etc/waze-panel"
OVPN_DIR="/etc/openvpn"
EASYRSA_DIR="${OVPN_DIR}/easy-rsa"

C_BLUE="\033[1;34m"; C_GREEN="\033[1;32m"; C_YELLOW="\033[1;33m"; C_RESET="\033[0m"
log_step() { echo -e "\n${C_BLUE}==>${C_RESET} $*"; }
log_ok()   { echo -e "    ${C_GREEN}OK${C_RESET} $*"; }
log_warn() { echo -e "    ${C_YELLOW}!${C_RESET} $*"; }

[ "$(id -u)" -eq 0 ] || { echo "This script must be run as root/sudo." >&2; exit 1; }

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

confirm() {
  local prompt="$1" answer
  if [ "$PURGE" -eq 1 ] || [ ! -t 0 ]; then
    echo "y"
    return
  fi
  read -r -p "$prompt [y/N]: " answer </dev/tty || true
  case "$answer" in y|Y|yes|Yes) echo "y" ;; *) echo "n" ;; esac
}

log_step "Stopping services"
for svc in waze-panel.service waze-panel-nat.service \
  "openvpn-server@waze-udp" "openvpn-server@waze-tcp" \
  "openvpn@waze-udp" "openvpn@waze-tcp"; do
  systemctl disable --now "$svc" >/dev/null 2>&1 || true
done
log_ok "Services stopped."

log_step "Removing systemd files"
rm -f /etc/systemd/system/waze-panel.service /etc/systemd/system/waze-panel-nat.service
rm -rf /etc/systemd/system/openvpn-server@waze-{udp,tcp}.service.d /etc/systemd/system/openvpn@waze-{udp,tcp}.service.d
rm -f /usr/local/bin/waze-panel /etc/logrotate.d/waze-panel
rm -f "${OVPN_DIR}/server/waze-udp.conf" "${OVPN_DIR}/server/waze-tcp.conf"
rm -f "${OVPN_DIR}/waze-udp.conf" "${OVPN_DIR}/waze-tcp.conf"
systemctl daemon-reload
log_ok "Done."

log_step "Removing NAT/firewall rules"
if [ -f "${DATA_DIR}/setup-nat.sh" ]; then
  WAN_IF="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')"
  for SUBNET in 10.8.0.0/24 10.9.0.0/24; do
    [ -n "$WAN_IF" ] && iptables -t nat -D POSTROUTING -s "$SUBNET" -o "$WAN_IF" -j MASQUERADE 2>/dev/null || true
    iptables -D FORWARD -s "$SUBNET" -j ACCEPT 2>/dev/null || true
  done
  log_ok "NAT rules removed (if any were present)."
else
  log_warn "setup-nat.sh not found, skipping this step."
fi

if [ "$(confirm "Remove the panel, database, and all settings (${APP_DIR} and ${DATA_DIR})?")" = "y" ]; then
  rm -rf "$APP_DIR" "$DATA_DIR"
  log_ok "Panel and settings removed."
else
  log_warn "Panel and settings kept."
fi

if [ "$(confirm "Also remove the certificate infrastructure (CA/PKI)? This permanently invalidates every issued user config.")" = "y" ]; then
  rm -rf "$EASYRSA_DIR"
  rm -f "${OVPN_DIR}/server/ta.key" "${OVPN_DIR}/server/dh.pem" "${OVPN_DIR}/server/crl.pem"
  rm -f "${OVPN_DIR}/server/status-udp.log" "${OVPN_DIR}/server/status-tcp.log"
  rm -f "${OVPN_DIR}/server/ipp-udp.txt" "${OVPN_DIR}/server/ipp-tcp.txt"
  log_ok "PKI and OpenVPN server files removed."
else
  log_warn "PKI kept."
fi

if [ -f /etc/nginx/sites-enabled/waze-panel.conf ] && [ "$(confirm "Also remove the panel's Nginx configuration?")" = "y" ]; then
  rm -f /etc/nginx/sites-enabled/waze-panel.conf /etc/nginx/sites-available/waze-panel.conf
  systemctl reload nginx 2>/dev/null || true
  log_ok "Nginx configuration removed."
fi

echo
log_ok "Waze Panel uninstall complete."
echo -e "    ${C_YELLOW}Note:${C_RESET} the openvpn/easy-rsa/nginx packages were not removed from the system (run apt remove manually if needed)."
