#!/usr/bin/env bash
#
# End-to-end test against a real, running Waze Panel install.
#
# Creates a throwaway admin + VPN user, then connects a *real* OpenVPN
# client from an isolated network namespace over UDP and TCP, pushes
# traffic through the encrypted tunnel, and checks that the panel:
#   - lets the client in (client-connect hook running as nobody:nogroup)
#   - sees it online and accounts its traffic (management interface)
#   - doesn't double count when the session ends (client-disconnect hook)
#   - cuts it off mid-session when it goes over quota, and refuses reconnects
#   - handles username/password logins: password-only users on the shared
#     certificate-less profile, certificate+password users, wrong passwords,
#     impersonation attempts, device limits and password changes
#   - routes clients through a relay server (relay.sh, in its own namespace)
#     and falls back to the direct address when the relay is down
#   - gives every user a personal tls-crypt-v2 key and retires a leaked one
#     without touching anyone else ("new key")
#   - forwards extra ports to each instance, and clients move on to the next
#     port by themselves when their network blocks one
#   - serves the same user over Xray: a VLESS/VMess/Trojan/Shadowsocks link
#     per inbound that really carries traffic, counted into the same quota,
#     shown online, in the subscription, cut off when disabled or out of
#     data, and replaced by "new links"
#   - revokes the certificate on delete (TLS-level rejection via the CRL)
#   - keeps a CRL that won't expire any time soon
# With RESTARTS=1 it also switches the key mode (shared/compat/per_user) and
# the HTTPS cover on and off; both restart OpenVPN, so every connected user
# reconnects once. The original settings are put back at the end.
# Everything it creates is removed again on exit.
#
# Usage: sudo bash tests/e2e.sh        (needs root, /dev/net/tun, ip, openvpn)
#        KEEP=1 sudo bash tests/e2e.sh   keep client configs/logs for debugging
#        RESTARTS=1 sudo bash tests/e2e.sh   include the tests that restart OpenVPN
#
set -uo pipefail

ENV_FILE=/etc/waze-panel/panel.env
APP_DIR=/opt/waze-panel
NS="wazetest$$"
VETH_H="wzh$$"; VETH_C="wzc$$"
HOST_IP=192.168.231.1; NS_IP=192.168.231.2
# relay namespace: client <-> relay on 10.231.10.0/24, relay <-> host on 10.231.11.0/24
RNS="wazerelay$$"
RELAY_IP=10.231.10.2; RELAY_OUT_IP=10.231.11.2; RELAY_HOST_IP=10.231.11.1
TPORT=18765
TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
XRAY_BIN=/usr/local/share/waze-panel/xray/xray
XIDS=()
WORK="$(mktemp -d)"
PASS=0; FAIL=0

green() { echo -e "\033[1;32m$*\033[0m"; }
red()   { echo -e "\033[1;31m$*\033[0m"; }
check() {  # check "description" <command...>
  local desc="$1"; shift
  if "$@"; then PASS=$((PASS + 1)); green "  PASS  $desc"; else FAIL=$((FAIL + 1)); red "  FAIL  $desc"; fi
}

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 2; }
[ -f "$ENV_FILE" ] || { echo "Waze Panel is not installed"; exit 2; }
for bin in ip openvpn curl python3; do command -v "$bin" >/dev/null || { echo "missing: $bin"; exit 2; }; done

env_get() { sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}$/\1/p" "$ENV_FILE" | tail -n1; }
PANEL="http://127.0.0.1:$(env_get PANEL_PORT)"
POLL="$(env_get TRAFFIC_POLL_INTERVAL_SECONDS)"; POLL="${POLL:-20}"
SERVER_DIR="$(env_get OPENVPN_SERVER_DIR)"
ADMIN="e2e_$(tr -dc a-z0-9 </dev/urandom | head -c 6 || true)"
ADMIN_PW="$(tr -dc A-Za-z0-9 </dev/urandom | head -c 20 || true)"
VUSER="e2e$(tr -dc a-z0-9 </dev/urandom | head -c 6 || true)"
COOKIES="$WORK/cookies"
USER_ID=""
EXTRA_IDS=()

cli() { (cd "$APP_DIR" && ./venv/bin/python -m app.cli "$@"); }
api() { curl -s --noproxy '*' -b "$COOKIES" "$@"; }
jget() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)"; }
usage_of() { api "$PANEL/api/users/$USER_ID" | jget "['data_used_bytes']"; }
online_of() { api "$PANEL/api/users/$USER_ID" | jget "['online']"; }

