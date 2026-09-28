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
log_ok()    { echo -e "    ${C_GREEN}OK${C_RESET} $*"; }
log_warn()  { echo -e "    ${C_YELLOW}!${C_RESET} $*"; }
log_err()   { echo -e "    ${C_RED}FAIL${C_RESET} $*" >&2; }
die()       { log_err "$*"; exit 1; }

# Random alphanumeric string of length $1. `tr </dev/urandom | head -c N`
# is the standard way to do this, but /dev/urandom keeps producing data
# after head has read enough and closes the pipe, so tr gets SIGPIPE; with
# `set -o pipefail` that makes the whole pipeline "fail" and, combined with
# `set -e`, silently kills the script right where this is called. The
# `|| true` swallows that specific non-fatal failure while still keeping
# the (already-correct) captured output.
rand_str() {
  tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c "$1" || true
}

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
[ "$(id -u)" -eq 0 ] || die "This script must be run as root (sudo bash install.sh)"

if ! command -v apt-get >/dev/null 2>&1; then
  die "This installer only supports Debian/Ubuntu based distros (apt)."
fi

if [ -f /etc/waze-panel/panel.env ]; then
  log_warn "Waze Panel appears to be already installed (/etc/waze-panel/panel.env exists)."
  cont=$(ask_yn "Re-run the installer and overwrite the existing configuration?" "n")
  [ "$cont" = "y" ] || die "Installation cancelled."
fi

