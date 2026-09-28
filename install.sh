#!/usr/bin/env bash
#
# Waze Panel installer / updater
# ---------------------------------------------------------------
# Sets up a self-contained OpenVPN admin panel on a Debian/Ubuntu server:
# installs OpenVPN + easy-rsa, generates a CA/server certificate, brings up
# two OpenVPN instances (UDP and TCP), installs the Waze Panel web app as a
# systemd service, and prints the admin login details.
#
# Re-running it on a server that already has Waze Panel is a safe in-place
# update: ports, server address, secrets, the PKI and the admin password
# are all kept.
#
# Usage:
#   bash <(curl -Ls https://raw.githubusercontent.com/Metixpro/waze-panel/main/install.sh)
#   sudo bash install.sh                     # from a checkout / downloaded file
#   sudo bash install.sh --yes               # no questions at all
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
UDP_MGMT_PORT=7505
TCP_MGMT_PORT=7506

# Where the rest of the code is, when this script is run from a checkout.
# Empty when it was piped in / fetched by the one-liner (`bash <(curl ...)`
# makes BASH_SOURCE a /dev/fd pipe, `bash -c "$(curl ...)"` leaves it
# empty): the code is then cloned from GitHub.
SCRIPT_SOURCE_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

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

# Value of KEY from an existing panel.env (empty if absent).
env_get() {
  [ -f "${DATA_DIR}/panel.env" ] || return 0
  sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}$/\1/p" "${DATA_DIR}/panel.env" | tail -n1 || true
}

# ============================================================
# Defaults (overridable via flags or env)
# ============================================================
ASSUME_YES=0
PANEL_PORT="${PANEL_PORT:-}"
UDP_PORT="${UDP_PORT:-}"
TCP_PORT="${TCP_PORT:-}"
ADMIN_USER="${ADMIN_USER:-}"
ADMIN_PASS="${ADMIN_PASS:-}"
SERVER_ADDRESS="${SERVER_ADDRESS:-}"
SETUP_NGINX="${SETUP_NGINX:-}"
DOMAIN="${DOMAIN:-}"
SKIP_NGINX=0

usage() {
  cat <<EOF
Waze Panel installer / updater

  --yes                 skip all prompts, use defaults / provided flags
  --panel-port PORT     web panel port (default: 8000)
  --udp-port PORT       OpenVPN UDP port (default: 1194)
  --tcp-port PORT       OpenVPN TCP port (default: 443, or 8443 if --domain given)
  --admin-user NAME     initial admin username (default: admin)
  --admin-pass PASS     admin password (default: random on first install,
                        unchanged on update)
  --server-address ADDR public IP or domain clients will connect to (default: auto-detected)
  --domain DOMAIN       set up Nginx + Let's Encrypt on this domain for the panel
  --no-nginx            never configure Nginx, even if --domain is given
  -h, --help            show this help

Running it again on an existing install updates it in place and keeps
ports, secrets, certificates, users and the admin password.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) ASSUME_YES=1 ;;
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

# Questions are read from the terminal itself, so they still work when the
# script arrives on stdin (`curl ... | bash`). No terminal -> defaults.
INTERACTIVE=1
if [ "$ASSUME_YES" -eq 1 ] || ! { : </dev/tty; } 2>/dev/null; then
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
    [ "$ASSUME_YES" -eq 1 ] && echo "y" || echo "$default"
    return
  fi
  read -r -p "$prompt [${default}]: " answer </dev/tty || true
  answer="${answer:-$default}"
  case "$answer" in
    y|Y|yes|Yes) echo "y" ;;
    *) echo "n" ;;
  esac
}

valid_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

# Is $2/$1 (proto/port) taken by something other than Waze Panel itself?
port_busy() {
  local proto="$1" port="$2" flag out
  command -v ss >/dev/null 2>&1 || return 1
  [ "$proto" = "udp" ] && flag="-lnup" || flag="-lntp"
  out="$(ss -H "$flag" "sport = :${port}" 2>/dev/null || true)"
  [ -n "$out" ] || return 1
  echo "$out" | grep -qE '"(openvpn|uvicorn)"' && return 1
  return 0
}

