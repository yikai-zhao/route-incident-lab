#!/usr/bin/env bash
# Local-only Route Incident Lab preflight. Never injects faults or triggers CI.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="$ROOT/lab-output/smoke-once"
TOPO="$ROOT/labs/three-router/topology.clab.yml"
mkdir -p "$OUT"
exec > >(tee "$OUT/run.log") 2>&1

fail() { echo "ERROR: $*" >&2; exit 2; }
diagnose() {
  echo "Preflight failed; logs in $OUT. No faults were injected."
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    docker ps -a --format 'table {{.Names}}\t{{.Status}}' || true
    for node in r1 r2 r3 h1 h2; do
      docker logs --tail 50 "clab-routeincident-$node" > "$OUT/$node.log" 2>&1 || true
    done
  fi
}
trap diagnose ERR

[[ "$(uname -s)" == Linux ]] || fail "Run inside WSL2/Linux, not Windows PowerShell."
for tool in docker containerlab python3; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is missing inside WSL/Linux."
done
docker info >/dev/null 2>&1 || fail "Docker Engine unavailable to current user."
echo "1/4 Validate local topology"
containerlab validate -t "$TOPO"

if docker ps -a --format '{{.Names}}' | grep -q '^clab-routeincident-'; then
  echo "2/4 Existing lab detected: checking five running containers"
  for n in r1 r2 r3 h1 h2; do
    running="$(docker inspect --format '{{.State.Running}}' "clab-routeincident-$n" 2>/dev/null || true)"
    [[ "$running" == true ]] || fail "Incomplete lab. Inspect containers and remove only routeincident topology if needed."
  done
else
  echo "2/4 Build local endpoint image (no cloud compute)"
  docker build -t routeincident-endpoint:local \
    -f "$ROOT/labs/three-router/Dockerfile.endpoint" "$ROOT/labs/three-router"
  echo "3/4 Deploy the five lab containers"
  if [[ "$(id -u)" == 0 ]]; then
    containerlab deploy -t "$TOPO"
  else
    sudo containerlab deploy -t "$TOPO"
  fi
fi

echo "4/4 Run read-only BGP and HTTP Smoke Test"
for attempt in 1 2 3; do
  if PYTHONPATH="$ROOT/src" python3 scripts/lab_smoke.py --output "$OUT/smoke.json"; then
    echo "PASS: 3 FRR routers and client-side HTTP smoke evidence captured."
    echo "Evidence: $OUT/smoke.json"
    echo "Logs: $OUT/run.log"
    echo "No GitHub Actions, cloud services, or fault injection used."
    exit 0
  fi
  if [[ "$attempt" -lt 3 ]]; then
    echo "Not ready (attempt $attempt/3); retrying after 10 seconds..."
    sleep 10
  fi
done
fail "Smoke Test failed on all three attempts; inspect local lab logs."
