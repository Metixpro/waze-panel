#!/usr/bin/env bash
#
# Waze Panel installer
# ---------------------------------------------------------------
# Sets up a self-contained OpenVPN admin panel on a fresh Debian/Ubuntu
# server: installs OpenVPN + easy-rsa, generates a CA/server certificate,
# brings up two OpenVPN instances (UDP and TCP), installs the Waze Panel
# web app as a systemd service, and prints the admin login details.
#
# Usage:
#   sudo bash install.sh                     # interactive
#   sudo bash install.sh --yes               # non-interactive, sane defaults
#   sudo bash install.sh --help              # see all flags
#
set -euo pipefail

# ============================================================
# Constants
# ============================================================
REPO_URL="https://github.com/Metixpro/waze-panel.git"
APP_DIR="/opt/waze-panel"
DATA_DIR="/etc/waze-panel"
OVPN_DIR="/etc/openvpn"
EASYRSA_DIR="${OVPN_DIR}/easy-rsa"
LOG_DIR="/var/log/openvpn"

SCRIPT_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ============================================================
# Output helpers
# ============================================================
C_RESET="\033[0m"; C_BLUE="\033[1;34m"; C_GREEN="\033[1;32m"
C_YELLOW="\033[1;33m"; C_RED="\033[1;31m"; C_BOLD="\033[1m"

log_step()  { echo -e "\n${C_BLUE}==>${C_RESET} ${C_BOLD}$*${C_RESET}"; }
log_info()  { echo -e "    $*"; }
log_ok()    { echo -e "    ${C_GREEN}✓${C_RESET} $*"; }
log_warn()  { echo -e "    ${C_YELLOW}!${C_RESET} $*"; }
log_err()   { echo -e "    ${C_RED}✗${C_RESET} $*" >&2; }
die()       { log_err "$*"; exit 1; }

# ============================================================
# Defaults (overridable via flags or env)
# ============================================================
ASSUME_YES=0
PANEL_PORT="${PANEL_PORT:-8000}"
UDP_PORT="${UDP_PORT:-1194}"
TCP_PORT="${TCP_PORT:-443}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-}"
SERVER_ADDRESS="${SERVER_ADDRESS:-}"
SETUP_NGINX="${SETUP_NGINX:-}"
DOMAIN="${DOMAIN:-}"
SKIP_NGINX=0

usage() {
  cat <<EOF
Waze Panel installer

  --yes                 skip all prompts, use defaults / provided flags
  --panel-port PORT     web panel port (default: 8000)
  --udp-port PORT       OpenVPN UDP port (default: 1194)
  --tcp-port PORT       OpenVPN TCP port (default: 443, or 8443 if --domain given)
  --admin-user NAME     initial admin username (default: admin)
  --admin-pass PASS     initial admin password (default: random, printed at the end)
  --server-address ADDR public IP or domain clients will connect to (default: auto-detected)
  --domain DOMAIN       set up Nginx + Let's Encrypt on this domain for the panel
  --no-nginx            never configure Nginx, even if --domain is given
  -h, --help            show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --yes) ASSUME_YES=1 ;;
    --panel-port) PANEL_PORT="$2"; shift ;;
    --udp-port) UDP_PORT="$2"; shift ;;
    --tcp-port) TCP_PORT="$2"; shift ;;
    --admin-user) ADMIN_USER="$2"; shift ;;
    --admin-pass) ADMIN_PASS="$2"; shift ;;
    --server-address) SERVER_ADDRESS="$2"; shift ;;
    --domain) DOMAIN="$2"; SETUP_NGINX=1; shift ;;
    --no-nginx) SKIP_NGINX=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown flag: $1 (see --help)" ;;
  esac
  shift
done

INTERACTIVE=1
if [ "$ASSUME_YES" -eq 1 ] || [ ! -t 0 ]; then
  INTERACTIVE=0
fi

ask() {
  # ask "prompt" "default" -> echoes the answer
  local prompt="$1" default="$2" answer
  if [ "$INTERACTIVE" -eq 0 ]; then
    echo "$default"
    return
  fi
  read -r -p "$prompt [$default]: " answer </dev/tty || true
  echo "${answer:-$default}"
}

ask_yn() {
  # ask_yn "prompt" "y|n default" -> echoes y or n
  local prompt="$1" default="$2" answer
  if [ "$INTERACTIVE" -eq 0 ]; then
    echo "$default"
    return
  fi
  read -r -p "$prompt [${default}]: " answer </dev/tty || true
  answer="${answer:-$default}"
  case "$answer" in
    y|Y|yes|Yes) echo "y" ;;
    *) echo "n" ;;
  esac
}