# free_port proto candidate... -> the first candidate that is free (and not
# already picked for another service), else the next free port after the
# last candidate.
free_port() {
  local proto="$1" p; shift
  for p in "$@"; do
    [ "$p" = "${PANEL_PORT:-}" ] && [ "$proto" = "tcp" ] && continue
    port_busy "$proto" "$p" || { echo "$p"; return; }
  done
  p="${!#}"
  while [ "$p" -lt 65535 ]; do
    p=$((p + 1))
    [ "$p" = "${PANEL_PORT:-}" ] && [ "$proto" = "tcp" ] && continue
    port_busy "$proto" "$p" || { echo "$p"; return; }
  done
  echo "$1"
}

# ask_port "label" proto default -> a free, valid port
ask_port() {
  local label="$1" proto="$2" port="$3"
  while true; do
    port="$(ask "$label" "$port")"
    if ! valid_port "$port"; then
      [ "$INTERACTIVE" -eq 1 ] || die "$label: '$port' is not a valid port."
      log_warn "'$port' is not a valid port." >&2
      continue
    fi
    if port_busy "$proto" "$port"; then
      [ "$INTERACTIVE" -eq 1 ] || die "$label: ${proto}/${port} is already in use by another program (see: ss -lnp | grep :${port}). Pick another with a flag."
      log_warn "${proto}/${port} is already used by another program; choose a different port." >&2
      continue
    fi
    echo "$port"
    return
  done
}

# ============================================================
# Pre-flight checks
# ============================================================
[ "$(id -u)" -eq 0 ] || die "This script must be run as root (sudo bash install.sh)"

if ! command -v apt-get >/dev/null 2>&1; then
  die "This installer only supports Debian/Ubuntu based distros (apt)."
fi

python_ok() {
  command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
}
PY_REQUIREMENT="Waze Panel needs Python 3.10 or newer (Ubuntu 22.04+ or Debian 12+)."
if command -v python3 >/dev/null 2>&1 && ! python_ok; then
  die "${PY_REQUIREMENT} This server has $(python3 -V 2>&1)."
fi

UPGRADE=0
if [ -f "${DATA_DIR}/panel.env" ]; then
  UPGRADE=1
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
if [ "$UPGRADE" -eq 1 ]; then
  log_step "Existing installation found - updating in place"
  cont=$(ask_yn "Update Waze Panel and keep all users, certificates and settings?" "y")
  [ "$cont" = "y" ] || die "Cancelled."

  PANEL_PORT="${PANEL_PORT:-$(env_get PANEL_PORT)}"
  UDP_PORT="${UDP_PORT:-$(env_get OVPN_UDP_PORT)}"
  TCP_PORT="${TCP_PORT:-$(env_get OVPN_TCP_PORT)}"
  SERVER_ADDRESS="${SERVER_ADDRESS:-$(env_get SERVER_ADDRESS)}"
  SECRET_KEY="$(env_get SECRET_KEY)"
  INTERNAL_TOKEN="$(env_get INTERNAL_TOKEN)"
  EXISTING_SUB_URL="$(env_get SUBSCRIPTION_BASE_URL)"
  EXISTING_TITLE="$(env_get PANEL_TITLE)"
  if [ -z "$DOMAIN" ] && [ -f /etc/nginx/sites-enabled/waze-panel.conf ]; then
    DOMAIN="$(grep -m1 -oP 'server_name\s+\K[^; ]+' /etc/nginx/sites-enabled/waze-panel.conf || true)"
    [ -n "$DOMAIN" ] && SETUP_NGINX=1
  fi
  [ -n "$SETUP_NGINX" ] || SETUP_NGINX=0
  log_info "Keeping: panel port ${PANEL_PORT}, UDP ${UDP_PORT}, TCP ${TCP_PORT}, address ${SERVER_ADDRESS}${DOMAIN:+, domain ${DOMAIN}}"
