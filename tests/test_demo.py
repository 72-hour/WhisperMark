# -*- coding: utf-8 -*-
"""`python main.py demo` end-to-end: deterministic fake grading run + monitor."""
import json
import os
import subprocess
import sys

import pytest

from support import ROOT, free_port, http_get, run_cli, wait_port_open

DEMO_FLAGS = ["demo", "--papers", "5", "--interval", "0.01", "--seed", "42", "--no-browser"]


def _isolated_env(tmp_path):
    return {
        "WM_DATA_DIR": str(tmp_path / "data"),
        "WM_RUNTIME_DIR": str(tmp_path / "runtime"),
        "WM_MONITOR_ENABLED": "false",
    }


def test_demo_no_monitor_exit_end_to_end(tmp_path):
    result = run_cli(DEMO_FLAGS + ["--no-monitor", "--exit"],
                     env_extra=_isolated_env(tmp_path), timeout=120)

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    history_dir = tmp_path / "data" / "history"
    history_files = sorted(history_dir.glob("*.json"))
    assert history_files, f"demo wrote no history JSON under {history_dir}"
    payload = json.loads(history_files[-1].read_text(encoding="utf-8"))

    assert payload["graded"] == 5
    assert payload["total"] == 5
    assert payload["percentage"] == pytest.approx(100.0)
    assert payload["status"] == "done"
    assert payload["remaining"] == 0
    assert len(payload["records"]) == 5
    assert [r["index"] for r in payload["records"]] == [1, 2, 3, 4, 5]
    for record in payload["records"]:
        assert 0.0 <= float(record["score"]) <= float(record["max_score"])
        assert record["student_name"]
        assert record["confidence"] in {"high", "medium", "low", "error"}


def test_demo_monitor_reachable_during_run(tmp_path):
    port = free_port()
    env = dict(os.environ)
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        **_isolated_env(tmp_path),
        "WM_MONITOR_ENABLED": "true",
    })

    proc = subprocess.Popen(
        [sys.executable, "main.py", "demo",
         "--papers", "40", "--interval", "0.05", "--seed", "1",
         "--no-browser", "--port", str(port), "--exit"],        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        assert wait_port_open(port, timeout=30), \
            "demo monitor never listened on --port during the run"
        status, headers, body = http_get(port, "/api/state")
        assert status == 200
        payload = json.loads(body.decode("utf-8"))
        assert payload["status"] in {"idle", "running", "done"}
        assert payload["total"] >= 0

        proc.wait(timeout=120)
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_demo_default_keeps_monitor_serving_after_completion(tmp_path):
    """Without --exit the demo should keep the page serving until the user quits."""
    port = free_port()
    env = dict(os.environ)
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        **_isolated_env(tmp_path),
        "WM_MONITOR_ENABLED": "true",
    })

    proc = subprocess.Popen(
        [sys.executable, "main.py", "demo",
         "--papers", "3", "--interval", "0.01", "--seed", "2",
         "--no-browser", "--port", str(port)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        assert wait_port_open(port, timeout=30)

        done = False
        for _ in range(300):
            try:
                status, headers, body = http_get(port, "/api/state", timeout=2)
                if json.loads(body.decode("utf-8")).get("status") == "done":
                    done = True
                    break
            except OSError:
                pass
            if proc.poll() is not None:
                break
            import time
            time.sleep(0.1)
        assert done, "demo never reached status=done"
        assert proc.poll() is None, "without --exit the demo must keep serving; it exited"
        status, headers, body = http_get(port, "/")
        assert status == 200
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
