#!/usr/bin/env bash
#
# Waze Panel relay -- run this on a server INSIDE the country.
# ---------------------------------------------------------------
# Users connect to this server; it forwards their OpenVPN traffic (UDP and
# TCP) to the main Waze Panel server abroad, in the kernel (iptables DNAT),
# so there is no userspace proxy to slow things down or crash. Add the
# relay in the panel (Settings -> relay) and it copies this command for you:
#
#   bash <(curl -Ls https://raw.githubusercontent.com/Metixpro/waze-panel/main/relay.sh) \
#        --to MAIN_SERVER --udp 1194:1194 --tcp 443:443
#
#   --to HOST        the main (foreign) server: IP or domain
#   --udp L[:T]      forward UDP port L here to port T there (T defaults to L)
#   --tcp L[:T]      same for TCP; both flags can be repeated
#
# Afterwards manage it with:  waze-relay status | test | restart | uninstall
#
# This file only installs the `waze-relay` helper (embedded below, so it
# keeps working without internet access) and hands over to it.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root (e.g. sudo -i first)." >&2; exit 1; }

install -d /usr/local/bin
cat > /usr/local/bin/waze-relay <<'WAZE_RELAY'
#!/usr/bin/env bash
# waze-relay: forwards OpenVPN ports from this server to the main Waze Panel
# server. Config: /etc/waze-relay/relay.conf  Service: waze-relay.service
set -uo pipefail

CONF_DIR=/etc/waze-relay
CONF="${CONF_DIR}/relay.conf"
UNIT=/etc/systemd/system/waze-relay.service
SYSCTL=/etc/sysctl.d/99-waze-relay.conf

C0="\033[0m"; CB="\033[1m"; CG="\033[1;32m"; CY="\033[1;33m"; CR="\033[1;31m"; CU="\033[1;34m"
ok()   { echo -e "  ${CG}OK${C0} $*"; }
warn() { echo -e "  ${CY}!${C0} $*"; }
die()  { echo -e "  ${CR}FAIL${C0} $*" >&2; exit 1; }
step() { echo -e "\n${CU}==>${C0} ${CB}$*${C0}"; }

need_root() { [ "$(id -u)" -eq 0 ] || die "run as root"; }

valid_port() { [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]; }

# "L" or "L:T" -> "L:T"
norm_map() {
  local l="${1%%:*}" t="${1##*:}"
  valid_port "$l" && valid_port "$t" || die "bad port mapping '$1' (use LISTEN or LISTEN:TARGET)"
  echo "${l}:${t}"
}

load_conf() {
  [ -f "$CONF" ] || die "not set up yet: run the relay.sh command from the panel first"
  # shellcheck disable=SC1090
  . "$CONF"
  TO="${TO:-}"; UDP_MAP="${UDP_MAP:-}"; TCP_MAP="${TCP_MAP:-}"
}

# IPv4 of the main server (the config may hold a domain)
resolve_to() {
  if [[ "$TO" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo "$TO"; return; fi
  getent ahostsv4 "$TO" 2>/dev/null | awk 'NR==1{print $1}'
}

ipt() { iptables -w "$@"; }

# Remove our chains (and the jumps into them) from every table.
flush_rules() {
  local t chain parent
  for spec in "nat:WZR_PRE:PREROUTING" "nat:WZR_POST:POSTROUTING" "filter:WZR_FWD:FORWARD"; do
    IFS=: read -r t chain parent <<<"$spec"
    while ipt -t "$t" -D "$parent" -j "$chain" 2>/dev/null; do :; done
    ipt -t "$t" -F "$chain" 2>/dev/null || true
    ipt -t "$t" -X "$chain" 2>/dev/null || true
  done
}

# rules <main-ip> -> installs the forwarding rules from UDP_MAP / TCP_MAP
rules() {
  local ip="$1" m l t proto maps
  sysctl -qw net.ipv4.ip_forward=1
  flush_rules
  ipt -t nat -N WZR_PRE; ipt -t nat -N WZR_POST; ipt -N WZR_FWD
  ipt -t nat -I PREROUTING 1 -j WZR_PRE
  ipt -t nat -I POSTROUTING 1 -j WZR_POST
  ipt -I FORWARD 1 -j WZR_FWD
  for proto in udp tcp; do
    [ "$proto" = udp ] && maps="$UDP_MAP" || maps="$TCP_MAP"
    for m in $maps; do
      l="${m%%:*}"; t="${m##*:}"
      # only traffic addressed to this machine, never traffic passing through
      ipt -t nat -A WZR_PRE -p "$proto" --dport "$l" -m addrtype --dst-type LOCAL \
        -j DNAT --to-destination "${ip}:${t}"
      # replies must come back through us, so hide the client's address
      ipt -t nat -A WZR_POST -p "$proto" -d "$ip" --dport "$t" -j MASQUERADE
      ipt -A WZR_FWD -p "$proto" -d "$ip" --dport "$t" -j ACCEPT
    done
  done
  ipt -A WZR_FWD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
}

cmd_apply() {
  need_root; load_conf
  local ip; ip="$(resolve_to)"
  [ -n "$ip" ] || die "cannot resolve ${TO}"
  rules "$ip"
  echo "$ip" > "${CONF_DIR}/resolved"
  ok "forwarding to ${ip} (UDP: ${UDP_MAP:-none} | TCP: ${TCP_MAP:-none})"
}

cmd_stop() { need_root; flush_rules; ok "forwarding stopped"; }

# parse_args "$@" -> sets to / udp / tcp
parse_args() {
  to=""; udp=""; tcp=""
  local m
  while [ $# -gt 0 ]; do
    case "$1" in
      --to) to="${2:-}"; shift ;;
      --udp) m="$(norm_map "${2:-}")" || exit 1; udp="$udp $m"; shift ;;
      --tcp) m="$(norm_map "${2:-}")" || exit 1; tcp="$tcp $m"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown option: $1" ;;
    esac
    shift
  done
  udp="${udp# }"; tcp="${tcp# }"
  [ -n "$to" ] || die "--to MAIN_SERVER is required"
  [ -n "$udp$tcp" ] || die "give at least one --udp or --tcp port"
  [[ "$to" =~ ^[A-Za-z0-9.-]+$ ]] || die "bad --to address '$to'"
}