# ============================================================
# Pre-flight checks
# ============================================================
[ "$(id -u)" -eq 0 ] || die "این اسکریپت باید با دسترسی root اجرا شود (sudo bash install.sh)"

if ! command -v apt-get >/dev/null 2>&1; then
  die "این نصب‌کننده فقط از توزیع‌های مبتنی بر Debian/Ubuntu (apt) پشتیبانی می‌کند."
fi

if [ -f /etc/waze-panel/panel.env ]; then
  log_warn "به نظر می‌رسد Waze Panel قبلا نصب شده است (/etc/waze-panel/panel.env موجود است)."
  cont=$(ask_yn "آیا می‌خواهید نصب را دوباره اجرا و تنظیمات را بازنویسی کنید؟" "n")
  [ "$cont" = "y" ] || die "نصب لغو شد."
fi

echo -e "${C_BOLD}"
cat <<'BANNER'
 __      __                  ____                  _
 \ \    / /                 |  _ \                | |
  \ \  / /_ _ _______      _| |_) |_ _ __   ___ | |
   \ \/ / _` |_  / _ \ /\ / /  ___/ _` | '_ \ / _ \| |
    \  / (_| |/ /  __/\ V  /| |  | (_| | | | |  __/| |
     \/ \__,_/___\___| \_/ |_|   \__,_|_| |_|\___||_|

        OpenVPN admin panel — installer
BANNER
echo -e "${C_RESET}"

# ============================================================
# Gather configuration
# ============================================================
log_step "پیکربندی نصب"

if [ -z "$SERVER_ADDRESS" ]; then
  log_info "در حال تشخیص آی‌پی عمومی سرور..."
  DETECTED_IP="$(curl -4 -fsSL --max-time 5 https://ifconfig.me 2>/dev/null || true)"
  [ -z "$DETECTED_IP" ] && DETECTED_IP="$(curl -4 -fsSL --max-time 5 https://api.ipify.org 2>/dev/null || true)"
  [ -z "$DETECTED_IP" ] && DETECTED_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  SERVER_ADDRESS=$(ask "آدرس عمومی سرور (IP یا دامنه) که کاربران به آن وصل می‌شوند" "${DETECTED_IP:-YOUR_SERVER_IP}")
fi

ADMIN_USER=$(ask "نام کاربری ادمین پنل" "$ADMIN_USER")

if [ -z "$ADMIN_PASS" ]; then
  ADMIN_PASS="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16)"
  log_info "رمز عبور ادمین به صورت خودکار تولید شد (در انتها نمایش داده می‌شود)."
fi

PANEL_PORT=$(ask "پورت پنل وب" "$PANEL_PORT")
UDP_PORT=$(ask "پورت OpenVPN (UDP)" "$UDP_PORT")

if [ -z "$SETUP_NGINX" ]; then
  wants_domain=$(ask_yn "آیا می‌خواهید پنل با دامنه و SSL رایگان (Let's Encrypt) بالا بیاید؟" "n")
  if [ "$wants_domain" = "y" ]; then
    SETUP_NGINX=1
    DOMAIN=$(ask "دامنه‌ای که به این سرور اشاره می‌کند" "")
    [ -n "$DOMAIN" ] || { log_warn "دامنه وارد نشد، از راه‌اندازی Nginx صرف‌نظر شد."; SETUP_NGINX=0; }
  else
    SETUP_NGINX=0
  fi
fi
[ "$SKIP_NGINX" -eq 1 ] && SETUP_NGINX=0

if [ "$SETUP_NGINX" -eq 1 ] && [ "$TCP_PORT" = "443" ]; then
  log_warn "پورت 443 هم برای پنل (HTTPS) و هم پیش‌فرض برای OpenVPN TCP لازم است."
  TCP_PORT=$(ask "پورت OpenVPN (TCP) — چون دامنه/443 برای پنل استفاده می‌شود، پورت دیگری انتخاب کنید" "8443")
else
  TCP_PORT=$(ask "پورت OpenVPN (TCP)" "$TCP_PORT")
fi

UDP_MGMT_PORT=7505
TCP_MGMT_PORT=7506

log_ok "پیکربندی کامل شد. شروع نصب..."

# ============================================================
# Install packages
# ============================================================
log_step "نصب پکیج‌های سیستمی (ممکن است چند دقیقه طول بکشد)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  openvpn easy-rsa python3 python3-venv python3-pip \
  curl git rsync openssl iptables sqlite3 ca-certificates >/dev/null
log_ok "پکیج‌های اصلی نصب شدند."

if [ "$SETUP_NGINX" -eq 1 ]; then
  apt-get install -y -qq nginx certbot python3-certbot-nginx >/dev/null
  log_ok "Nginx و Certbot نصب شدند."
fi

mkdir -p "$LOG_DIR"

# ============================================================
# Detect the OpenVPN systemd template flavor
# ============================================================
if [ -f /lib/systemd/system/openvpn-server@.service ] || [ -f /usr/lib/systemd/system/openvpn-server@.service ]; then
  OVPN_SERVICE_PREFIX="openvpn-server@"
  OVPN_CONF_DIR="${OVPN_DIR}/server"
else
  OVPN_SERVICE_PREFIX="openvpn@"
  OVPN_CONF_DIR="${OVPN_DIR}"
fi
mkdir -p "$OVPN_CONF_DIR"
log_info "از قالب سرویس systemd «${OVPN_SERVICE_PREFIX}» با پوشه کانفیگ «${OVPN_CONF_DIR}» استفاده می‌شود."

# ============================================================
# Enable IP forwarding
# ============================================================
log_step "فعال‌سازی IP forwarding"
if ! grep -q '^net.ipv4.ip_forward=1' /etc/sysctl.conf 2>/dev/null; then
  echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf
fi
sysctl -w net.ipv4.ip_forward=1 >/dev/null
log_ok "ip_forward فعال شد."

# ============================================================
# PKI setup (easy-rsa)
# ============================================================
log_step "ساخت زیرساخت گواهی (CA/PKI) با easy-rsa"

if [ ! -d "$EASYRSA_DIR" ]; then
  EASYRSA_SHARE="$(find /usr/share -maxdepth 1 -iname 'easy-rsa' 2>/dev/null | head -n1)"
  [ -n "$EASYRSA_SHARE" ] || die "پکیج easy-rsa پیدا نشد."
  cp -r "$EASYRSA_SHARE" "$EASYRSA_DIR"
  # some distros ship easyrsa under a versioned subdir; flatten if so
  if [ ! -f "${EASYRSA_DIR}/easyrsa" ]; then
    inner="$(find "$EASYRSA_DIR" -maxdepth 1 -type d -iname '*easy-rsa*' | head -n1)"
    [ -n "$inner" ] && cp -r "$inner"/* "$EASYRSA_DIR"/
  fi
fi
chmod 700 "$EASYRSA_DIR"

cd "$EASYRSA_DIR"
if [ ! -d pki ]; then
  export EASYRSA_BATCH=1
  export EASYRSA_REQ_CN="Waze-Panel-CA"
  ./easyrsa init-pki >/dev/null
  ./easyrsa build-ca nopass >/dev/null
  log_ok "CA ساخته شد."
  # build-server-full derives its own CN from the "server" argument and
  # conflicts with an externally-set EASYRSA_REQ_CN, so it must not be set
  # for this call (or for build-client-full, used later by the panel).
  unset EASYRSA_REQ_CN
  ./easyrsa build-server-full server nopass >/dev/null
  log_ok "گواهی سرور ساخته شد."
  ./easyrsa gen-crl >/dev/null
  log_ok "لیست ابطال گواهی (CRL) ساخته شد."
else
  log_info "PKI از قبل وجود دارد، این مرحله رد شد."
fi

cp "${EASYRSA_DIR}/pki/crl.pem" "${OVPN_CONF_DIR}/crl.pem"
chmod 644 "${OVPN_CONF_DIR}/crl.pem"

if [ ! -f "${OVPN_CONF_DIR}/ta.key" ]; then
  openvpn --genkey secret "${OVPN_CONF_DIR}/ta.key"
  log_ok "کلید tls-crypt ساخته شد."
fi

if [ ! -f "${OVPN_CONF_DIR}/dh.pem" ]; then
  log_info "در حال ساخت پارامترهای Diffie-Hellman (ممکن است تا ۱ دقیقه طول بکشد)..."
  openssl dhparam -out "${OVPN_CONF_DIR}/dh.pem" 2048 2>/dev/null
  log_ok "پارامترهای DH ساخته شد."
fi

# ============================================================
# Deploy application code
# ============================================================
log_step "استقرار کد پنل در ${APP_DIR}"

if [ -f "${SCRIPT_SOURCE_DIR}/app/main.py" ]; then
  if [ "$(readlink -f "$SCRIPT_SOURCE_DIR")" = "$(readlink -f "$APP_DIR" 2>/dev/null || echo __none__)" ]; then
    log_info "در حال حاضر داخل ${APP_DIR} اجرا شده؛ کپی لازم نیست."
  else
    mkdir -p "$APP_DIR"
    rsync -a --delete \
      --exclude 'venv' --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
      "${SCRIPT_SOURCE_DIR}/" "${APP_DIR}/" 2>/dev/null || \
    cp -r "${SCRIPT_SOURCE_DIR}/." "${APP_DIR}/"
    log_ok "کد از مسیر جاری کپی شد."
  fi
else
  if [ -d "${APP_DIR}/.git" ]; then
    git -C "$APP_DIR" pull --quiet
  else
    rm -rf "$APP_DIR"
    git clone --quiet --depth 1 "$REPO_URL" "$APP_DIR"
  fi
  log_ok "کد از گیت‌هاب دریافت شد."
fi

chmod +x "${APP_DIR}"/scripts/*.py "${APP_DIR}"/*.sh 2>/dev/null || true

log_info "ساخت محیط مجازی پایتون و نصب وابستگی‌ها..."
python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${APP_DIR}/venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"
log_ok "وابستگی‌های پایتون نصب شدند."

# ============================================================
# Render OpenVPN server configs
# ============================================================
log_step "پیکربندی سرویس‌های OpenVPN (UDP + TCP)"

render_tmpl() {
  local tmpl="$1" dest="$2"
  sed \
    -e "s#__UDP_PORT__#${UDP_PORT}#g" \
    -e "s#__TCP_PORT__#${TCP_PORT}#g" \
    -e "s#__APP_DIR__#${APP_DIR}#g" \
    -e "s#__UDP_MGMT_PORT__#${UDP_MGMT_PORT}#g" \
    -e "s#__TCP_MGMT_PORT__#${TCP_MGMT_PORT}#g" \
    "$tmpl" > "$dest"
}

UDP_CONF_NAME="waze-udp"
TCP_CONF_NAME="waze-tcp"
render_tmpl "${APP_DIR}/scripts/openvpn-server-udp.conf.tmpl" "${OVPN_CONF_DIR}/${UDP_CONF_NAME}.conf"
render_tmpl "${APP_DIR}/scripts/openvpn-server-tcp.conf.tmpl" "${OVPN_CONF_DIR}/${TCP_CONF_NAME}.conf"

# these two configs reference the PKI/crl/dh/ta files with absolute paths
# already, but crl/ta/dh live under OVPN_CONF_DIR which may differ from the
# hard-coded /etc/openvpn/server used in the templates on older distros.
if [ "$OVPN_CONF_DIR" != "${OVPN_DIR}/server" ]; then
  sed -i "s#${OVPN_DIR}/server#${OVPN_CONF_DIR}#g" "${OVPN_CONF_DIR}/${UDP_CONF_NAME}.conf" "${OVPN_CONF_DIR}/${TCP_CONF_NAME}.conf"
fi

log_ok "فایل‌های کانفیگ OpenVPN ساخته شدند."

# ============================================================
# NAT / firewall rules
# ============================================================
log_step "پیکربندی NAT برای تانل‌های VPN"

WAN_IF="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')"
[ -n "$WAN_IF" ] || log_warn "اینترفیس خروجی پیدا نشد؛ NAT به صورت دستی قابل تنظیم است."

NAT_TMP="$(mktemp)"
cat > "$NAT_TMP" <<EOF
#!/usr/bin/env bash
# Auto-generated by Waze Panel installer. Safe to re-run (idempotent).
set -e
WAN_IF="${WAN_IF}"
for SUBNET in 10.8.0.0/24 10.9.0.0/24; do
  if [ -n "\$WAN_IF" ] && ! iptables -t nat -C POSTROUTING -s "\$SUBNET" -o "\$WAN_IF" -j MASQUERADE 2>/dev/null; then
    iptables -t nat -A POSTROUTING -s "\$SUBNET" -o "\$WAN_IF" -j MASQUERADE
  fi
done
for PORT_SPEC in "${UDP_PORT}/udp" "${TCP_PORT}/tcp"; do
  PORT="\${PORT_SPEC%/*}"; PROTO="\${PORT_SPEC#*/}"
  if ! iptables -C INPUT -p "\$PROTO" --dport "\$PORT" -j ACCEPT 2>/dev/null; then
    iptables -I INPUT -p "\$PROTO" --dport "\$PORT" -j ACCEPT
  fi
done
if ! iptables -C FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; then
  iptables -I FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT
fi
for SUBNET in 10.8.0.0/24 10.9.0.0/24; do
  if ! iptables -C FORWARD -s "\$SUBNET" -j ACCEPT 2>/dev/null; then
    iptables -I FORWARD -s "\$SUBNET" -j ACCEPT
  fi
done
EOF

mkdir -p "$DATA_DIR"
mv "$NAT_TMP" "${DATA_DIR}/setup-nat.sh"
chmod 700 "${DATA_DIR}/setup-nat.sh"
bash "${DATA_DIR}/setup-nat.sh"
log_ok "قوانین NAT/فایروال اعمال شدند."

cp "${APP_DIR}/scripts/waze-panel-nat.service.tmpl" /etc/systemd/system/waze-panel-nat.service

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow "${PANEL_PORT}/tcp" >/dev/null || true
  ufw allow "${UDP_PORT}/udp" >/dev/null || true
  ufw allow "${TCP_PORT}/tcp" >/dev/null || true
  [ "$SETUP_NGINX" -eq 1 ] && { ufw allow 80/tcp >/dev/null || true; ufw allow 443/tcp >/dev/null || true; }
  log_ok "قوانین ufw اضافه شدند."
fi

# ============================================================
# Write panel.env
# ============================================================
log_step "نوشتن فایل تنظیمات پنل"

SECRET_KEY="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 48)"
INTERNAL_TOKEN="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 48)"

SUBSCRIPTION_BASE_URL=""
if [ "$SETUP_NGINX" -eq 1 ] && [ -n "$DOMAIN" ]; then
  SUBSCRIPTION_BASE_URL="http://${DOMAIN}"
fi

mkdir -p "$DATA_DIR"
cat > "${DATA_DIR}/panel.env" <<EOF
SECRET_KEY="${SECRET_KEY}"
INTERNAL_TOKEN="${INTERNAL_TOKEN}"
DATA_DIR="${DATA_DIR}"
DB_PATH="${DATA_DIR}/waze-panel.db"
OPENVPN_DIR="${OVPN_DIR}"
OPENVPN_SERVER_DIR="${OVPN_CONF_DIR}"
EASYRSA_DIR="${EASYRSA_DIR}"
EASYRSA_PKI_DIR="${EASYRSA_DIR}/pki"
SERVER_ADDRESS="${SERVER_ADDRESS}"
OVPN_UDP_PORT="${UDP_PORT}"
OVPN_TCP_PORT="${TCP_PORT}"
OVPN_UDP_MGMT_PORT="${UDP_MGMT_PORT}"
OVPN_TCP_MGMT_PORT="${TCP_MGMT_PORT}"
PANEL_PORT="${PANEL_PORT}"
PANEL_TITLE="Waze Panel"
SUBSCRIPTION_BASE_URL="${SUBSCRIPTION_BASE_URL}"
TRAFFIC_POLL_INTERVAL_SECONDS="20"
EOF
chmod 600 "${DATA_DIR}/panel.env"
log_ok "فایل ${DATA_DIR}/panel.env نوشته شد."

# ============================================================
# Init DB + create admin
# ============================================================
log_step "ساخت پایگاه‌داده و کاربر ادمین"
"${APP_DIR}/venv/bin/python" -m app.cli init-db > /dev/null
if ! "${APP_DIR}/venv/bin/python" -m app.cli create-admin --username "$ADMIN_USER" --password "$ADMIN_PASS" 2>/tmp/waze-admin-err.log; then
  if grep -q "already exists" /tmp/waze-admin-err.log; then
    "${APP_DIR}/venv/bin/python" -m app.cli reset-password --username "$ADMIN_USER" --password "$ADMIN_PASS" >/dev/null
    log_ok "رمز عبور ادمین «${ADMIN_USER}» به‌روزرسانی شد."
  else
    cat /tmp/waze-admin-err.log >&2
    die "ساخت کاربر ادمین با خطا مواجه شد."
  fi
else
  log_ok "کاربر ادمین «${ADMIN_USER}» ساخته شد."
fi
rm -f /tmp/waze-admin-err.log

# ============================================================
# systemd services
# ============================================================
log_step "راه‌اندازی سرویس‌ها"

sed -e "s#__APP_DIR__#${APP_DIR}#g" -e "s#__PANEL_PORT__#${PANEL_PORT}#g" \
  "${APP_DIR}/scripts/waze-panel.service.tmpl" > /etc/systemd/system/waze-panel.service

systemctl daemon-reload

systemctl enable --now waze-panel-nat.service >/dev/null 2>&1
systemctl enable --now "${OVPN_SERVICE_PREFIX}${UDP_CONF_NAME}" >/dev/null 2>&1
systemctl enable --now "${OVPN_SERVICE_PREFIX}${TCP_CONF_NAME}" >/dev/null 2>&1
systemctl enable --now waze-panel.service >/dev/null 2>&1

sleep 2

for svc in "${OVPN_SERVICE_PREFIX}${UDP_CONF_NAME}" "${OVPN_SERVICE_PREFIX}${TCP_CONF_NAME}" waze-panel.service; do
  if systemctl is-active --quiet "$svc"; then
    log_ok "سرویس ${svc} در حال اجراست."
  else
    log_warn "سرویس ${svc} بالا نیامد — با «journalctl -u ${svc} -e» بررسی کنید."
  fi
done

# ============================================================
# Optional: Nginx + Let's Encrypt
# ============================================================
if [ "$SETUP_NGINX" -eq 1 ] && [ -n "$DOMAIN" ]; then
  log_step "پیکربندی Nginx و SSL برای ${DOMAIN}"
  sed -e "s#__DOMAIN__#${DOMAIN}#g" -e "s#__PANEL_PORT__#${PANEL_PORT}#g" \
    "${APP_DIR}/scripts/nginx-waze-panel.conf.tmpl" > "/etc/nginx/sites-available/waze-panel.conf"
  ln -sf /etc/nginx/sites-available/waze-panel.conf /etc/nginx/sites-enabled/waze-panel.conf
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
  log_ok "Nginx برای ${DOMAIN} پیکربندی شد."

  if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@${DOMAIN}" --redirect 2>/tmp/certbot.log; then
    log_ok "گواهی SSL با Let's Encrypt صادر شد."
    SUBSCRIPTION_BASE_URL="https://${DOMAIN}"
    sed -i "s#^SUBSCRIPTION_BASE_URL=.*#SUBSCRIPTION_BASE_URL=\"${SUBSCRIPTION_BASE_URL}\"#" "${DATA_DIR}/panel.env"
    systemctl restart waze-panel.service
  else
    log_warn "صدور گواهی SSL ناموفق بود (لاگ: /tmp/certbot.log). دامنه را بررسی کنید و بعدا 'certbot --nginx -d ${DOMAIN}' را دستی اجرا کنید."
  fi
fi

# ============================================================
# Summary
# ============================================================
if [ "$SETUP_NGINX" -eq 1 ] && [ -n "$DOMAIN" ]; then
  PANEL_URL="https://${DOMAIN}"
else
  PANEL_URL="http://${SERVER_ADDRESS}:${PANEL_PORT}"
fi

echo
echo -e "${C_GREEN}${C_BOLD}=======================================================${C_RESET}"
echo -e "${C_GREEN}${C_BOLD}  نصب Waze Panel با موفقیت تمام شد! 🎉${C_RESET}"
echo -e "${C_GREEN}${C_BOLD}=======================================================${C_RESET}"
echo
echo -e "  ${C_BOLD}آدرس پنل:${C_RESET}      ${PANEL_URL}"
echo -e "  ${C_BOLD}نام کاربری:${C_RESET}    ${ADMIN_USER}"
echo -e "  ${C_BOLD}رمز عبور:${C_RESET}      ${ADMIN_PASS}"
echo
echo -e "  ${C_BOLD}پورت OpenVPN UDP:${C_RESET} ${UDP_PORT}"
echo -e "  ${C_BOLD}پورت OpenVPN TCP:${C_RESET} ${TCP_PORT}"
echo
echo -e "  این اطلاعات را جایی امن ذخیره کنید؛ دوباره نمایش داده نخواهند شد."
echo -e "  فایل تنظیمات: ${DATA_DIR}/panel.env"
echo -e "  لاگ سرویس پنل: journalctl -u waze-panel.service -f"
echo
if [ "$SETUP_NGINX" -eq 0 ]; then
  echo -e "  ${C_YELLOW}نکته امنیتی:${C_RESET} پنل روی HTTP ساده در دسترس است. برای امنیت بیشتر،"
  echo -e "  یک دامنه تهیه کرده و نصب را با --domain دوباره اجرا کنید تا HTTPS رایگان فعال شود."
fi
echo