echo -e "${C_BOLD}"
cat <<'BANNER'
 __      __                  ____                  _
 \ \    / /                 |  _ \                | |
  \ \  / /_ _ _______      _| |_) |_ _ __   ___ | |
   \ \/ / _` |_  / _ \ /\ / /  ___/ _` | '_ \ / _ \| |
    \  / (_| |/ /  __/\ V  /| |  | (_| | | | |  __/| |
     \/ \__,_/___\___| \_/ |_|   \__,_|_| |_|\___||_|

        OpenVPN admin panel - installer
BANNER
echo -e "${C_RESET}"

# ============================================================
# Gather configuration
# ============================================================
log_step "Installation configuration"

if [ -z "$SERVER_ADDRESS" ]; then
  log_info "Detecting the server's public IP..."
  DETECTED_IP="$(curl -4 -fsSL --max-time 5 https://ifconfig.me 2>/dev/null || true)"
  [ -z "$DETECTED_IP" ] && DETECTED_IP="$(curl -4 -fsSL --max-time 5 https://api.ipify.org 2>/dev/null || true)"
  [ -z "$DETECTED_IP" ] && DETECTED_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  SERVER_ADDRESS=$(ask "Public server address (IP or domain) clients will connect to" "${DETECTED_IP:-YOUR_SERVER_IP}")
fi

ADMIN_USER=$(ask "Panel admin username" "$ADMIN_USER")

if [ -z "$ADMIN_PASS" ]; then
  ADMIN_PASS="$(rand_str 16)"
  log_info "Admin password auto-generated (shown at the end)."
fi

PANEL_PORT=$(ask "Web panel port" "$PANEL_PORT")
UDP_PORT=$(ask "OpenVPN port (UDP)" "$UDP_PORT")

if [ -z "$SETUP_NGINX" ]; then
  wants_domain=$(ask_yn "Set up the panel with a domain and free SSL (Let's Encrypt)?" "n")
  if [ "$wants_domain" = "y" ]; then
    SETUP_NGINX=1
    DOMAIN=$(ask "Domain that points to this server" "")
    [ -n "$DOMAIN" ] || { log_warn "No domain entered, skipping Nginx setup."; SETUP_NGINX=0; }
  else
    SETUP_NGINX=0
  fi
fi
[ "$SKIP_NGINX" -eq 1 ] && SETUP_NGINX=0

if [ "$SETUP_NGINX" -eq 1 ] && [ "$TCP_PORT" = "443" ]; then
  log_warn "Port 443 is needed both for the panel (HTTPS) and by default for OpenVPN TCP."
  TCP_PORT=$(ask "OpenVPN port (TCP) - pick a different port since 443/domain is used by the panel" "8443")
else
  TCP_PORT=$(ask "OpenVPN port (TCP)" "$TCP_PORT")
fi

UDP_MGMT_PORT=7505
TCP_MGMT_PORT=7506

log_ok "Configuration complete. Starting installation..."

# ============================================================
# Install packages
# ============================================================
log_step "Installing system packages (this can take a few minutes)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  openvpn easy-rsa python3 python3-venv python3-pip \
  curl git rsync openssl iptables iproute2 sqlite3 ca-certificates >/dev/null
log_ok "Core packages installed."

if [ "$SETUP_NGINX" -eq 1 ]; then
  apt-get install -y -qq nginx certbot python3-certbot-nginx >/dev/null
  log_ok "Nginx and Certbot installed."
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
log_info "Using systemd service template \"${OVPN_SERVICE_PREFIX}\" with config dir \"${OVPN_CONF_DIR}\"."

# ============================================================
# Enable IP forwarding
# ============================================================
log_step "Enabling IP forwarding"
if ! grep -q '^net.ipv4.ip_forward=1' /etc/sysctl.conf 2>/dev/null; then
  echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf
fi
sysctl -w net.ipv4.ip_forward=1 >/dev/null
log_ok "ip_forward enabled."

# ============================================================
# PKI setup (easy-rsa)
# ============================================================
log_step "Building the certificate infrastructure (CA/PKI) with easy-rsa"

if [ ! -d "$EASYRSA_DIR" ]; then
  EASYRSA_SHARE="$(find /usr/share -maxdepth 1 -iname 'easy-rsa' 2>/dev/null | head -n1 || true)"
  [ -n "$EASYRSA_SHARE" ] || die "The easy-rsa package was not found."
  cp -r "$EASYRSA_SHARE" "$EASYRSA_DIR"
  # some distros ship easyrsa under a versioned subdir; flatten if so
  if [ ! -f "${EASYRSA_DIR}/easyrsa" ]; then
    inner="$(find "$EASYRSA_DIR" -maxdepth 1 -type d -iname '*easy-rsa*' | head -n1 || true)"
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
  log_ok "CA created."
  # build-server-full derives its own CN from the "server" argument and
  # conflicts with an externally-set EASYRSA_REQ_CN, so it must not be set
  # for this call (or for build-client-full, used later by the panel).
  unset EASYRSA_REQ_CN
  ./easyrsa build-server-full server nopass >/dev/null
  log_ok "Server certificate created."
  ./easyrsa gen-crl >/dev/null
  log_ok "Certificate revocation list (CRL) created."
else
  log_info "PKI already exists, skipping this step."
fi

cp "${EASYRSA_DIR}/pki/crl.pem" "${OVPN_CONF_DIR}/crl.pem"
chmod 644 "${OVPN_CONF_DIR}/crl.pem"

if [ ! -f "${OVPN_CONF_DIR}/ta.key" ]; then
  openvpn --genkey secret "${OVPN_CONF_DIR}/ta.key"
  log_ok "tls-crypt key created."
fi

if [ ! -f "${OVPN_CONF_DIR}/dh.pem" ]; then
  log_info "Generating Diffie-Hellman parameters (can take up to a minute)..."
  openssl dhparam -out "${OVPN_CONF_DIR}/dh.pem" 2048 2>/dev/null
  log_ok "DH parameters created."
fi

# ============================================================
# Deploy application code
# ============================================================
log_step "Deploying the panel code to ${APP_DIR}"

if [ -f "${SCRIPT_SOURCE_DIR}/app/main.py" ]; then
  if [ "$(readlink -f "$SCRIPT_SOURCE_DIR")" = "$(readlink -f "$APP_DIR" 2>/dev/null || echo __none__)" ]; then
    log_info "Already running from inside ${APP_DIR}; no copy needed."
  else
    mkdir -p "$APP_DIR"
    rsync -a --delete \
      --exclude 'venv' --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
      "${SCRIPT_SOURCE_DIR}/" "${APP_DIR}/" 2>/dev/null || \
    cp -r "${SCRIPT_SOURCE_DIR}/." "${APP_DIR}/"
    log_ok "Code copied from the current directory."
  fi
else
  if [ -d "${APP_DIR}/.git" ]; then
    git -C "$APP_DIR" pull --quiet
  else
    rm -rf "$APP_DIR"
    git clone --quiet --depth 1 "$REPO_URL" "$APP_DIR"
  fi
  log_ok "Code fetched from GitHub."
fi

# From here on the script needs to run with $APP_DIR as the working
# directory: `python -m app.cli` resolves the `app` package via the
# current directory, and the earlier PKI step left us in $EASYRSA_DIR.
cd "$APP_DIR"

chmod +x "${APP_DIR}"/scripts/*.py "${APP_DIR}"/*.sh 2>/dev/null || true

log_info "Creating the Python virtualenv and installing dependencies..."
python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${APP_DIR}/venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"
log_ok "Python dependencies installed."

# ============================================================
# Render OpenVPN server configs
# ============================================================
log_step "Configuring the OpenVPN services (UDP + TCP)"

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

log_ok "OpenVPN config files created."

# ============================================================
# NAT / firewall rules
# ============================================================
log_step "Configuring NAT for the VPN tunnels"

WAN_IF="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}' || true)"
[ -n "$WAN_IF" ] || log_warn "Outbound interface not found; NAT can be configured manually."

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
log_ok "NAT/firewall rules applied."

cp "${APP_DIR}/scripts/waze-panel-nat.service.tmpl" /etc/systemd/system/waze-panel-nat.service

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow "${PANEL_PORT}/tcp" >/dev/null || true
  ufw allow "${UDP_PORT}/udp" >/dev/null || true
  ufw allow "${TCP_PORT}/tcp" >/dev/null || true
  [ "$SETUP_NGINX" -eq 1 ] && { ufw allow 80/tcp >/dev/null || true; ufw allow 443/tcp >/dev/null || true; }
  log_ok "ufw rules added."
fi

# ============================================================
# Write panel.env
# ============================================================
log_step "Writing the panel configuration file"

SECRET_KEY="$(rand_str 48)"
INTERNAL_TOKEN="$(rand_str 48)"

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
log_ok "Wrote ${DATA_DIR}/panel.env."

# ============================================================
# Init DB + create admin
# ============================================================
log_step "Creating the database and the admin user"
"${APP_DIR}/venv/bin/python" -m app.cli init-db > /dev/null
if ! "${APP_DIR}/venv/bin/python" -m app.cli create-admin --username "$ADMIN_USER" --password "$ADMIN_PASS" 2>/tmp/waze-admin-err.log; then
  if grep -q "already exists" /tmp/waze-admin-err.log; then
    "${APP_DIR}/venv/bin/python" -m app.cli reset-password --username "$ADMIN_USER" --password "$ADMIN_PASS" >/dev/null
    log_ok "Password updated for admin '${ADMIN_USER}'."
  else
    cat /tmp/waze-admin-err.log >&2
    die "Failed to create the admin user."
  fi
else
  log_ok "Admin user '${ADMIN_USER}' created."
fi
rm -f /tmp/waze-admin-err.log

# ============================================================
# systemd services
# ============================================================
log_step "Starting services"

sed -e "s#__APP_DIR__#${APP_DIR}#g" -e "s#__PANEL_PORT__#${PANEL_PORT}#g" \
  "${APP_DIR}/scripts/waze-panel.service.tmpl" > /etc/systemd/system/waze-panel.service

# Each step below is allowed to fail without killing the script: a single
# service refusing to start (bad config, a port already in use, ...) must
# not stop us from reaching the summary at the end, since that is the only
# place the freshly-generated admin password is ever shown. The per-service
# status loop right after this reports exactly what did or didn't come up.
systemctl daemon-reload || true

systemctl enable --now waze-panel-nat.service >/dev/null 2>&1 || true
systemctl enable --now "${OVPN_SERVICE_PREFIX}${UDP_CONF_NAME}" >/dev/null 2>&1 || true
systemctl enable --now "${OVPN_SERVICE_PREFIX}${TCP_CONF_NAME}" >/dev/null 2>&1 || true
systemctl enable --now waze-panel.service >/dev/null 2>&1 || true

sleep 2

for svc in "${OVPN_SERVICE_PREFIX}${UDP_CONF_NAME}" "${OVPN_SERVICE_PREFIX}${TCP_CONF_NAME}" waze-panel.service; do
  if systemctl is-active --quiet "$svc"; then
    log_ok "Service ${svc} is running."
  else
    log_warn "Service ${svc} did not come up - check it with 'journalctl -u ${svc} -e'."
  fi
done

# ============================================================
# Optional: Nginx + Let's Encrypt
# ============================================================
if [ "$SETUP_NGINX" -eq 1 ] && [ -n "$DOMAIN" ]; then
  log_step "Configuring Nginx and SSL for ${DOMAIN}"
  sed -e "s#__DOMAIN__#${DOMAIN}#g" -e "s#__PANEL_PORT__#${PANEL_PORT}#g" \
    "${APP_DIR}/scripts/nginx-waze-panel.conf.tmpl" > "/etc/nginx/sites-available/waze-panel.conf"
  ln -sf /etc/nginx/sites-available/waze-panel.conf /etc/nginx/sites-enabled/waze-panel.conf
  rm -f /etc/nginx/sites-enabled/default
  if nginx -t 2>/tmp/nginx-test.log && systemctl reload nginx; then
    log_ok "Nginx configured for ${DOMAIN}."

    if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@${DOMAIN}" --redirect 2>/tmp/certbot.log; then
      log_ok "SSL certificate issued via Let's Encrypt."
      SUBSCRIPTION_BASE_URL="https://${DOMAIN}"
      sed -i "s#^SUBSCRIPTION_BASE_URL=.*#SUBSCRIPTION_BASE_URL=\"${SUBSCRIPTION_BASE_URL}\"#" "${DATA_DIR}/panel.env"
      systemctl restart waze-panel.service || true
    else
      log_warn "SSL certificate issuance failed (log: /tmp/certbot.log). Check the domain and run 'certbot --nginx -d ${DOMAIN}' manually later."
    fi
  else
    log_warn "Nginx config test failed (log: /tmp/nginx-test.log). The panel is still reachable on its plain HTTP port; fix and reload Nginx manually."
    SETUP_NGINX=0
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
echo -e "${C_GREEN}${C_BOLD}  Waze Panel installed successfully!${C_RESET}"
echo -e "${C_GREEN}${C_BOLD}=======================================================${C_RESET}"
echo
echo -e "  ${C_BOLD}Panel URL:${C_RESET}      ${PANEL_URL}"
echo -e "  ${C_BOLD}Username:${C_RESET}       ${ADMIN_USER}"
echo -e "  ${C_BOLD}Password:${C_RESET}       ${ADMIN_PASS}"
echo
echo -e "  ${C_BOLD}OpenVPN UDP port:${C_RESET} ${UDP_PORT}"
echo -e "  ${C_BOLD}OpenVPN TCP port:${C_RESET} ${TCP_PORT}"
echo
echo -e "  Save this information somewhere safe; it will not be shown again."
echo -e "  Config file: ${DATA_DIR}/panel.env"
echo -e "  Panel service log: journalctl -u waze-panel.service -f"
echo
if [ "$SETUP_NGINX" -eq 0 ]; then
  echo -e "  ${C_YELLOW}Security note:${C_RESET} the panel is reachable over plain HTTP. For better"
  echo -e "  security, get a domain and re-run the installer with --domain to enable free HTTPS."
fi
echo