else
  log_step "Installation configuration"

  # Ports given as flags are used as they are; the rest default to the
  # usual ones, or the next free alternative if something already uses them.
  EXPLICIT_PORTS="${PANEL_PORT} ${UDP_PORT} ${TCP_PORT}"
  if [ -z "$SERVER_ADDRESS" ]; then
    log_info "Detecting the server's public IP..."
    SERVER_ADDRESS="$(curl -4 -fsSL --max-time 5 https://ifconfig.me 2>/dev/null || true)"
    [ -z "$SERVER_ADDRESS" ] && SERVER_ADDRESS="$(curl -4 -fsSL --max-time 5 https://api.ipify.org 2>/dev/null || true)"
    [ -z "$SERVER_ADDRESS" ] && SERVER_ADDRESS="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  ADMIN_USER="${ADMIN_USER:-admin}"
  [ -n "$SETUP_NGINX" ] || SETUP_NGINX=0
  [ "$SKIP_NGINX" -eq 1 ] && SETUP_NGINX=0

  pick_ports() {
    PANEL_PORT="${PANEL_PORT:-$(free_port tcp 8000 8080 2053)}"
    UDP_PORT="${UDP_PORT:-$(free_port udp 1194 1195)}"
    if [ -z "$TCP_PORT" ]; then
      # with a domain, 443 serves the panel over HTTPS
      if [ "$SETUP_NGINX" -eq 1 ]; then TCP_PORT="$(free_port tcp 8443 2083)"
      else TCP_PORT="$(free_port tcp 443 8443 2083)"; fi
    fi
  }
  pick_ports

  show_plan() {
    local panel_url="http://${SERVER_ADDRESS:-<server-ip>}:${PANEL_PORT}"
    [ "$SETUP_NGINX" -eq 1 ] && panel_url="https://${DOMAIN}"
    echo
    echo -e "    ${C_BOLD}Server address${C_RESET}   ${SERVER_ADDRESS:-${C_YELLOW}not detected${C_RESET}}"
    echo -e "    ${C_BOLD}Panel${C_RESET}            ${panel_url}"
    echo -e "    ${C_BOLD}Admin${C_RESET}            ${ADMIN_USER} ${ADMIN_PASS:+(password as given)}${ADMIN_PASS:-(a random password is generated)}"
    echo -e "    ${C_BOLD}OpenVPN${C_RESET}          UDP ${UDP_PORT}  +  TCP ${TCP_PORT}"
    [ "$SETUP_NGINX" -eq 1 ] || echo -e "    ${C_BOLD}HTTPS domain${C_RESET}     none (optional - choose 'c' to add one)"
    echo
  }

  customize() {
    SERVER_ADDRESS=$(ask "Public server address (IP or domain) clients will connect to" "${SERVER_ADDRESS:-YOUR_SERVER_IP}")
    ADMIN_USER=$(ask "Panel admin username" "$ADMIN_USER")
    if [ "$SKIP_NGINX" -eq 0 ]; then
      wants_domain=$(ask_yn "Set up the panel with a domain and free SSL (Let's Encrypt)?" "$([ "$SETUP_NGINX" -eq 1 ] && echo y || echo n)")
      if [ "$wants_domain" = "y" ]; then
        DOMAIN=$(ask "Domain that points to this server" "${DOMAIN}")
        if [ -n "$DOMAIN" ]; then SETUP_NGINX=1; else log_warn "No domain entered, skipping Nginx setup."; SETUP_NGINX=0; fi
      else
        SETUP_NGINX=0
      fi
    fi
    PANEL_PORT=$(ask_port "Web panel port" tcp "$PANEL_PORT")
    UDP_PORT=$(ask_port "OpenVPN port (UDP)" udp "$UDP_PORT")
    if [ "$SETUP_NGINX" -eq 1 ] && [ "$TCP_PORT" = "443" ]; then
      log_warn "Port 443 will serve the panel over HTTPS, so OpenVPN TCP needs another port."
      TCP_PORT="$(free_port tcp 8443 2083)"
    fi
    TCP_PORT=$(ask_port "OpenVPN port (TCP)" tcp "$TCP_PORT")
  }

  show_plan
  if [ "$INTERACTIVE" -eq 1 ]; then
    if [ -z "$SERVER_ADDRESS" ]; then
      log_warn "Could not detect this server's public IP; please enter it."
      customize
    else
      read -r -p "    Press Enter to install with these settings, or type 'c' to change them: " choice </dev/tty || true
      case "${choice:-}" in
        c|C|change|customize) customize ;;
        "") ;;
        *) log_warn "Unknown answer '${choice}', continuing with the settings above." ;;
      esac
    fi
  else
    [ -n "$SERVER_ADDRESS" ] || die "Could not detect the server's public IP - pass it with --server-address."
    # explicitly requested ports must really be free
    for pp in "tcp:$PANEL_PORT" "udp:$UDP_PORT" "tcp:$TCP_PORT"; do
      case " $EXPLICIT_PORTS " in *" ${pp#*:} "*)
        port_busy "${pp%%:*}" "${pp#*:}" && die "${pp%%:*}/${pp#*:} is already in use by another program (see: ss -lnp | grep :${pp#*:}). Pick another with a flag."
      esac
    done
  fi