# Apply rules straight from flags, saving nothing (used by tests/e2e.sh
# inside a network namespace).
cmd_run() {
  need_root
  local to udp tcp
  parse_args "$@"
  TO="$to"; UDP_MAP="$udp"; TCP_MAP="$tcp"
  local ip; ip="$(resolve_to)"
  [ -n "$ip" ] || die "cannot resolve ${TO}"
  rules "$ip"
  ok "forwarding to ${ip} (UDP: ${UDP_MAP:-none} | TCP: ${TCP_MAP:-none})"
}

cmd_setup() {
  need_root
  local to udp tcp
  parse_args "$@"

  echo -e "${CB}"
  echo "  Waze Panel relay"
  echo -e "${C0}  this server  ->  ${to}"

  step "Installing packages"
  if ! command -v iptables >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq iptables >/dev/null || die "could not install iptables"
  fi
  ok "iptables $(iptables -V 2>/dev/null | awk '{print $2}')"

  step "Checking ports"
  local m l
  for m in $udp; do
    l="${m%%:*}"
    ss -H -lnu "sport = :$l" 2>/dev/null | grep -q . && warn "UDP port $l is used by a local program; forwarded traffic takes priority over it"
  done
  # never take over the port we are logged in through
  local ssh_ports
  ssh_ports=" 22 $(ss -H -lntp 2>/dev/null | awk '/"sshd"/ {n = split($4, a, ":"); print a[n]}' | sort -u | tr '\n' ' ')"
  for m in $tcp; do
    l="${m%%:*}"
    case "$ssh_ports" in *" $l "*) die "refusing to forward TCP port $l - SSH listens there and you would lock yourself out" ;; esac
    ss -H -lnt "sport = :$l" 2>/dev/null | grep -q . && warn "TCP port $l is used by a local program (e.g. a web server); it will stop receiving outside connections"
  done
  ok "ports checked"

  step "Saving the configuration"
  mkdir -p "$CONF_DIR"
  cat > "$CONF" <<EOF
# Waze Panel relay configuration. Change it, then: waze-relay restart
TO="${to}"
UDP_MAP="${udp}"
TCP_MAP="${tcp}"
EOF
  chmod 600 "$CONF"
  cat > "$SYSCTL" <<'EOF'
# Waze Panel relay: forward packets, and keep enough connection-tracking
# room for many users on a busy relay.
net.ipv4.ip_forward = 1
net.netfilter.nf_conntrack_max = 262144
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
EOF
  modprobe nf_conntrack 2>/dev/null || true
  modprobe tcp_bbr 2>/dev/null || true
  sysctl -q -p "$SYSCTL" 2>/dev/null || sysctl -qw net.ipv4.ip_forward=1
  ok "saved to ${CONF}"

  step "Starting the relay"
  cat > "$UNIT" <<'EOF'
[Unit]
Description=Waze Panel relay (forwards OpenVPN to the main server)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/waze-relay apply
ExecReload=/usr/local/bin/waze-relay apply
ExecStop=/usr/local/bin/waze-relay stop

