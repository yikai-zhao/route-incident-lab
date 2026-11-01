#!/usr/bin/env python3
"""Actual HTTP response check; timings are client-observed, NOT one-way wire latency."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import socket
import time


def one(host: str, port: int, timeout: float) -> dict:
    t0 = time.monotonic_ns()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            connected = time.monotonic_ns()
            sock.settimeout(timeout)
            sock.sendall(b"GET / HTTP/1.0\r\nHost: lab\r\nConnection: close\r\n\r\n")
            line = sock.recv(128).split(b"\r\n", 1)[0]
            finished = time.monotonic_ns()
            if not (line.startswith(b"HTTP/1.0 200") or line.startswith(b"HTTP/1.1 200")):
                raise OSError(f"unexpected HTTP response: {line[:50]!r}")
        return {"ok": True, "connect_ms": round((connected-t0)/1e6, 3),
                "delivery_ms": round((finished-t0)/1e6, 3), "error": None}
    except (OSError, TimeoutError) as exc:
        return {"ok": False, "connect_ms": None, "delivery_ms": None,
                "error": type(exc).__name__}


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--host", default="10.10.2.2")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--count", type=int, default=8)
    p.add_argument("--interval", type=float, default=0.2)
    p.add_argument("--timeout", type=float, default=0.5)
    a=p.parse_args()
    if not 1 <= a.count <= 200 or not 0 <= a.interval <= 10 or not 0.05 <= a.timeout <= 10:
        p.error("probe limits exceeded")
    for i in range(a.count):
        result=one(a.host,a.port,a.timeout)
        result.update({"seq":i,"observed_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
                       "observed_monotonic_ns":time.monotonic_ns()})
        print(json.dumps(result,sort_keys=True),flush=True)
        if i+1<a.count: time.sleep(a.interval)

if __name__=="__main__":main()
