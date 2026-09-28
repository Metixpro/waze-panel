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
#   - revokes the certificate on delete (TLS-level rejection via the CRL)
#   - keeps a CRL that won't expire any time soon
# Everything it creates is removed again on exit.
#
# Usage: sudo bash tests/e2e.sh        (needs root, /dev/net/tun, ip, openvpn)
#        KEEP=1 sudo bash tests/e2e.sh   keep client configs/logs for debugging
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
  bash -c "! grep -q '<cert>' '$WORK/shared-udp.ovpn' && grep -q '^auth-user-pass' '$WORK/shared-udp.ovpn' && grep -q '<tls-crypt>' '$WORK/shared-udp.ovpn'"

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
check "without relays the config is direct-only again" bash -c "[ \"\$(curl -s --noproxy '*' -b '$COOKIES' '$PANEL/api/users/$USER_ID/config/udp' | grep -c '^remote ')\" = 1 ]"
}
if [ "$(api "$PANEL/api/relays" | jget "['relays'].__len__()")" = "0" ]; then
  relay_tests
else
  echo "  (skipped: this server already has relay servers configured)"
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

echo; echo "== revocation =="
cp "$WORK/udp.ovpn" "$WORK/old.ovpn"
log_lines="$(wc -l < /var/log/openvpn/udp.log)"
api -X DELETE "$PANEL/api/users/$USER_ID" >/dev/null; USER_ID=""
: > "$WORK/client-old.log"
ip netns exec "$NS" openvpn --config "$WORK/old.ovpn" --route-nopull --dev "tunold$$" \
  --daemon --writepid "$WORK/old.pid" --log "$WORK/client-old.log" --verb 3
sleep 12
check "deleted user's certificate can't connect" bash -c "! grep -q 'Initialization Sequence Completed' '$WORK/client-old.log'"
check "server rejected it as a revoked certificate" bash -c "tail -n +$((log_lines + 1)) /var/log/openvpn/udp.log | grep -q 'certificate revoked'"
next="$(openssl crl -in "$SERVER_DIR/crl.pem" -noout -nextupdate | cut -d= -f2)"
check "CRL stays valid for more than a year ($next)" [ "$(date -d "$next" +%s)" -gt "$(( $(date +%s) + 365*86400 ))" ]

echo
if [ "$FAIL" -eq 0 ]; then green "All $PASS checks passed."; else red "$FAIL of $((PASS + FAIL)) checks failed."; fi
[ "$FAIL" -eq 0 ]