cleanup() {
  for pidf in "$WORK"/*.pid; do [ -f "$pidf" ] && kill "$(cat "$pidf")" 2>/dev/null; done
  [ -n "${HTTP_PID:-}" ] && kill "$HTTP_PID" 2>/dev/null
  iptables -D INPUT -i tun+ -p tcp --dport "$TPORT" -j ACCEPT 2>/dev/null
  ip route del 10.231.10.0/24 via "$RELAY_OUT_IP" 2>/dev/null
  ip netns del "$RNS" 2>/dev/null
  ip netns del "$NS" 2>/dev/null
  ip link del "$VETH_H" 2>/dev/null
  ip link del "wzk$$" 2>/dev/null
  [ -n "${RELAY_ID:-}" ] && api -X DELETE "$PANEL/api/relays/$RELAY_ID" >/dev/null 2>&1
  [ -n "${ORIG_PORTS:-}" ] && api -H 'Content-Type: application/json' -d "$ORIG_PORTS" "$PANEL/api/settings/connection/ports" >/dev/null 2>&1
  [ -n "${ORIG_COVER:-}" ] && api -H 'Content-Type: application/json' -d "{\"enabled\":$ORIG_COVER}" "$PANEL/api/settings/connection/cover" >/dev/null 2>&1
  [ -n "${ORIG_MODE:-}" ] && api -H 'Content-Type: application/json' -d "{\"mode\":\"$ORIG_MODE\"}" "$PANEL/api/settings/connection/keys" >/dev/null 2>&1
  for id in "${XIDS[@]}"; do api -X DELETE "$PANEL/api/xray/inbounds/$id" >/dev/null 2>&1; done
  rm -f /etc/waze-panel/xray/certs/e2e.example.com-self.*
  [ -n "${XDEST_ADDED:-}" ] && ip addr del "$XDEST/32" dev lo 2>/dev/null
  [ -n "$USER_ID" ] && api -X DELETE "$PANEL/api/users/$USER_ID" >/dev/null 2>&1
  for id in "${EXTRA_IDS[@]}"; do api -X DELETE "$PANEL/api/users/$id" >/dev/null 2>&1; done
  cli delete-admin --username "$ADMIN" >/dev/null 2>&1
  if [ "${KEEP:-0}" = "1" ]; then echo "logs kept in $WORK"; else rm -rf "$WORK"; fi
}
trap cleanup EXIT

# --- network namespace for the client + a traffic source on the host ---------
ip netns add "$NS"
ip link add "$VETH_H" type veth peer name "$VETH_C"
ip link set "$VETH_C" netns "$NS"
ip addr add "$HOST_IP/24" dev "$VETH_H"; ip link set "$VETH_H" up
ip netns exec "$NS" ip addr add "$NS_IP/24" dev "$VETH_C"
ip netns exec "$NS" ip link set "$VETH_C" up
ip netns exec "$NS" ip link set lo up

head -c 4000000 /dev/urandom > "$WORK/blob"
(cd "$WORK" && exec python3 -m http.server "$TPORT" --bind 0.0.0.0 >/dev/null 2>&1) &
HTTP_PID=$!
iptables -I INPUT -i tun+ -p tcp --dport "$TPORT" -j ACCEPT 2>/dev/null

# --- throwaway admin + VPN user ----------------------------------------------
cli create-admin --username "$ADMIN" --password "$ADMIN_PW" >/dev/null
curl -s --noproxy '*' -o /dev/null -c "$COOKIES" -d "username=$ADMIN&password=$ADMIN_PW" "$PANEL/login"
USER_ID="$(api -H 'Content-Type: application/json' -d "{\"username\":\"$VUSER\",\"data_limit_gb\":1}" "$PANEL/api/users" | jget "['id']")"
echo "test user: $VUSER (id $USER_ID), poll interval ${POLL}s"
[ -n "$USER_ID" ] || { red "could not create a user through the API"; exit 1; }

echo; echo "== about page =="
VERSION="$(cli version | awk '{print $3}')"
code_of() { curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "$@"; }
check "About page opens for the admin" [ "$(code_of -b "$COOKIES" "$PANEL/about")" = 200 ]
check "...and reports the running version ($VERSION)" [ "$(api "$PANEL/api/about" | jget "['version']")" = "$VERSION" ]
check "...but not without a login" [ "$(code_of "$PANEL/api/about")" = 401 ]

start_client() {  # start_client proto -> waits for the tunnel; returns 0 if up
  local proto="$1" cfg="$WORK/$1.ovpn" log="$WORK/client-$1.log"
  api "$PANEL/api/users/$USER_ID/config/$proto" | sed "s/^remote .* \([0-9]*\)$/remote $HOST_IP \1/" > "$cfg"
  : > "$log"
  ip netns exec "$NS" openvpn --config "$cfg" --route-nopull --dev "tun$proto$$" \
    --daemon --writepid "$WORK/$proto.pid" --log "$log" --verb 3
  for _ in $(seq 1 25); do
    grep -q "Initialization Sequence Completed" "$log" 2>/dev/null && return 0
    grep -qE "AUTH_FAILED|Halt command was pushed" "$log" 2>/dev/null && return 1
    sleep 1
  done
  return 1
}
stop_client() {
  local pidf="$WORK/$1.pid"
  [ -f "$pidf" ] && kill "$(cat "$pidf")" 2>/dev/null
  sleep 2; rm -f "$pidf"
}
pull_traffic() {  # pull_traffic <server tun ip> <times>
  ip netns exec "$NS" python3 - "$1" "$2" "$TPORT" <<'EOF'
import sys, urllib.request
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(int(sys.argv[2])):
    op.open(f"http://{sys.argv[1]}:{sys.argv[3]}/blob", timeout=20).read()
EOF
}
ge() { [ "$1" -ge "$2" ]; }
wait_offline() {  # the server waits ~5s after a client's exit notification
  for _ in $(seq 1 $((POLL + 10))); do
    [ "$(online_of)" = "False" ] && return 0
    sleep 1
  done
  return 1
}
client_running() { [ -f "$WORK/$1.pid" ] && kill -0 "$(cat "$WORK/$1.pid")" 2>/dev/null; }

# --- helpers for the login tests ---------------------------------------------
field_of() { api "$PANEL/api/users/$1" | jget "['$2']"; }   # field_of <id> <field>
local_cfg() { sed "s/^remote .* \([0-9]*\)$/remote $HOST_IP \1/"; }
new_user() {  # new_user <json> -> sets NEW_ID
  NEW_ID="$(api -H 'Content-Type: application/json' -d "$1" "$PANEL/api/users" | jget "['id']")"
  EXTRA_IDS+=("$NEW_ID")
}
run_cfg() {  # run_cfg <name> <config> [user] [password] -> 0 once the tunnel is up
  local name="$1" cfg="$2" log="$WORK/client-$1.log" auth=()
  if [ -n "${3:-}" ]; then printf '%s\n%s\n' "$3" "${4:-}" > "$WORK/$name.auth"; auth=(--auth-user-pass "$WORK/$name.auth"); fi
  : > "$log"
  ip netns exec "$NS" openvpn --config "$cfg" --route-nopull --dev "tun$name$$" \
    --daemon --writepid "$WORK/$name.pid" --log "$log" --verb 3 "${auth[@]}"
  for _ in $(seq 1 25); do
    grep -q "Initialization Sequence Completed" "$log" 2>/dev/null && return 0
    grep -qE "AUTH_FAILED|Halt command was pushed|Exiting due to fatal error" "$log" 2>/dev/null && return 1
    sleep 1
  done
  return 1
}
refused() { run_cfg "$@"; local rc=$?; stop_client "$1"; [ "$rc" -ne 0 ]; }
said() { grep -aq "$2" "$WORK/client-$1.log"; }   # said <client> <text of the AUTH_FAILED reason>
wait_online() {  # wait_online <id> True|False
  for _ in $(seq 1 $((POLL + 12))); do
    [ "$(field_of "$1" online)" = "$2" ] && return 0
    sleep 1
  done
  return 1
}
wait_stopped() { for _ in $(seq 1 15); do client_running "$1" || return 0; sleep 1; done; return 1; }

echo; echo "== UDP instance =="
check "client connects over UDP (hook allows it)" start_client udp
before="$(usage_of)"
pull_traffic 10.8.0.1 2
sleep $((POLL + 3))
check "panel shows the user online" [ "$(online_of)" = "True" ]
after="$(usage_of)"
check "~8 MB through the tunnel is accounted ($((after - before)) bytes)" ge $((after - before)) 8000000
stop_client udp
check "user goes offline right after disconnecting" wait_offline
settled="$(usage_of)"
sleep $((POLL + 3))
check "no double counting after the session ends" [ "$(usage_of)" = "$settled" ]

echo; echo "== TCP instance =="
check "client connects over TCP" start_client tcp
before="$(usage_of)"
pull_traffic 10.9.0.1 1
sleep $((POLL + 3))
after="$(usage_of)"
check "~4 MB over TCP is accounted ($((after - before)) bytes)" ge $((after - before)) 4000000
stop_client tcp

echo; echo "== username / password logins =="
PW="Pw-$(tr -dc A-Za-z0-9 </dev/urandom | head -c 10 || true)"
PUSER="e2ep$(tr -dc a-z0-9 </dev/urandom | head -c 5 || true)"
CPUSER="e2ec$(tr -dc a-z0-9 </dev/urandom | head -c 5 || true)"
new_user "{\"username\":\"$PUSER\",\"auth_mode\":\"pass\",\"password\":\"$PW\"}"; P_ID="$NEW_ID"
new_user "{\"username\":\"$CPUSER\",\"auth_mode\":\"cert_pass\"}"; CP_ID="$NEW_ID"
CP_PW="$(field_of "$CP_ID" password)"
VUSER_PW="$(field_of "$USER_ID" password)"
check "password-only user keeps the password it was given" [ "$(field_of "$P_ID" password)" = "$PW" ]
check "certificate+password user gets a generated password" [ "${#CP_PW}" -ge 8 ]

api "$PANEL/api/settings/shared-config/udp" | local_cfg > "$WORK/shared-udp.ovpn"
api "$PANEL/api/settings/shared-config/tcp" | local_cfg > "$WORK/shared-tcp.ovpn"
check "shared profile has no client certificate and asks for a login" \
  bash -c "! grep -q '<cert>' '$WORK/shared-udp.ovpn' && grep -q '^auth-user-pass' '$WORK/shared-udp.ovpn' && grep -q '<tls-crypt' '$WORK/shared-udp.ovpn'"

check "password-only user connects with the shared profile (UDP)" run_cfg pw "$WORK/shared-udp.ovpn" "$PUSER" "$PW"
check "...and shows up online" wait_online "$P_ID" True
before="$(field_of "$P_ID" data_used_bytes)"
pull_traffic 10.8.0.1 1
sleep $((POLL + 3))
after="$(field_of "$P_ID" data_used_bytes)"
check "...and its traffic is accounted to it ($((after - before)) bytes)" ge $((after - before)) 4000000
stop_client pw
check "...goes offline after disconnecting" wait_online "$P_ID" False
settled="$(field_of "$P_ID" data_used_bytes)"; sleep $((POLL + 2))
check "...without double counting" [ "$(field_of "$P_ID" data_used_bytes)" = "$settled" ]
check "username is case-insensitive (phone keyboards), over TCP" run_cfg pwc "$WORK/shared-tcp.ovpn" "${PUSER^}" "$PW"
stop_client pwc

check "wrong password is refused" refused bad "$WORK/shared-udp.ovpn" "$PUSER" "wrong-$PW"
check "...and the app is told why" said bad "Wrong username or password"
check "a certificate user can't log in with just its password" refused nocert "$WORK/shared-udp.ovpn" "$VUSER" "$VUSER_PW"
check "...and is told to use its own config" said nocert "own config"

api "$PANEL/api/users/$CP_ID/config/udp" | local_cfg > "$WORK/cp.ovpn"
check "certificate+password profile asks for a login" grep -q '^auth-user-pass' "$WORK/cp.ovpn"
check "certificate+password user connects with both" run_cfg cp "$WORK/cp.ovpn" "$CPUSER" "$CP_PW"
stop_client cp
grep -v '^auth-user-pass' "$WORK/cp.ovpn" > "$WORK/cp-nopass.ovpn"
check "...but not with the certificate alone" refused cpn "$WORK/cp-nopass.ovpn"
check "...nor with the certificate and a wrong password" refused cpw "$WORK/cp.ovpn" "$CPUSER" "nope-$CP_PW"
check "...nor with the password alone (shared profile)" refused cps "$WORK/shared-udp.ovpn" "$CPUSER" "$CP_PW"
check "another user's certificate can't log in as someone else" refused imp "$WORK/cp.ovpn" "$PUSER" "$PW"
check "a certificate-only user sending someone's login stays itself" run_cfg imp2 "$WORK/udp.ovpn" "$PUSER" "$PW"
sleep $((POLL + 3))
check "...(the login is ignored: $PUSER stays offline)" [ "$(field_of "$P_ID" online)" = "False" ]
stop_client imp2

echo; echo "== device limit & password change =="
api -X PATCH -H 'Content-Type: application/json' -d '{"max_devices":1}' "$PANEL/api/users/$P_ID" >/dev/null
check "first device connects" run_cfg d1 "$WORK/shared-udp.ovpn" "$PUSER" "$PW"
sleep 2
check "second device connects too (newest wins)" run_cfg d2 "$WORK/shared-tcp.ovpn" "$PUSER" "$PW"
check "...and the first one is disconnected" wait_stopped d1
check "...while the second keeps running" client_running d2
api -X PATCH -H 'Content-Type: application/json' -d '{"regenerate_password":true}' "$PANEL/api/users/$P_ID" >/dev/null
check "password change cuts the open session right away" wait_stopped d2
stop_client d1; stop_client d2
check "...the old password no longer works" refused old "$WORK/shared-udp.ovpn" "$PUSER" "$PW"
check "...the new one does" run_cfg new "$WORK/shared-udp.ovpn" "$PUSER" "$(field_of "$P_ID" password)"
stop_client new

echo; echo "== relay server (Iran tunnel) =="
relay_tests() {
UDP_PORT="$(env_get OVPN_UDP_PORT)"; TCP_PORT="$(env_get OVPN_TCP_PORT)"; SERVER_ADDR="$(env_get SERVER_ADDRESS)"
ip netns add "$RNS"
ip link add "wzq$$" type veth peer name "wzr$$"        # client <-> relay
ip link set "wzq$$" netns "$NS"; ip link set "wzr$$" netns "$RNS"
ip link add "wzk$$" type veth peer name "wzo$$"        # host <-> relay
ip link set "wzo$$" netns "$RNS"
ip netns exec "$NS" ip addr add 10.231.10.1/24 dev "wzq$$"; ip netns exec "$NS" ip link set "wzq$$" up
ip netns exec "$RNS" ip addr add "$RELAY_IP/24" dev "wzr$$"; ip netns exec "$RNS" ip link set "wzr$$" up
ip netns exec "$RNS" ip addr add "$RELAY_OUT_IP/24" dev "wzo$$"; ip netns exec "$RNS" ip link set "wzo$$" up
ip netns exec "$RNS" ip link set lo up
ip addr add "$RELAY_HOST_IP/24" dev "wzk$$"; ip link set "wzk$$" up
ip route add 10.231.10.0/24 via "$RELAY_OUT_IP"   # so the panel's health check can reach the relay
# the relay helper, straight out of relay.sh
sed -n "/^cat > \/usr\/local\/bin\/waze-relay <<'WAZE_RELAY'$/,/^WAZE_RELAY$/p" "$(dirname "$0")/../relay.sh" | sed '1d;$d' > "$WORK/waze-relay"
chmod +x "$WORK/waze-relay"
relay_on()  { ip netns exec "$RNS" "$WORK/waze-relay" run --to "$RELAY_HOST_IP" --udp "$UDP_PORT" --tcp "$TCP_PORT" >/dev/null; }
relay_off() { ip netns exec "$RNS" "$WORK/waze-relay" stop >/dev/null; }
relay_cfg() {  # relay_cfg proto out: the user's config, direct address pointed at this host
  api "$PANEL/api/users/$USER_ID/config/$1" | sed "s/^remote $SERVER_ADDR /remote $HOST_IP /" > "$2"
}
check "relay.sh forwarding rules load" relay_on

RELAY_ID="$(api -H 'Content-Type: application/json' -d "{\"name\":\"e2e-relay\",\"address\":\"$RELAY_IP\"}" "$PANEL/api/relays" | jget "['id']")"
relay_cfg udp "$WORK/relay-udp.ovpn"
check "config lists the relay first, then the direct address" \
  bash -c "grep '^remote ' '$WORK/relay-udp.ovpn' | head -1 | grep -q '^remote $RELAY_IP $UDP_PORT' && grep '^remote ' '$WORK/relay-udp.ovpn' | sed -n 2p | grep -q '^remote $HOST_IP ' && grep -q '^server-poll-timeout' '$WORK/relay-udp.ovpn'"
check "panel health check sees traffic loop back through the relay" \
  bash -c "curl -s --noproxy '*' -b '$COOKIES' -X POST '$PANEL/api/relays/check' | python3 -c 'import sys,json; sys.exit(0 if json.load(sys.stdin)[\"$RELAY_ID\"][\"ok\"] else 1)'"
check "client connects through the relay (UDP)" run_cfg rly "$WORK/relay-udp.ovpn"
sleep $((POLL + 3))
check "...the server sees it arriving from the relay" [ "$(field_of "$USER_ID" last_ip)" = "$RELAY_OUT_IP" ]
check "...and the panel names the relay" [ "$(field_of "$USER_ID" last_via)" = "e2e-relay" ]
stop_client rly
relay_cfg tcp "$WORK/relay-tcp.ovpn"
check "client connects through the relay (TCP)" run_cfg rlt "$WORK/relay-tcp.ovpn"
stop_client rlt

relay_off
check "health check notices the relay is down" \
  bash -c "curl -s --noproxy '*' -b '$COOKIES' -X POST '$PANEL/api/relays/check' | python3 -c 'import sys,json; sys.exit(1 if json.load(sys.stdin)[\"$RELAY_ID\"][\"ok\"] else 0)'"
started=$(date +%s)
check "with the relay down, the same config falls back to the direct address" run_cfg rlf "$WORK/relay-udp.ovpn"
echo "        (failover took $(( $(date +%s) - started ))s)"
sleep $((POLL + 3))
check "...(connected directly)" [ "$(field_of "$USER_ID" last_ip)" = "$NS_IP" ]
stop_client rlf
relay_on

api -H 'Content-Type: application/json' -d '{"fallback_direct":false,"balance":false,"timeout":8}' "$PANEL/api/relays/options" >/dev/null
check "direct fallback can be switched off" bash -c "[ \"\$(curl -s --noproxy '*' -b '$COOKIES' '$PANEL/api/users/$USER_ID/config/udp' | grep -c '^remote ')\" = 1 ]"
api -H 'Content-Type: application/json' -d '{"fallback_direct":true,"balance":false,"timeout":8}' "$PANEL/api/relays/options" >/dev/null
api -X DELETE "$PANEL/api/relays/$RELAY_ID" >/dev/null; RELAY_ID=""
DIRECT_REMOTES=$((1 + $(api "$PANEL/api/settings/connection" | jget "['ports']['udp']['extra'].__len__()")))   # + extra ports
check "without relays the config is direct-only again" bash -c "[ \"\$(curl -s --noproxy '*' -b '$COOKIES' '$PANEL/api/users/$USER_ID/config/udp' | grep -c '^remote ')\" = $DIRECT_REMOTES ]"
}
if [ "$(api "$PANEL/api/relays" | jget "['relays'].__len__()")" = "0" ]; then
  relay_tests
else
  echo "  (skipped: this server already has relay servers configured)"
fi

echo; echo "== personal keys (tls-crypt-v2) =="
conn() { api "$PANEL/api/settings/connection" | jget "$1"; }
set_mode() { api -H 'Content-Type: application/json' -d "{\"mode\":\"$1\"}" "$PANEL/api/settings/connection/keys" | jget "['keys']['mode']"; }
key_of() { sed -n '/<tls-crypt-v2>/,/<\/tls-crypt-v2>/p' "$1" | md5sum | cut -d' ' -f1; }
MODE="$(conn "['keys']['mode']")"
if [ "${RESTARTS:-0}" = "1" ] && [ "$(conn "['keys']['supported']")" = "True" ]; then
  ORIG_MODE="$MODE"
  check "key mode switches to shared (both instances restart)" [ "$(set_mode shared)" = "shared" ]
  api "$PANEL/api/users/$USER_ID/config/udp" | local_cfg > "$WORK/legacy.ovpn"
  check "...configs then carry the shared tls-crypt key" grep -q '<tls-crypt>' "$WORK/legacy.ovpn"
  check "...and connect" run_cfg lg "$WORK/legacy.ovpn"
  stop_client lg
  check "compat mode: personal keys for new downloads" [ "$(set_mode compat)" = "compat" ]
  check "...while the old shared-key config still connects" run_cfg lg2 "$WORK/legacy.ovpn"
  stop_client lg2
  MODE=compat
fi
if [ "$MODE" != "shared" ]; then
  api "$PANEL/api/users/$USER_ID/config/udp" | local_cfg > "$WORK/k1.ovpn"
  api "$PANEL/api/users/$USER_ID/config/tcp" | local_cfg > "$WORK/k1t.ovpn"
  api "$PANEL/api/users/$CP_ID/config/udp" | local_cfg > "$WORK/kcp.ovpn"
  check "config carries the user's own tls-crypt-v2 key" bash -c "grep -q '<tls-crypt-v2>' '$WORK/k1.ovpn' && ! grep -q '<tls-crypt>' '$WORK/k1.ovpn'"
  check "...the same one in the UDP and the TCP file" [ "$(key_of "$WORK/k1.ovpn")" = "$(key_of "$WORK/k1t.ovpn")" ]
  check "...and a different one than another user's" [ "$(key_of "$WORK/k1.ovpn")" != "$(key_of "$WORK/kcp.ovpn")" ]
  check "client connects with its personal key (UDP)" run_cfg k1 "$WORK/k1.ovpn"
  stop_client k1
  check "...and over TCP" run_cfg k1t "$WORK/k1t.ovpn"
  stop_client k1t
  check "...and the panel sees the key in use" [ "$(field_of "$USER_ID" tls_key_seen_at)" != "None" ]
  api -X POST "$PANEL/api/users/$USER_ID/regenerate_key" >/dev/null
  check "after «new key» the leaked file no longer connects" refused k1old "$WORK/k1.ovpn"
  check "...other users are untouched" run_cfg kcp "$WORK/kcp.ovpn" "$CPUSER" "$(field_of "$CP_ID" password)"
  stop_client kcp
  api "$PANEL/api/users/$USER_ID/config/udp" | local_cfg > "$WORK/k2.ovpn"
  check "...and the user's new download connects" run_cfg k2 "$WORK/k2.ovpn"
  stop_client k2
  api "$PANEL/api/settings/shared-config/udp" | local_cfg > "$WORK/sh1.ovpn"
  check "the shared password-only profile has a key of its own" \
    bash -c "grep -q '<tls-crypt-v2>' '$WORK/sh1.ovpn' && [ \"\$(sed -n '/<tls-crypt-v2>/,/<\/tls-crypt-v2>/p' '$WORK/sh1.ovpn' | md5sum)\" != \"\$(sed -n '/<tls-crypt-v2>/,/<\/tls-crypt-v2>/p' '$WORK/k2.ovpn' | md5sum)\" ]"
  P_PW="$(field_of "$P_ID" password)"
  check "...which connects with a login" run_cfg sh1 "$WORK/sh1.ovpn" "$PUSER" "$P_PW"
  stop_client sh1
  api -X POST "$PANEL/api/settings/shared-key" >/dev/null
  check "...and a new shared key retires old copies of that file" refused sh1old "$WORK/sh1.ovpn" "$PUSER" "$P_PW"
  api "$PANEL/api/settings/shared-config/udp" | local_cfg > "$WORK/sh2.ovpn"
  check "...while the new one works" run_cfg sh2 "$WORK/sh2.ovpn" "$PUSER" "$P_PW"
  stop_client sh2
  if [ "${RESTARTS:-0}" = "1" ]; then
    check "strict mode: personal keys only" [ "$(set_mode per_user)" = "per_user" ]
    check "...the old shared-key config is refused" refused lg3 "$WORK/legacy.ovpn"
    check "...personal-key configs keep working" run_cfg k3 "$WORK/k2.ovpn"
    stop_client k3
  fi
else
  echo "  (skipped: personal keys are off; RESTARTS=1 switches them on for the test)"
fi

echo; echo "== extra ports =="
UDP_PORT="$(env_get OVPN_UDP_PORT)"; TCP_PORT="$(env_get OVPN_TCP_PORT)"
ORIG_PORTS="$(api "$PANEL/api/settings/connection" | python3 -c "import sys,json; p=json.load(sys.stdin)['ports']; print(json.dumps({'udp':p['udp']['extra'],'tcp':p['tcp']['extra']}))")"
set_ports() { api -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d "{\"udp\":[$1],\"tcp\":[$2]}" "$PANEL/api/settings/connection/ports"; }
EU=21194; ET=20443
check "a port the panel itself uses is refused" [ "$(set_ports "" "$(env_get PANEL_PORT)")" = 400 ]
check "...and so is the main port" [ "$(set_ports "$UDP_PORT" "")" = 400 ]
check "extra ports are saved" [ "$(set_ports "$EU" "$ET")" = 200 ]
check "...and redirected to the real ports by iptables" bash -c \
  "iptables -t nat -C WAZE_PORTS -p udp --dport $EU -j REDIRECT --to-ports $UDP_PORT && iptables -t nat -C WAZE_PORTS -p tcp --dport $ET -j REDIRECT --to-ports $TCP_PORT"
api "$PANEL/api/users/$USER_ID/config/udp" | local_cfg > "$WORK/ep-udp.ovpn"
api "$PANEL/api/users/$USER_ID/config/tcp" | local_cfg > "$WORK/ep-tcp.ovpn"
check "configs try the main port first, then the extra one" bash -c \
  "grep '^remote ' '$WORK/ep-udp.ovpn' | awk '{print \$3}' | tr '\n' ' ' | grep -q '^$UDP_PORT $EU ' && grep -q '^server-poll-timeout' '$WORK/ep-udp.ovpn'"
ip netns exec "$NS" iptables -I OUTPUT -p udp --dport "$UDP_PORT" -j DROP
started=$(date +%s)
check "main UDP port blocked on the client's network: it moves on to the extra port" run_cfg epu "$WORK/ep-udp.ovpn"
echo "        (took $(( $(date +%s) - started ))s)"
ip netns exec "$NS" iptables -D OUTPUT -p udp --dport "$UDP_PORT" -j DROP
stop_client epu
ip netns exec "$NS" iptables -I OUTPUT -p tcp --dport "$TCP_PORT" -j DROP
started=$(date +%s)
check "...the same over TCP" run_cfg ept "$WORK/ep-tcp.ovpn"
echo "        (took $(( $(date +%s) - started ))s)"
ip netns exec "$NS" iptables -D OUTPUT -p tcp --dport "$TCP_PORT" -j DROP
stop_client ept

if [ "${RESTARTS:-0}" = "1" ]; then
  echo; echo "== HTTPS cover =="
  ORIG_COVER="$(conn "['cover']['enabled']" | tr 'TF' 'tf')"
  cover() { api -H 'Content-Type: application/json' -d "{\"enabled\":$1}" "$PANEL/api/settings/connection/cover" | jget "['cover']['enabled']"; }
  fetch() { ip netns exec "$NS" curl -sk --noproxy '*' --max-time 6 "$@" 2>/dev/null; }
  page_has() { fetch "$1" | grep -q "$2"; }
  not() { ! "$@"; }
  check "cover switches on (TCP instance restarts)" [ "$(cover true)" = "True" ]
  check "a browser opening the TCP port gets a web page" page_has "https://$HOST_IP:$TCP_PORT/" 'Welcome to nginx'
  cert_cn() { echo | ip netns exec "$NS" timeout 6 openssl s_client -connect "$HOST_IP:$TCP_PORT" 2>/dev/null | sed -n 's/^subject=CN *= *//p'; }
  COVER_HOST="$(conn "['cover']['url']" | sed 's#https://##; s#:.*##')"
  check "...over TLS, with a certificate for this server ($COVER_HOST)" [ "$(cert_cn)" = "$COVER_HOST" ]
  check "...plain HTTP gets the usual web-server error" page_has "http://$HOST_IP:$TCP_PORT/" 'plain HTTP request was sent to HTTPS port'
  check "...unknown paths are a 404" [ "$(fetch -o /dev/null -w '%{http_code}' "https://$HOST_IP:$TCP_PORT/admin")" = 404 ]
  check "...the extra TCP port shows the same site" page_has "https://$HOST_IP:$ET/" 'Welcome to nginx'
  api "$PANEL/api/users/$USER_ID/config/tcp" | local_cfg > "$WORK/cv.ovpn"
  check "VPN clients still connect on the same port" run_cfg cv "$WORK/cv.ovpn"
  stop_client cv
  check "cover switches off again" [ "$(cover false)" = "False" ]
  check "...and the port stops answering browsers" not page_has "https://$HOST_IP:$TCP_PORT/" 'nginx'
fi
check "extra ports can be removed again" [ "$(set_ports "" "")" = 200 ]
check "...which removes the firewall chain" bash -c "! iptables -t nat -S WAZE_PORTS >/dev/null 2>&1"

# --- Xray ----------------------------------------------------------------------
not() { ! "$@"; }
eventually() { for _ in $(seq 1 12); do "$@" && return 0; sleep 1; done; return 1; }
noproxy() { env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY -u no_proxy -u NO_PROXY "$@"; }
# "<inbound id> <link>" for each of the user's Xray links
xlinks_of() { api "$PANEL/api/users/$1" | python3 -c 'import sys, json; [print(l["id"], l["link"]) for l in json.load(sys.stdin)["xray_links"]]'; }
xlink() { xlinks_of "$USER_ID" | awk -v id="$1" '$1 == id {print $2}'; }   # xlink <inbound id>
xclient() {  # xclient <link> <socks port>: an Xray client in the namespace
  python3 "$TESTS_DIR/xray_link2json.py" "$1" "$2" --address "$HOST_IP" > "$WORK/xc-$2.json" || return 1
  ip netns exec "$NS" env XRAY_LOCATION_ASSET="${XRAY_BIN%/*}" "$XRAY_BIN" run -c "$WORK/xc-$2.json" > "$WORK/xc-$2.log" 2>&1 &
  echo $! > "$WORK/xc-$2.pid"
  for _ in $(seq 1 25); do ip netns exec "$NS" ss -Hltn "sport = :$2" | grep -q . && return 0; sleep 0.2; done
  return 1
}
xclient_stop() { [ -f "$WORK/xc-$1.pid" ] && { kill "$(cat "$WORK/xc-$1.pid")" 2>/dev/null; rm -f "$WORK/xc-$1.pid"; }; }
xfetch() {  # xfetch <link>: 0 if the whole test file comes through it
  local port=$((31000 + RANDOM % 3000)) size
  xclient "$1" "$port" || { xclient_stop "$port"; return 1; }
  size="$(ip netns exec "$NS" env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
    curl -s -o /dev/null -w '%{size_download}' --max-time 15 --socks5-hostname "127.0.0.1:$port" "http://$XDEST:$TPORT/blob" || true)"
  xclient_stop "$port"
  [ "${size:-0}" = "$(stat -c %s "$WORK/blob")" ]
}
xadd() {  # xadd <json> -> the new inbound's id (empty if refused)
  api -H 'Content-Type: application/json' -d "$1" "$PANEL/api/xray/inbounds" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("id", ""))' 2>/dev/null
}
xadd_code() { api -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d "$1" "$PANEL/api/xray/inbounds"; }
has_xray_online() { api "$PANEL/api/users/$USER_ID" | python3 -c 'import sys, json; sys.exit("xray" not in json.load(sys.stdin)["online_protos"])'; }
tunnel_open() { ss -Htn state established "( sport = :$1 )" | grep -q "$NS_IP"; }   # tunnel_open <port>