fi
[ "$SKIP_NGINX" -eq 1 ] && SETUP_NGINX=0

for p in "$PANEL_PORT" "$UDP_PORT" "$TCP_PORT"; do
  valid_port "$p" || die "Invalid port '$p'."
done
[ "$PANEL_PORT" != "$TCP_PORT" ] || die "The panel and OpenVPN TCP cannot share port ${TCP_PORT}."

log_ok "Configuration complete. Starting installation..."

# ============================================================
# Install packages
# ============================================================
log_step "Installing system packages (this can take a few minutes)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  openvpn easy-rsa python3 python3-venv python3-pip \
  curl git rsync openssl iptables iproute2 sqlite3 ca-certificates logrotate >/dev/null
log_ok "Core packages installed."

python_ok || die "${PY_REQUIREMENT} This server has $(python3 -V 2>&1)."

if [ "$SETUP_NGINX" -eq 1 ]; then
  apt-get install -y -qq nginx certbot python3-certbot-nginx >/dev/null
  log_ok "Nginx and Certbot installed."
fi

mkdir -p "$LOG_DIR" "$DATA_DIR"

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

# Group OpenVPN drops to after start-up (and that its hook scripts run as).
if getent group nogroup >/dev/null 2>&1; then
  OVPN_GROUP="nogroup"
else
  OVPN_GROUP="nobody"
fi

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
export EASYRSA_BATCH=1
# easy-rsa defaults (825-day certs, 180-day CRL) would make OpenVPN reject
# every client once the CRL expires; use 10 years for both.
export EASYRSA_CERT_EXPIRE=3650
export EASYRSA_CRL_DAYS=3650
if [ ! -d pki ]; then
  export EASYRSA_REQ_CN="Waze-Panel-CA"
  ./easyrsa init-pki >/dev/null
  ./easyrsa build-ca nopass >/dev/null 2>&1
  log_ok "CA created."
  # build-server-full derives its own CN from the "server" argument and
  # conflicts with an externally-set EASYRSA_REQ_CN, so it must not be set
  # for this call (or for build-client-full, used later by the panel).
  unset EASYRSA_REQ_CN
  ./easyrsa build-server-full server nopass >/dev/null 2>&1
  log_ok "Server certificate created."
  ./easyrsa gen-crl >/dev/null 2>&1
  log_ok "Certificate revocation list (CRL) created."
else
  log_info "PKI already exists, keeping it."
  ./easyrsa gen-crl >/dev/null 2>&1 && log_ok "CRL re-signed (valid for 10 years)." || log_warn "Could not re-sign the CRL."
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

if [ -n "$SCRIPT_SOURCE_DIR" ] && [ -f "${SCRIPT_SOURCE_DIR}/app/main.py" ]; then
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
    git -C "$APP_DIR" fetch --quiet --depth 1 origin main
    git -C "$APP_DIR" reset --quiet --hard FETCH_HEAD
  else
    TMP_CLONE="$(mktemp -d)"
    git clone --quiet --depth 1 "$REPO_URL" "${TMP_CLONE}/src"
    mkdir -p "$APP_DIR"
    rsync -a --delete --exclude 'venv' "${TMP_CLONE}/src/" "${APP_DIR}/"
    rm -rf "$TMP_CLONE"
  fi
  log_ok "Latest code fetched from GitHub."
fi

# From here on the script needs to run with $APP_DIR as the working
# directory: `python -m app.cli` resolves the `app` package via the
# current directory, and the earlier PKI step left us in $EASYRSA_DIR.
cd "$APP_DIR"

