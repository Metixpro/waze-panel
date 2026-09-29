#!/usr/bin/env bash
#
# Install or update the Xray core that Waze Panel runs VLESS / VMess /
# Trojan / Shadowsocks on. Called by install.sh and `waze-panel xray update`.
#
#   xray-install.sh                    latest release
#   xray-install.sh --version v26.9.9  a specific one
#   xray-install.sh --force            reinstall even if it is current
#
# Takes the official build from github.com/XTLS/Xray-core, checks it against
# the SHA-256 published with the release, and runs it as the waze-xray
# service (nobody:nogroup, allowed to bind ports below 1024 and nothing more).
# The panel writes its config and restarts it; users and inbounds are only
# ever in the panel's database.
#
set -euo pipefail

XRAY_HOME="${XRAY_HOME:-/usr/local/share/waze-panel/xray}"
APP_DIR="${APP_DIR:-/opt/waze-panel}"
DATA_DIR="${DATA_DIR:-/etc/waze-panel}"
REPO="XTLS/Xray-core"
SERVICE="waze-xray"

VERSION=""
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

C_RESET="\033[0m"; C_GREEN="\033[1;32m"; C_YELLOW="\033[1;33m"; C_RED="\033[1;31m"
ok()   { echo -e "    ${C_GREEN}OK${C_RESET} $*"; }
warn() { echo -e "    ${C_YELLOW}!${C_RESET} $*"; }
die()  { echo -e "    ${C_RED}FAIL${C_RESET} $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root"

case "$(uname -m)" in
  x86_64|amd64)  ASSET="Xray-linux-64.zip" ;;
  aarch64|arm64) ASSET="Xray-linux-arm64-v8a.zip" ;;
  armv7*|armv8l) ASSET="Xray-linux-arm32-v7a.zip" ;;
  *) die "no Xray build for this CPU ($(uname -m))" ;;
esac

# The newest release: GitHub's /releases/latest redirects to its tag page;
# the API and the tag list are fallbacks for when one of them is blocked.
latest_tag() {
  local tag
  tag="$(curl -fsSL -o /dev/null -w '%{url_effective}' --max-time 20 "https://github.com/${REPO}/releases/latest" 2>/dev/null || true)"
  tag="${tag##*/}"
  if ! [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    tag="$(curl -fsSL --max-time 20 "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null \
      | grep -oE '"tag_name": *"v[0-9]+\.[0-9]+\.[0-9]+"' | grep -oE 'v[0-9.]+' | head -n1 || true)"
  fi
  if ! [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] && command -v git >/dev/null 2>&1; then
    tag="$(git ls-remote --tags --refs "https://github.com/${REPO}.git" 2>/dev/null \
      | grep -oE 'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's#refs/tags/##' | sort -V | tail -n1 || true)"
  fi
  echo "$tag"
}

[ -n "$VERSION" ] || VERSION="$(latest_tag)"
[[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "could not find the latest Xray release (is github.com reachable from this server?)"

CURRENT=""
if [ -x "${XRAY_HOME}/xray" ]; then
  CURRENT="v$("${XRAY_HOME}/xray" version 2>/dev/null | awk 'NR==1 {print $2}')"
fi

if [ "$CURRENT" = "$VERSION" ] && [ "$FORCE" -eq 0 ]; then
  ok "Xray ${VERSION} is already installed."
else
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  BASE="https://github.com/${REPO}/releases/download/${VERSION}"
  curl -fsSL --retry 3 --max-time 300 -o "${TMP}/xray.zip" "${BASE}/${ASSET}" || die "download failed: ${BASE}/${ASSET}"
  curl -fsSL --retry 3 --max-time 60 -o "${TMP}/xray.dgst" "${BASE}/${ASSET}.dgst" || die "download failed: ${ASSET}.dgst"
  WANT="$(sed -n 's/^SHA2-256= *//p' "${TMP}/xray.dgst" | tr -d ' \r' | head -n1)"
  GOT="$(sha256sum "${TMP}/xray.zip" | awk '{print $1}')"
  [ -n "$WANT" ] && [ "$WANT" = "$GOT" ] || die "checksum mismatch for ${ASSET} (${GOT}); not installing it"

  python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "${TMP}/xray.zip" "${TMP}/x"
  chmod 755 "${TMP}/x/xray"   # zipfile does not keep the executable bit
  "${TMP}/x/xray" version >/dev/null 2>&1 || die "the downloaded xray does not run on this server"
  install -d -m 755 "$XRAY_HOME"
  install -m 755 "${TMP}/x/xray" "${XRAY_HOME}/xray.new"
  mv -f "${XRAY_HOME}/xray.new" "${XRAY_HOME}/xray"
  for f in geoip.dat geosite.dat; do
    [ -f "${TMP}/x/${f}" ] && install -m 644 "${TMP}/x/${f}" "${XRAY_HOME}/${f}"
  done
  if [ -n "$CURRENT" ] && [ "$CURRENT" != "v" ]; then
    ok "Xray updated: ${CURRENT} -> ${VERSION}"
  else
    ok "Xray ${VERSION} installed."
  fi
fi

# ------------------------------------------------------------ the service
GROUP="nogroup"
getent group nogroup >/dev/null 2>&1 || GROUP="nobody"
mkdir -p "${DATA_DIR}/xray"

cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=Waze Panel - Xray core (VLESS, VMess, Trojan, Shadowsocks)
Documentation=https://github.com/Metixpro/waze-panel
After=network-online.target
Wants=network-online.target
# the panel writes this file (and restarts us) whenever inbounds or users change
ConditionPathExists=${DATA_DIR}/xray/config.json

[Service]
User=nobody
Group=${GROUP}
Environment=XRAY_LOCATION_ASSET=${XRAY_HOME}
ExecStart=${XRAY_HOME}/xray run -c ${DATA_DIR}/xray/config.json
Restart=on-failure
RestartSec=3
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1 || true

# Config from the panel's database, then (re)start. Without the panel (this
# script run on its own before install.sh) there is nothing to run yet.
if [ -x "${APP_DIR}/venv/bin/python" ] && [ -f "${DATA_DIR}/panel.env" ]; then
  if (cd "$APP_DIR" && ./venv/bin/python -m app.cli xray-apply); then
    ok "Xray is running."
  else
    warn "Xray did not start; see: journalctl -u ${SERVICE} -e"
  fi
fi