[Install]
WantedBy=multi-user.target
EOF
  if command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload 2>/dev/null; then
    systemctl enable waze-relay.service >/dev/null 2>&1 || true
    systemctl restart waze-relay.service || cmd_apply
  else
    cmd_apply
  fi
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    for m in $udp; do ufw allow "${m%%:*}/udp" >/dev/null; done
    for m in $tcp; do ufw allow "${m%%:*}/tcp" >/dev/null; done
    ok "opened the ports in ufw"
  fi
  ok "relay is running and starts again after a reboot"

  step "Testing the route to ${to}"
  cmd_test || true

  echo
  echo -e "  ${CG}${CB}Done.${C0} In the panel press 'check connection', then let users re-download"
  echo -e "  their config from the subscription link."
  echo -e "  Manage:  ${CB}waze-relay status${C0} | test | restart | uninstall"
  echo
}

cmd_test() {
  load_conf
  local ip m t start ms rc=0
  ip="$(resolve_to)"
  [ -n "$ip" ] || die "cannot resolve ${TO}"
  for m in $TCP_MAP; do
    t="${m##*:}"
    start=$(date +%s%N)
    if timeout 5 bash -c "exec 3<>/dev/tcp/${ip}/${t}" 2>/dev/null; then
      ms=$(( ($(date +%s%N) - start) / 1000000 ))
      ok "TCP ${ip}:${t} reachable (${ms} ms)"
    else
      warn "TCP ${ip}:${t} NOT reachable from this server"
      rc=1
    fi
  done
  if command -v ping >/dev/null 2>&1; then
    local loss
    loss="$(ping -c 5 -i 0.3 -W 2 "$ip" 2>/dev/null | grep -oE '[0-9.]+% packet loss' || true)"
    [ -n "$loss" ] && ok "ping ${ip}: ${loss}"
  fi
  [ -n "$UDP_MAP" ] && echo "  (UDP can't be probed from here; the panel's check covers the TCP path)"
  return $rc
}

cmd_status() {
  load_conf
  echo -e "${CB}Waze Panel relay${C0}"
  echo "  main server : ${TO} ($(cat "${CONF_DIR}/resolved" 2>/dev/null || resolve_to))"
  echo "  UDP         : ${UDP_MAP:-none}"
  echo "  TCP         : ${TCP_MAP:-none}"
  if command -v systemctl >/dev/null 2>&1; then
    echo "  service     : $(systemctl is-active waze-relay.service 2>/dev/null || echo unknown)"
  fi
  echo "  forwarding  : $(sysctl -n net.ipv4.ip_forward 2>/dev/null)"
  if ipt -t nat -S WZR_PRE >/dev/null 2>&1; then
    echo; echo "  packets/bytes forwarded:"
    ipt -t nat -L WZR_PRE -v -n -x 2>/dev/null | awk 'NR>2 {p = $4; if (p == "17") p = "udp"; if (p == "6") p = "tcp"; printf "    %-4s %-22s %s packets, %s bytes\n", p, $NF, $1, $2}'
    local conns
    conns="$(grep -c "dst=$(cat "${CONF_DIR}/resolved" 2>/dev/null)" /proc/net/nf_conntrack 2>/dev/null || true)"
    [ -n "$conns" ] && echo "    active connections: ${conns}"
  else
    echo -e "  ${CY}rules are not loaded${C0} (waze-relay restart)"
  fi
}

cmd_restart() {
  if command -v systemctl >/dev/null 2>&1 && systemctl restart waze-relay.service 2>/dev/null; then
    ok "restarted"
  else
    cmd_apply
  fi
}

cmd_uninstall() {
  need_root
  if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now waze-relay.service >/dev/null 2>&1 || true
  fi
  flush_rules
  rm -f "$UNIT" "$SYSCTL"
  rm -rf "$CONF_DIR"
  command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload 2>/dev/null || true
  rm -f /usr/local/bin/waze-relay
  ok "relay removed"
}

usage() {
  cat <<EOF
waze-relay - forward OpenVPN from this server to the main Waze Panel server

  waze-relay setup --to HOST --udp L[:T] --tcp L[:T]   configure and start
  waze-relay status      what is forwarded, counters, service state
  waze-relay test        check the route to the main server
  waze-relay restart     reload the rules (e.g. after editing ${CONF})
  waze-relay uninstall   remove everything
EOF
}

case "${1:-status}" in
  setup) shift; cmd_setup "$@" ;;
  run) shift; cmd_run "$@" ;;
  apply) cmd_apply ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  test) cmd_test ;;
  restart) cmd_restart ;;
  uninstall|remove) cmd_uninstall ;;
  -h|--help|help) usage ;;
  *) usage; exit 1 ;;
esac
WAZE_RELAY
chmod 755 /usr/local/bin/waze-relay

exec /usr/local/bin/waze-relay setup "$@"