chmod +x "${APP_DIR}"/scripts/*.py "${APP_DIR}"/scripts/*.sh "${APP_DIR}"/*.sh 2>/dev/null || true

log_info "Creating the Python virtualenv and installing dependencies..."
[ -x "${APP_DIR}/venv/bin/python" ] || python3 -m venv "${APP_DIR}/venv"
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
    -e "s#__OVPN_GROUP__#${OVPN_GROUP}#g" \
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
# Write panel.env / hook.env
# ============================================================
log_step "Writing the panel configuration"

[ -n "${SECRET_KEY:-}" ] || SECRET_KEY="$(rand_str 48)"
[ -n "${INTERNAL_TOKEN:-}" ] || INTERNAL_TOKEN="$(rand_str 48)"

SUBSCRIPTION_BASE_URL="${EXISTING_SUB_URL:-}"
if [ "$SETUP_NGINX" -eq 1 ] && [ -n "$DOMAIN" ] && [ -z "$SUBSCRIPTION_BASE_URL" ]; then
  SUBSCRIPTION_BASE_URL="http://${DOMAIN}"
fi

umask 077
cat > "${DATA_DIR}/panel.env" <<EOF
SECRET_KEY="${SECRET_KEY}"
INTERNAL_TOKEN="${INTERNAL_TOKEN}"
DATA_DIR="${DATA_DIR}"
DB_PATH="${DATA_DIR}/waze-panel.db"
OPENVPN_DIR="${OVPN_DIR}"
OPENVPN_SERVER_DIR="${OVPN_CONF_DIR}"
OVPN_SERVICE_PREFIX="${OVPN_SERVICE_PREFIX}"
EASYRSA_DIR="${EASYRSA_DIR}"
EASYRSA_PKI_DIR="${EASYRSA_DIR}/pki"
SERVER_ADDRESS="${SERVER_ADDRESS}"
OVPN_UDP_PORT="${UDP_PORT}"
OVPN_TCP_PORT="${TCP_PORT}"
OVPN_UDP_MGMT_PORT="${UDP_MGMT_PORT}"
OVPN_TCP_MGMT_PORT="${TCP_MGMT_PORT}"
PANEL_PORT="${PANEL_PORT}"
PANEL_TITLE="${EXISTING_TITLE:-Waze Panel}"
SUBSCRIPTION_BASE_URL="${SUBSCRIPTION_BASE_URL}"
TRAFFIC_POLL_INTERVAL_SECONDS="20"
EOF
chmod 600 "${DATA_DIR}/panel.env"

# OpenVPN runs the client-connect/disconnect hooks after dropping to
# nobody:nogroup, so they can't read panel.env. Give them their own file
# with only what they need, readable by that group.
cat > "${DATA_DIR}/hook.env" <<EOF
PANEL_PORT="${PANEL_PORT}"
INTERNAL_TOKEN="${INTERNAL_TOKEN}"
EOF
chown "root:${OVPN_GROUP}" "${DATA_DIR}/hook.env"
chmod 640 "${DATA_DIR}/hook.env"
umask 022
log_ok "Wrote ${DATA_DIR}/panel.env and hook.env."

# ============================================================
# Init DB + admin
# ============================================================
log_step "Preparing the database"
"${APP_DIR}/venv/bin/python" -m app.cli init-db > /dev/null
chmod 600 "${DATA_DIR}/waze-panel.db"

ADMIN_EXISTS=0
if [ -n "$("${APP_DIR}/venv/bin/python" -m app.cli list-admins 2>/dev/null || true)" ]; then
  ADMIN_EXISTS=1
fi

PASSWORD_NOTE=""
if [ "$ADMIN_EXISTS" -eq 1 ] && [ -z "$ADMIN_PASS" ]; then
  ADMIN_USER="${ADMIN_USER:-$("${APP_DIR}/venv/bin/python" -m app.cli list-admins | head -n1 || true)}"
  PASSWORD_NOTE="(unchanged - use 'waze-panel reset-password' if you lost it)"
  log_ok "Existing admin account kept."
else
  ADMIN_USER="${ADMIN_USER:-admin}"
  [ -n "$ADMIN_PASS" ] || ADMIN_PASS="$(rand_str 16)"
  if "${APP_DIR}/venv/bin/python" -m app.cli create-admin --username "$ADMIN_USER" --password "$ADMIN_PASS" >/dev/null 2>&1; then
    log_ok "Admin user '${ADMIN_USER}' created."
  else
    "${APP_DIR}/venv/bin/python" -m app.cli reset-password --username "$ADMIN_USER" --password "$ADMIN_PASS" >/dev/null \
      || die "Failed to create the admin user."
    log_ok "Password set for admin '${ADMIN_USER}'."
  fi
fi

# ============================================================
# Optional: Nginx + Let's Encrypt (decides where the panel listens)
# ============================================================
PANEL_HOST="0.0.0.0"
if [ "$SETUP_NGINX" -eq 1 ] && [ -n "$DOMAIN" ]; then
  log_step "Configuring Nginx and SSL for ${DOMAIN}"
  if [ ! -f /etc/letsencrypt/live/"${DOMAIN}"/fullchain.pem ] || [ ! -f /etc/nginx/sites-available/waze-panel.conf ]; then
    sed -e "s#__DOMAIN__#${DOMAIN}#g" -e "s#__PANEL_PORT__#${PANEL_PORT}#g" \
      "${APP_DIR}/scripts/nginx-waze-panel.conf.tmpl" > "/etc/nginx/sites-available/waze-panel.conf"
  fi
  ln -sf /etc/nginx/sites-available/waze-panel.conf /etc/nginx/sites-enabled/waze-panel.conf
  rm -f /etc/nginx/sites-enabled/default
  if nginx -t 2>/tmp/nginx-test.log && systemctl reload nginx; then
    log_ok "Nginx configured for ${DOMAIN}."
    if [ -f /etc/letsencrypt/live/"${DOMAIN}"/fullchain.pem ] || \
       certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect 2>/tmp/certbot.log; then
      log_ok "HTTPS is active via Let's Encrypt."
      SUBSCRIPTION_BASE_URL="https://${DOMAIN}"
      sed -i "s#^SUBSCRIPTION_BASE_URL=.*#SUBSCRIPTION_BASE_URL=\"${SUBSCRIPTION_BASE_URL}\"#" "${DATA_DIR}/panel.env"
      # Only reachable through Nginx from now on.
      PANEL_HOST="127.0.0.1"
    else
      log_warn "SSL certificate issuance failed (log: /tmp/certbot.log). Check the domain's DNS and run 'certbot --nginx -d ${DOMAIN}' later."
    fi
  else
    log_warn "Nginx config test failed (log: /tmp/nginx-test.log). The panel stays on its plain HTTP port."
    SETUP_NGINX=0
  fi
fi

# ============================================================
# NAT / firewall rules
# ============================================================
log_step "Configuring NAT and firewall rules"

WAN_IF="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}' || true)"
[ -n "$WAN_IF" ] || log_warn "Outbound interface not found; NAT can be configured manually."

PUBLIC_TCP_PORTS="${TCP_PORT}"
[ "$PANEL_HOST" = "0.0.0.0" ] && PUBLIC_TCP_PORTS="${PUBLIC_TCP_PORTS} ${PANEL_PORT}"

NAT_TMP="$(mktemp)"
cat > "$NAT_TMP" <<EOF
#!/usr/bin/env bash
# Auto-generated by the Waze Panel installer. Safe to re-run (idempotent).
set -e
WAN_IF="${WAN_IF}"
for SUBNET in 10.8.0.0/24 10.9.0.0/24; do
  if [ -n "\$WAN_IF" ] && ! iptables -t nat -C POSTROUTING -s "\$SUBNET" -o "\$WAN_IF" -j MASQUERADE 2>/dev/null; then
    iptables -t nat -A POSTROUTING -s "\$SUBNET" -o "\$WAN_IF" -j MASQUERADE
  fi
done
if ! iptables -C INPUT -p udp --dport ${UDP_PORT} -j ACCEPT 2>/dev/null; then
  iptables -I INPUT -p udp --dport ${UDP_PORT} -j ACCEPT
fi
for PORT in ${PUBLIC_TCP_PORTS}; do
  if ! iptables -C INPUT -p tcp --dport "\$PORT" -j ACCEPT 2>/dev/null; then
    iptables -I INPUT -p tcp --dport "\$PORT" -j ACCEPT
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
mv "$NAT_TMP" "${DATA_DIR}/setup-nat.sh"
chmod 700 "${DATA_DIR}/setup-nat.sh"
bash "${DATA_DIR}/setup-nat.sh" || log_warn "Could not apply iptables rules (is iptables available?)."
log_ok "NAT/firewall rules applied."

cp "${APP_DIR}/scripts/waze-panel-nat.service.tmpl" /etc/systemd/system/waze-panel-nat.service

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "${UDP_PORT}/udp" >/dev/null || true
  ufw allow "${TCP_PORT}/tcp" >/dev/null || true
  if [ "$PANEL_HOST" = "0.0.0.0" ]; then
    ufw allow "${PANEL_PORT}/tcp" >/dev/null || true
  fi
  [ "$SETUP_NGINX" -eq 1 ] && { ufw allow 80/tcp >/dev/null || true; ufw allow 443/tcp >/dev/null || true; }
  log_ok "ufw rules added."
fi

# ============================================================
# Log rotation + management command
# ============================================================
cat > /etc/logrotate.d/waze-panel <<'EOF'
/var/log/openvpn/udp.log /var/log/openvpn/tcp.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
    copytruncate
}
EOF

install -m 755 "${APP_DIR}/scripts/waze-panel-cli.sh" /usr/local/bin/waze-panel
log_ok "Installed the 'waze-panel' management command."

# ============================================================
# systemd services
# ============================================================
log_step "Starting services"

sed -e "s#__APP_DIR__#${APP_DIR}#g" -e "s#__PANEL_PORT__#${PANEL_PORT}#g" -e "s#__PANEL_HOST__#${PANEL_HOST}#g" \
  "${APP_DIR}/scripts/waze-panel.service.tmpl" > /etc/systemd/system/waze-panel.service

# Each step below is allowed to fail without killing the script: a single
# service refusing to start (bad config, a port already in use, ...) must
# not stop us from reaching the summary at the end, since that is the only
# place the freshly-generated admin password is ever shown. The per-service
# status loop right after this reports exactly what did or didn't come up.
systemctl daemon-reload || true

# OpenVPN asks the panel about every login, so start it after the panel
# (at boot too) -- clients connecting in between would be refused.
for conf in "$UDP_CONF_NAME" "$TCP_CONF_NAME"; do
  mkdir -p "/etc/systemd/system/${OVPN_SERVICE_PREFIX}${conf}.service.d"
  cat > "/etc/systemd/system/${OVPN_SERVICE_PREFIX}${conf}.service.d/waze-panel.conf" <<'EOF'
[Unit]
After=waze-panel.service
Wants=waze-panel.service
EOF
done
systemctl daemon-reload || true

SERVICES=(waze-panel-nat.service waze-panel.service "${OVPN_SERVICE_PREFIX}${UDP_CONF_NAME}" "${OVPN_SERVICE_PREFIX}${TCP_CONF_NAME}")
for svc in "${SERVICES[@]}"; do
  systemctl enable "$svc" >/dev/null 2>&1 || true
  # restart (not just start) so an update actually loads the new code/config
  systemctl restart "$svc" >/dev/null 2>&1 || true
done

sleep 3

for svc in "${SERVICES[@]:1}"; do
  if systemctl is-active --quiet "$svc"; then
    log_ok "Service ${svc} is running."
  else
    log_warn "Service ${svc} did not come up - check it with 'journalctl -u ${svc} -e'."
  fi
done

# ============================================================
# Summary
# ============================================================
if [ "$PANEL_HOST" = "127.0.0.1" ]; then
  PANEL_URL="https://${DOMAIN}"
else
  PANEL_URL="http://${SERVER_ADDRESS}:${PANEL_PORT}"
fi

echo
echo -e "${C_GREEN}${C_BOLD}=======================================================${C_RESET}"
if [ "$UPGRADE" -eq 1 ]; then
  echo -e "${C_GREEN}${C_BOLD}  Waze Panel updated successfully!${C_RESET}"
else
  echo -e "${C_GREEN}${C_BOLD}  Waze Panel installed successfully!${C_RESET}"
fi
echo -e "${C_GREEN}${C_BOLD}=======================================================${C_RESET}"
echo
echo -e "  ${C_BOLD}Panel URL:${C_RESET}      ${PANEL_URL}"
echo -e "  ${C_BOLD}Username:${C_RESET}       ${ADMIN_USER}"
if [ -n "$PASSWORD_NOTE" ]; then
  echo -e "  ${C_BOLD}Password:${C_RESET}       ${PASSWORD_NOTE}"
else
  echo -e "  ${C_BOLD}Password:${C_RESET}       ${ADMIN_PASS}"
fi
echo
echo -e "  ${C_BOLD}OpenVPN UDP port:${C_RESET} ${UDP_PORT}"
echo -e "  ${C_BOLD}OpenVPN TCP port:${C_RESET} ${TCP_PORT}"
echo
[ -z "$PASSWORD_NOTE" ] && echo -e "  Save this information somewhere safe; it will not be shown again."
echo -e "  Manage the server with:  ${C_BOLD}waze-panel${C_RESET}   (status, logs, backup, update, ...)"
echo
if [ "$PANEL_HOST" = "0.0.0.0" ]; then
  echo -e "  ${C_YELLOW}Security note:${C_RESET} the panel is reachable over plain HTTP. For better"
  echo -e "  security, point a domain at this server and re-run with --domain for free HTTPS."
  echo
fi
