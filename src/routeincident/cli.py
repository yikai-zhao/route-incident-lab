"""Small CLI for live snapshots and offline incident replay."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .analyze import analyze_snapshot, compare
from .capture import capture, load_config, write_json
from .probe import tcp_probe
from .scenarios import analyze_three_phases

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _print_findings(report: dict) -> None:
    stats = report["summary"]
    print(f"Findings: {stats['total']} ({stats['warnings']} warnings)")
    for item in report["findings"]:
        target = item.get("device", item.get("probe", ""))
        suffix = item.get("prefix", item.get("command", ""))
        print(f"  [{item['severity']}] {item['kind']}  {target} {suffix}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="routeincident", description="Read-only router incident investigation")
    sub = parser.add_subparsers(dest="action", required=True)
    c = sub.add_parser("capture", help="save one live read-only snapshot")
    c.add_argument("--config", required=True)
    c.add_argument("--out", required=True)
    d = sub.add_parser("diff", help="compare snapshots and produce a report")
    d.add_argument("--before", required=True)
    d.add_argument("--after", required=True)
    d.add_argument("--out")
    demo = sub.add_parser("demo", help="replay synthetic route loss and recovery fixtures")
    demo.add_argument("--fixtures", default=str(ROOT / "examples"))
    sd = sub.add_parser("scenario-demo", help="three OFFLINE synthetic lab scenarios")
    sd.add_argument("--fixtures", default=str(ROOT / "examples/scenarios"))
    sd.add_argument("--out-dir", help="optional folder for machine-readable reports")
    p = sub.add_parser("probe", help="bounded TCP connect-time sampling")
    p.add_argument("--host", required=True)
    p.add_argument("--port", required=True, type=int)
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--out")
    m = sub.add_parser("monitor", help="finite live polling (no daemon or scheduled job)")
    m.add_argument("--config", required=True)
    m.add_argument("--out-dir", required=True)
    m.add_argument("--cycles", type=int, default=3)
    m.add_argument("--interval", type=float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "capture":
            snapshot = capture(load_config(args.config))
            write_json(args.out, snapshot)
            print(f"Saved snapshot to {args.out}; {len(analyze_snapshot(snapshot))} capture findings")
        elif args.action == "diff":
            report = compare(_read(args.before), _read(args.after))
            if args.out:
                write_json(args.out, report)
            _print_findings(report)
        elif args.action == "demo":
            base = Path(args.fixtures)
            before = _read(str(base / "healthy.json"))
            failed = _read(str(base / "route-lost.json"))
            restored = _read(str(base / "recovered.json"))
            print("=== Healthy -> Route lost (synthetic) ===")
            _print_findings(compare(before, failed))
            print("=== Route lost -> Recovered (synthetic) ===")
            _print_findings(compare(failed, restored))
            print("These are synthetic fixtures, not measurements from Ciena or live routers.")
        elif args.action == "scenario-demo":
            for scenario in ("neighbor_outage", "backup_failover", "route_ok_blackhole"):
                case = _read(str(Path(args.fixtures) / f"{scenario}.json"))
                report = analyze_three_phases(case["before"], case["fault"], case["recovered"], scenario)
                if args.out_dir:
                    write_json(str(Path(args.out_dir) / f"{scenario}-report.json"), report)
                print(f"{scenario}: assertions {sum(report['assertions'].values())}/"
                      f"{len(report['assertions'])}; "
                      f"diagnostic leads {len(report['diagnostic_leads'])}; SYNTHETIC")
        elif args.action == "probe":
            result = tcp_probe("manual", args.host, args.port, args.count)
            if args.out:
                write_json(args.out, result)
            print(json.dumps(result, indent=2))
        elif args.action == "monitor":
            if not 1 <= args.cycles <= 100 or not 0.5 <= args.interval <= 3600:
                raise ValueError("cycles must be 1..100 and interval >= 0.5 seconds")
            config = load_config(args.config)
            dest = Path(args.out_dir)
            previous = None
            for index in range(args.cycles):
                current = capture(config)
                write_json(str(dest / f"snapshot-{index:03d}.json"), current)
                if previous:
                    report = compare(previous, current)
                    write_json(str(dest / f"incident-{index:03d}.json"), report)
                    _print_findings(report)
                previous = current
                if index + 1 < args.cycles:
                    time.sleep(args.interval)
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
