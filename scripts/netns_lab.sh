#!/usr/bin/env bash
# Optional local Linux network-namespace test. No cloud services or host route changes.
set -euo pipefail
if [[ "$EUID" -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/netns_lab.sh" >&2
  exit 1
fi
for cmd in ip python3; do command -v "$cmd" >/dev/null || { echo "Missing: $cmd" >&2; exit 1; }; done
REPO="$(cd "$(dirname "$0")/.." && pwd)"
NS_A="ril-a-$$"
NS_B="ril-b-$$"
VETH_A="veth-ril-a"
VETH_B="veth-ril-b"
OUT="$REPO/lab-output"
SERVER_PID=""
cleanup() {
  [[ -z "$SERVER_PID" ]] || kill "$SERVER_PID" 2>/dev/null || true
  ip netns del "$NS_A" 2>/dev/null || true
  ip netns del "$NS_B" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
mkdir -p "$OUT"
ip netns add "$NS_A"
ip netns add "$NS_B"
ip link add "$VETH_A" type veth peer name "$VETH_B"
ip link set "$VETH_A" netns "$NS_A"
ip link set "$VETH_B" netns "$NS_B"
ip -n "$NS_A" addr add 10.213.0.1/30 dev "$VETH_A"
ip -n "$NS_B" addr add 10.213.0.2/30 dev "$VETH_B"
ip -n "$NS_A" link set lo up
ip -n "$NS_B" link set lo up
ip -n "$NS_A" link set "$VETH_A" up
ip -n "$NS_B" link set "$VETH_B" up
ip netns exec "$NS_B" python3 -m http.server 8979 --bind 10.213.0.2 >/dev/null 2>&1 &
SERVER_PID="$!"
sleep 0.6
capture() {
  local filename="$1"
  ip netns exec "$NS_A" env PYTHONPATH="$REPO/src" python3 -m routeincident.cli capture \
    --config "$REPO/examples/netns-config.json" --out "$OUT/$filename"
}
echo "1/3: healthy link"
capture before.json
echo "2/3: link down"
ip -n "$NS_A" link set "$VETH_A" down
capture failed.json
echo "3/3: link restored"
ip -n "$NS_A" link set "$VETH_A" up
sleep 0.3
capture recovered.json
PYTHONPATH="$REPO/src" python3 -m routeincident.cli diff \
  --before "$OUT/before.json" --after "$OUT/failed.json" --out "$OUT/incident.json"
PYTHONPATH="$REPO/src" python3 -m routeincident.cli diff \
  --before "$OUT/failed.json" --after "$OUT/recovered.json" --out "$OUT/recovery.json"
echo "Results in $OUT. Namespaces cleaned up on exit."