xray_tests() {
  check "Xray core is running" [ "$(api "$PANEL/api/xray" | jget "['state']")" = running ]
  # Xray carries nobody to private or reserved addresses, so the test file
  # comes from a public address of this host (or one borrowed on lo for the test).
  XDEST="$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 \
    | python3 -c 'import sys, ipaddress; print(next((a for a in sys.stdin.read().split() if ipaddress.ip_address(a).is_global), ""))')"
  if [ -z "$XDEST" ]; then XDEST=11.22.33.44; ip addr add "$XDEST/32" dev lo 2>/dev/null && XDEST_ADDED=1; fi

  # REALITY imitates a real TLS 1.3 site; a local one keeps the test offline
  XTPORT=$((20000 + RANDOM % 5000))
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 2 -subj /CN=e2e.example.com \
    -keyout "$WORK/xt.key" -out "$WORK/xt.crt" >/dev/null 2>&1
  python3 - "$XTPORT" "$WORK/xt.crt" "$WORK/xt.key" <<'PY' &
import http.server, ssl, sys
class Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Page)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.minimum_version = ssl.TLSVersion.TLSv1_3
ctx.load_cert_chain(sys.argv[2], sys.argv[3]); ctx.set_alpn_protocols(["h2", "http/1.1"])
srv.socket = ctx.wrap_socket(srv.socket, server_side=True); srv.serve_forever()
PY
  echo $! > "$WORK/xtarget.pid"

  local xp=$((25000 + RANDOM % 5000)) real="\"sni\":\"e2e.example.com\",\"target\":\"127.0.0.1:$XTPORT\"" spec n pr tr se op id
  check "refuses a combination Xray doesn't have (VMess + Reality)" \
    [ "$(xadd_code "{\"name\":\"x\",\"protocol\":\"vmess\",\"transport\":\"ws\",\"security\":\"reality\",\"port\":$xp}")" = 400 ]
  check "refuses a port OpenVPN uses" \
    [ "$(xadd_code "{\"name\":\"x\",\"protocol\":\"vless\",\"transport\":\"ws\",\"security\":\"none\",\"port\":$(env_get OVPN_TCP_PORT)}")" = 400 ]
  local before; before="$(usage_of)"
  for spec in \
    "Reality|vless|raw|reality|{$real}" \
    "XHTTP|vless|xhttp|reality|{$real,\"path\":\"/e2ex\"}" \
    "WS|vless|ws|none|{\"path\":\"/e2ew\"}" \
    "VMess|vmess|ws|none|{\"path\":\"/e2ev\"}" \
    "Trojan|trojan|raw|tls|{\"sni\":\"e2e.example.com\"}" \
    "SS|shadowsocks|raw|none|{}"; do
    IFS='|' read -r n pr tr se op <<< "$spec"
    id="$(xadd "{\"name\":\"e2e-$n\",\"protocol\":\"$pr\",\"transport\":\"$tr\",\"security\":\"$se\",\"port\":$xp,\"options\":$op}")"
    check "creates a $pr + $tr + $se inbound (port $xp)" [ -n "$id" ]
    if [ -n "$id" ]; then
      XIDS+=("$id")
      eval "XID_$n=$id"
      check "...and its link carries traffic" eventually xfetch "$(xlink "$id")"
    fi
    xp=$((xp + 1))
  done
  [ -n "${XID_Reality:-}" ] || return

  check "shows the user online over Xray" eventually has_xray_online
  sleep $((POLL + 3))
  local after; after="$(usage_of)"
  check "traffic over Xray counts into the same quota ($((after - before)) bytes)" ge $((after - before)) $((${#XIDS[@]} * 4000000))

  local token body
  token="$(api "$PANEL/api/users/$USER_ID" | jget "['token']")"
  body="$(curl -s --noproxy '*' -D "$WORK/sub-headers" "$PANEL/sub/$token/xray")"
  check "subscription has every link (base64, for v2rayNG/Hiddify/...)" \
    [ "$(echo "$body" | base64 -d 2>/dev/null | grep -c '://')" -ge "${#XIDS[@]}" ]
  check "...with usage and limit in subscription-userinfo" grep -qi '^subscription-userinfo: upload=0; download=[1-9]' "$WORK/sub-headers"

  local link; link="$(xlink "$XID_Reality")"
  api -X PATCH -H 'Content-Type: application/json' -d '{"xray_enabled":false}' "$PANEL/api/users/$USER_ID" >/dev/null
  check "Xray switched off for the user: the link is refused" eventually not xfetch "$link"
  api -X PATCH -H 'Content-Type: application/json' -d '{"xray_enabled":true}' "$PANEL/api/users/$USER_ID" >/dev/null
  check "...and works again when switched back on" eventually xfetch "$link"

  # Disabling a user mid-download must cut the open connection too (Xray
  # itself would let it run, uncounted).
  head -c 64000000 /dev/zero > "$WORK/big"
  local xpt; xpt="$(api "$PANEL/api/xray" | python3 -c "import sys, json; print(next(i['port'] for i in json.load(sys.stdin)['inbounds'] if i['id'] == $XID_Reality))")"
  xclient "$link" 30999
  ip netns exec "$NS" env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
    curl -s -o /dev/null --limit-rate 300k --max-time 60 --socks5-hostname 127.0.0.1:30999 "http://$XDEST:$TPORT/big" &
  echo $! > "$WORK/xslow.pid"
  sleep $((POLL + 2))
  check "a long download is open over Xray" tunnel_open "$xpt"
  api -X POST "$PANEL/api/users/$USER_ID/toggle" >/dev/null
  check "disabling the user closes it mid-download" eventually not tunnel_open "$xpt"
  kill "$(cat "$WORK/xslow.pid")" 2>/dev/null; rm -f "$WORK/xslow.pid" "$WORK/big"; xclient_stop 30999
  api -X POST "$PANEL/api/users/$USER_ID/toggle" >/dev/null
  check "...and the user gets back in when enabled" eventually xfetch "$link"

  api -X POST "$PANEL/api/users/$USER_ID/regenerate_xray" >/dev/null
  check "'new links' retires the old link" eventually not xfetch "$link"
  check "...and the new one works" eventually xfetch "$(xlink "$XID_Reality")"

  api -X PATCH -H 'Content-Type: application/json' -d '{"enabled":false}' "$PANEL/api/xray/inbounds/$XID_WS" >/dev/null
  check "a switched-off inbound leaves the user's links" [ -z "$(xlink "$XID_WS")" ]
}

echo; echo "== xray =="
if [ -x "$XRAY_BIN" ]; then
  xray_tests
else
  echo "  Xray is not installed (waze-panel xray install): skipped"
fi

echo; echo "== quota enforcement =="
check "reconnects over UDP" start_client udp
limit_gb="$(python3 -c "print($(usage_of) / 2 / 1024**3)")"
log_lines="$(wc -l < /var/log/openvpn/udp.log)"
api -X PATCH -H 'Content-Type: application/json' -d "{\"data_limit_gb\":$limit_gb}" "$PANEL/api/users/$USER_ID" >/dev/null
halted=1
for _ in $(seq 1 $((POLL + 10))); do
  client_running udp || { halted=0; break; }
  sleep 1
done
check "over-quota session is cut off mid-session" [ "$halted" -eq 0 ]
check "...with HALT, so the client stops instead of retrying" bash -c "tail -n +$((log_lines + 1)) /var/log/openvpn/udp.log | grep -q \"'HALT'\""
stop_client udp
start_client udp; rc=$?
check "reconnect is refused while over quota" [ "$rc" -ne 0 ]
check "...and the client is told to stop (AUTH_FAILED/HALT)" grep -qE "AUTH_FAILED|Halt command was pushed" "$WORK/client-udp.log"
check "...with the reason (data limit reached)" said udp "Data limit reached"
stop_client udp
if [ -n "${XID_Reality:-}" ]; then
  check "...and Xray refuses them too" eventually not xfetch "$(xlink "$XID_Reality")"
fi

echo; echo "== revocation =="
cp "$WORK/udp.ovpn" "$WORK/old.ovpn"
log_lines="$(wc -l < /var/log/openvpn/udp.log)"
api -X DELETE "$PANEL/api/users/$USER_ID" >/dev/null; USER_ID=""
: > "$WORK/client-old.log"
ip netns exec "$NS" openvpn --config "$WORK/old.ovpn" --route-nopull --dev "tunold$$" \
  --daemon --writepid "$WORK/old.pid" --log "$WORK/client-old.log" --verb 3
sleep 12
check "deleted user's certificate can't connect" bash -c "! grep -q 'Initialization Sequence Completed' '$WORK/client-old.log'"
if grep -q '<tls-crypt-v2>' "$WORK/old.ovpn"; then
  check "server refused its personal key before the handshake" bash -c "tail -n +$((log_lines + 1)) /var/log/openvpn/udp.log | grep -qi 'tls-crypt-v2.*\(verify\|script\)\|TLS CRYPT V2 VERIFY'"
else
  check "server rejected it as a revoked certificate" bash -c "tail -n +$((log_lines + 1)) /var/log/openvpn/udp.log | grep -q 'certificate revoked'"
fi
next="$(openssl crl -in "$SERVER_DIR/crl.pem" -noout -nextupdate | cut -d= -f2)"
check "CRL stays valid for more than a year ($next)" [ "$(date -d "$next" +%s)" -gt "$(( $(date +%s) + 365*86400 ))" ]

echo
if [ "$FAIL" -eq 0 ]; then green "All $PASS checks passed."; else red "$FAIL of $((PASS + FAIL)) checks failed."; fi
[ "$FAIL" -eq 0 ]
