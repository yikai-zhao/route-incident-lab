#!/usr/bin/env python3
"""Actual HTTP response check; timings are client-observed, NOT one-way wire latency."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import socket
import time
import sys
import select

_STATUS = re.compile(rb"HTTP/1\.[01] ([0-9]{3})(?:[ \t].*)?$")


def _status(sock: socket.socket) -> int:
    """Read a complete status line; TCP recv() may return only a fragment."""
    line = bytearray()
    while b"\r\n" not in line:
        chunk = sock.recv(512)
        if not chunk:
            raise OSError("HTTP response ended before status line")
        line.extend(chunk)
        if len(line) > 4096:
            raise OSError("HTTP status line exceeds 4096 bytes")
    match = _STATUS.fullmatch(bytes(line).split(b"\r\n", 1)[0])
    if not match:
        raise OSError("invalid HTTP status line")
    return int(match.group(1))


def one(host: str, port: int, timeout: float) -> dict:
    t0 = time.monotonic_ns()
    status = None
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            connected = time.monotonic_ns()
            sock.settimeout(timeout)
            sock.sendall(b"GET / HTTP/1.0\r\nHost: lab\r\nConnection: close\r\n\r\n")
            status = _status(sock)
            finished = time.monotonic_ns()
            if status != 200:
                raise OSError(f"unexpected HTTP status: {status}")
        return {"ok": True, "connect_ms": round((connected-t0)/1e6, 3),
                "delivery_ms": round((finished-t0)/1e6, 3), "http_status": status,
                "error": None}
    except (OSError, TimeoutError) as exc:
        return {"ok": False, "connect_ms": None, "delivery_ms": None,
                "http_status": status, "error": type(exc).__name__}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="10.10.2.2")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--count", type=int, default=8)
    p.add_argument("--interval", type=float, default=0.2)
    p.add_argument("--timeout", type=float, default=0.5)
    p.add_argument("--stream", action="store_true", help="finite streaming; stop through stdin")
    p.add_argument("--duration", type=float, default=180.0)
    args = p.parse_args()
    if not 1 <= args.count <= 200 or not 0 <= args.interval <= 10 or not 0.05 <= args.timeout <= 10:
        p.error("probe limits exceeded")
    if not 0.5 <= args.duration <= 180:
        p.error("stream duration must be 0.5..180 seconds")
    deadline = time.monotonic() + args.duration
    limit = 2000 if args.stream else args.count
    for i in range(limit):
        if args.stream:
            if time.monotonic() >= deadline:
                break
            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if ready:
                command = sys.stdin.readline()
                if not command or command.strip().lower() == "stop":
                    break
        result = one(args.host, args.port, args.timeout)
        result.update({"seq": i, "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                       "observed_monotonic_ns": time.monotonic_ns()})
        print(json.dumps(result, sort_keys=True), flush=True)
        if i + 1 < limit and args.interval:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
