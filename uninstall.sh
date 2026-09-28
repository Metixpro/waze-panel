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
log_ok()   { echo -e "    ${C_GREEN}✓${C_RESET} $*"; }
log_warn() { echo -e "    ${C_YELLOW}!${C_RESET} $*"; }

[ "$(id -u)" -eq 0 ] || { echo "این اسکریپت باید با sudo/root اجرا شود." >&2; exit 1; }

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

log_step "توقف سرویس‌ها"
for svc in waze-panel.service waze-panel-nat.service \
  "openvpn-server@waze-udp" "openvpn-server@waze-tcp" \
  "openvpn@waze-udp" "openvpn@waze-tcp"; do
  systemctl disable --now "$svc" >/dev/null 2>&1 || true
done
log_ok "سرویس‌ها متوقف شدند."

log_step "حذف فایل‌های systemd"
rm -f /etc/systemd/system/waze-panel.service /etc/systemd/system/waze-panel-nat.service
rm -f "${OVPN_DIR}/server/waze-udp.conf" "${OVPN_DIR}/server/waze-tcp.conf"
rm -f "${OVPN_DIR}/waze-udp.conf" "${OVPN_DIR}/waze-tcp.conf"
systemctl daemon-reload
log_ok "انجام شد."

log_step "حذف قوانین NAT/فایروال"
if [ -f "${DATA_DIR}/setup-nat.sh" ]; then
  WAN_IF="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')"
  for SUBNET in 10.8.0.0/24 10.9.0.0/24; do
    [ -n "$WAN_IF" ] && iptables -t nat -D POSTROUTING -s "$SUBNET" -o "$WAN_IF" -j MASQUERADE 2>/dev/null || true
    iptables -D FORWARD -s "$SUBNET" -j ACCEPT 2>/dev/null || true
  done
  log_ok "قوانین NAT حذف شدند (در صورت وجود)."
else
  log_warn "فایل setup-nat.sh پیدا نشد، این مرحله رد شد."
fi

if [ "$(confirm "پنل، پایگاه‌داده و تمام تنظیمات (${APP_DIR} و ${DATA_DIR}) حذف شوند؟")" = "y" ]; then
  rm -rf "$APP_DIR" "$DATA_DIR"
  log_ok "پنل و تنظیمات حذف شدند."
else
  log_warn "پنل و تنظیمات نگه داشته شدند."
fi

if [ "$(confirm "زیرساخت گواهی (CA/PKI) هم حذف شود؟ این کار همه کانفیگ‌های صادرشده کاربران را برای همیشه بی‌اعتبار می‌کند.")" = "y" ]; then
  rm -rf "$EASYRSA_DIR"
  rm -f "${OVPN_DIR}/server/ta.key" "${OVPN_DIR}/server/dh.pem" "${OVPN_DIR}/server/crl.pem"
  rm -f "${OVPN_DIR}/server/status-udp.log" "${OVPN_DIR}/server/status-tcp.log"
  rm -f "${OVPN_DIR}/server/ipp-udp.txt" "${OVPN_DIR}/server/ipp-tcp.txt"
  log_ok "PKI و فایل‌های سرور OpenVPN حذف شدند."
else
  log_warn "PKI نگه داشته شد."
fi

if [ -f /etc/nginx/sites-enabled/waze-panel.conf ] && [ "$(confirm "پیکربندی Nginx مربوط به پنل هم حذف شود؟")" = "y" ]; then
  rm -f /etc/nginx/sites-enabled/waze-panel.conf /etc/nginx/sites-available/waze-panel.conf
  systemctl reload nginx 2>/dev/null || true
  log_ok "پیکربندی Nginx حذف شد."
fi

echo
log_ok "حذف نصب Waze Panel تمام شد."
echo -e "    ${C_YELLOW}نکته:${C_RESET} پکیج‌های openvpn/easy-rsa/nginx از سیستم حذف نشدند (در صورت نیاز به صورت دستی apt remove کنید)."
