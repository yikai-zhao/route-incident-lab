"""Bounded continuous, client-observed delivery evidence.

Observations share the Linux host monotonic clock when run inside local
Containerlab containers. These are sampled HTTP results, NOT exact packet loss,
BGP convergence times, or one-way wire latency.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from typing import Any


def summarize_delivery(samples: list[dict], fault_started_ns: int | None = None) -> dict:
    ordered = sorted((s for s in samples
                      if isinstance(s.get("observed_monotonic_ns"), int)
                      and isinstance(s.get("ok"), bool)),
                     key=lambda s: s["observed_monotonic_ns"])
    report: dict[str, Any] = {
        "attempts": len(ordered), "successes": sum(s["ok"] for s in ordered),
        "failures": sum(not s["ok"] for s in ordered),
        "measurement": "sampled_http_response_delivery",
        "confidence": "sampled_window_only",
        "observed_failure_span_ms": None,
        "possible_outage_window_ms": None,
        "first_failure_after_fault_ms": None,
        "notes": [
            "Failure span is a sampled lower bound, not exact downtime.",
            "Possible outage window is bounded by neighboring good samples.",
            "A TCP connection alone is not proof of HTTP application delivery.",
        ],
    }
    if fault_started_ns is None or not ordered:
        return report
    post = [i for i, s in enumerate(ordered)
            if s["observed_monotonic_ns"] >= fault_started_ns and not s["ok"]]
    if not post:
        report["outage_observed"] = False
        return report
    report["outage_observed"] = True
    first = post[0]
    # Group contiguous failed samples into runs; use the longest observed run.
    runs: list[tuple[int, int]] = []
    start = last = first
    for i in post[1:]:
        if i == last + 1:
            last = i
        else:
            runs.append((start, last))
            start = last = i
    runs.append((start, last))
    start, end = max(runs, key=lambda ab: (ab[1] - ab[0], ab[1]))
    t_first = ordered[start]["observed_monotonic_ns"]
    t_last = ordered[end]["observed_monotonic_ns"]
    report["first_failure_after_fault_ms"] = round(
        (ordered[first]["observed_monotonic_ns"] - fault_started_ns) / 1e6, 3)
    report["observed_failure_span_ms"] = round((t_last - t_first) / 1e6, 3)
    before = next((ordered[i] for i in range(start - 1, -1, -1) if ordered[i]["ok"]), None)
    after = next((ordered[i] for i in range(end + 1, len(ordered)) if ordered[i]["ok"]), None)
    if before and after:
        report["possible_outage_window_ms"] = round(
            (after["observed_monotonic_ns"] - before["observed_monotonic_ns"]) / 1e6, 3)
    report["first_failed_sample_monotonic_ns"] = t_first
    report["last_failed_sample_monotonic_ns"] = t_last
    report["recovered_delivery_observed"] = after is not None
    return report


class LocalLabHttpSampler:
    """Start one finite stream inside h1. Never targets production systems.

    The Docker exec process is sent an explicit 'stop' on stdin; remote probe
    also has time/count caps to prevent indefinite background operation.
    """

    def __init__(self, container: str = "clab-routeincident-h1",
                 host: str = "10.10.2.2", port: int = 8080) -> None:
        if container != "clab-routeincident-h1" or host != "10.10.2.2" or port != 8080:
            raise ValueError("continuous sampling is restricted to the local testbed")
        self.args = ["docker", "exec", "-i", container,
                     "python3", "/opt/routeincident/tcp_http_probe.py",
                     "--host", host, "--port", str(port),
                     "--stream", "--duration", "180",
                     "--interval", "0.20", "--timeout", "0.5"]
        self.samples: list[dict] = []
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.errors: list[str] = []

    def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if isinstance(item.get("ok"), bool) and len(self.samples) < 2000:
                    self.samples.append(item)
            except (ValueError, TypeError) as exc:
                self.errors.append(f"invalid probe JSON: {exc}")

    def start(self) -> None:
        if self.proc is not None:
            raise RuntimeError("sampler already started")
        self.proc = subprocess.Popen(self.args, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.samples:
                return
            if self.proc.poll() is not None:
                break
            time.sleep(0.05)
        self.stop()
        raise RuntimeError("continuous HTTP probe did not produce a sample within 5 seconds")

    def stop(self) -> list[dict]:
        if self.proc is None:
            return list(self.samples)
        proc = self.proc
        if proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.write("stop\n")
                    proc.stdin.flush()
                    proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        if self.thread:
            self.thread.join(timeout=3)
        if proc.stderr:
            errors = proc.stderr.read().strip()
            if errors:
                self.errors.append(errors[:1000])
        if proc.returncode not in (0, None):
            self.errors.append(f"probe process exit={proc.returncode}")
        self.proc = None
        return list(self.samples)
