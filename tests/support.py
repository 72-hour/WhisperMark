# -*- coding: utf-8 -*-
"""Shared helpers for the WhisperMark 2.0 QA test-suite (no backend imports here)."""
import http.client
import json
import socket
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    """Return a currently-free ephemeral TCP port on 127.0.0.1."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def port_is_open(port: int, timeout: float = 0.2) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def wait_port_open(port: int, timeout: float = 5.0) -> bool:
    return wait_until(lambda: port_is_open(port), timeout=timeout)


def wait_port_closed(port: int, timeout: float = 5.0) -> bool:
    return wait_until(lambda: not port_is_open(port), timeout=timeout)


def http_get(port: int, path: str, timeout: float = 5.0):
    """Minimal GET helper -> (status, headers dict, body bytes)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
    finally:
        conn.close()


def http_json(port: int, path: str, timeout: float = 5.0):
    status, headers, body = http_get(port, path, timeout=timeout)
    return status, headers, json.loads(body.decode("utf-8"))


def run_cli(argv, cwd=None, timeout: float = 60.0, env_extra=None):
    """Run `python main.py <argv>` in a subprocess, return CompletedProcess."""
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "main.py", *argv],
        cwd=str(cwd or ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
