#!/usr/bin/env bash
# Manual local-only build and validation: no Docker, sudo, CI or cloud.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="$ROOT/lab-output/full-local-verification"
mkdir -p "$OUT"
echo "Local evidence directory: $OUT"
{
  date -u +'%Y-%m-%dT%H:%M:%SZ'
  uname -a
  python3 --version
  cmake --version | head -1
  c++ --version | head -1
  git rev-parse HEAD 2>/dev/null || true
} > "$OUT/environment.txt"

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j2
python3 -m unittest discover -s tests -v > "$OUT/tests.log" 2>&1 || {
  cat "$OUT/tests.log"
  echo "FAILED: tests; see $OUT/tests.log" >&2
  exit 1
}
cat "$OUT/tests.log"

python3 scripts/demo_epoll_local.py --binary build/epoll_connect_sampler \
  --output "$OUT/epoll" > "$OUT/epoll.log"
python3 scripts/demo_observability.py --epoll build/epoll_connect_sampler \
  --netlink build/rtnetlink_monitor --output "$OUT/kernel-and-tcp" \
  > "$OUT/kernel-and-tcp.log"
python3 scripts/demo_multicast_feed.py --binary build/udp_feed_monitor \
  --output "$OUT/multicast-feed" > "$OUT/multicast.log"

python3 - "$OUT" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
epoll = json.loads((root / "epoll" / "report.json").read_text())
observe = json.loads((root / "kernel-and-tcp" / "correlation.json").read_text())
multicast = json.loads((root / "multicast-feed" / "report.json").read_text())
assert epoll["status"] == "PASS"
assert observe["tcp_demo_status"] == "PASS"
assert observe["route_snapshot_records"] > 0
assert multicast["status"] == "PASS"
report = {
    "verification": "PASS",
    "components": {
        "linux_epoll_connect": epoll["status"],
        "kernel_route_snapshot": observe["route_snapshot_records"],
        "cross_plane_events_observed": len(observe["event_windows"]),
        "multicast_sequence": multicast["status"]
    },
    "limits": ["Loopback and read-only kernel route snapshots only.",
               "No FRR fault was injected; no external packet loss measured.",
               "Not a performance benchmark or an exchange-feed validation."]
}
(root / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
PY
