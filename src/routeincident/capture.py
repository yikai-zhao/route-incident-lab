"""Collect read-only command output. No remote configuration changes."""
from __future__ import annotations

import datetime as dt
import json
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from .probe import tcp_probe
from .profiles import PROFILES
from .vendors import parse_vendor_commands

IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_config(config: dict[str, Any]) -> None:
    devices = config.get("devices")
    if not isinstance(devices, list) or not devices:
        raise ValueError("config must contain a non-empty devices list")
    names = set()
    for device in devices:
        if not isinstance(device, dict):
            raise ValueError("each device must be an object")
        name = device.get("name")
        if not isinstance(name, str) or not IDENT.fullmatch(name) or name in names:
            raise ValueError("device names must be unique simple identifiers")
        names.add(name)
        profile = device.get("profile")
        if profile not in PROFILES:
            raise ValueError(f"unsupported profile for {name}: {profile}")
        transport = device.get("transport", "local")
        if transport not in ("local", "ssh", "docker"):
            raise ValueError(f"unsupported transport for {name}: {transport}")
        if transport == "docker":
            container = device.get("container")
            if not isinstance(container, str) or not IDENT.fullmatch(container) or not container.startswith("clab-routeincident-"):
                raise ValueError(f"invalid lab container for {name}")
        if transport == "ssh":
            for key in ("host", "user"):
                val = device.get(key)
                if not isinstance(val, str) or not IDENT.fullmatch(val):
                    raise ValueError(f"invalid SSH {key} for {name}")
        prefixes = device.get("watch_prefixes", [])
        if not isinstance(prefixes, list) or any(not isinstance(p, str) for p in prefixes):
            raise ValueError(f"invalid watch_prefixes for {name}")
    probes = config.get("probes", [])
    if not isinstance(probes, list):
        raise ValueError("probes must be a list")
    for p in probes:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str):
            raise ValueError("each probe needs a name")
        if not isinstance(p.get("host"), str) or not IDENT.fullmatch(p["host"]):
            raise ValueError("probe host must be a simple hostname or IP address")
        if not isinstance(p.get("port"), int) or not 1 <= p["port"] <= 65535:
            raise ValueError("probe port must be 1..65535")
        if not isinstance(p.get("count", 3), int) or not 1 <= p.get("count", 3) <= 50:
            raise ValueError("probe count must be 1..50")


def _command(device: dict[str, Any], argv: tuple[str, ...]) -> list[str]:
    if device.get("transport", "local") == "local":
        return list(argv)
    if device.get("transport") == "docker":
        return ["docker", "exec", device["container"], *argv]
    # ssh receives a single shell-quoted command string; BatchMode avoids prompts.
    # A one-element vendor CLI command is passed unchanged to the router shell.
    remote = shlex.join(argv) if len(argv) > 1 else argv[0]
    return [
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=5", "--", f"{device['user']}@{device['host']}", remote,
    ]


def collect_command(device: dict[str, Any], argv: tuple[str, ...], timeout: float = 8.0) -> dict:
    start = time.perf_counter_ns()
    try:
        result = subprocess.run(_command(device, argv), capture_output=True, text=True,
                                timeout=timeout, check=False)
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout[:1_000_000],
            "stderr": result.stderr[:3000],
            "duration_ms": round((time.perf_counter_ns() - start) / 1_000_000, 3),
        }
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "ok": False, "exit_code": None, "stdout": "", "stderr": str(exc)[:3000],
            "duration_ms": round((time.perf_counter_ns() - start) / 1_000_000, 3),
        }


def capture(config: dict[str, Any]) -> dict:
    validate_config(config)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    mono_start = time.monotonic_ns()
    output: dict[str, Any] = {
        "schema_version": 1,
        "timestamp": started,
        "capture_started_utc": started,
        "capture_started_monotonic_ns": mono_start,
        "devices": [],
        "probes": [],
        "capture_mode": "live_observation",
    }
    for device in config["devices"]:
        commands = {label: collect_command(device, argv)
                    for label, argv in PROFILES[device["profile"]].items()}
        device_record = {
            "name": device["name"], "profile": device["profile"],
            "watch_prefixes": device.get("watch_prefixes", []),
            "commands": commands,
        }
        vendor_state = parse_vendor_commands(device["profile"], commands)
        if vendor_state is not None:
            device_record["structured"] = vendor_state
        output["devices"].append(device_record)
    for item in config.get("probes", []):
        output["probes"].append(tcp_probe(
            name=item["name"], host=item["host"], port=item["port"],
            count=item.get("count", 3), timeout_s=item.get("timeout_s", 1.0),
            interval_s=item.get("interval_s", 0.2),
        ))
    output["capture_finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    output["capture_finished_monotonic_ns"] = time.monotonic_ns()
    return output


def load_config(path: str) -> dict:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_config(config)
    return config


def write_json(path: str, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
