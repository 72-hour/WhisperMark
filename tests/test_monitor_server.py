# -*- coding: utf-8 -*-
"""wm.monitor.server: synchronous bind, HTTP endpoints, SSE, stop/restart."""
import http.client
import json
import time

import pytest

from wm.engine.state import state_manager, GradingRecord
from wm.monitor.server import MonitorServer, monitor_server as module_singleton

from support import free_port, http_get, port_is_open, wait_port_closed


def _sse_connect(port, timeout=5.0):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.request("GET", "/events")
    resp = conn.getresponse()
    return conn, resp


def _read_sse_data(resp, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = resp.readline()
        if not line:
            break
        text = line.decode("utf-8", "replace").strip()
        if text.startswith("data:"):
            return json.loads(text[len("data:"):].strip())
    raise AssertionError("no SSE data frame received")


@pytest.fixture
def make_server():
    created = []

    def factory(**kwargs):
        srv = MonitorServer(**kwargs)
        created.append(srv)
        return srv

    yield factory
    for srv in created:
        try:
            srv.stop()
        except Exception:
            pass


def test_start_is_synchronous_and_returns_url(make_server, free_port):
    srv = make_server()
    url = srv.start(host="127.0.0.1", port=free_port, allow_port_fallback=False)

    assert isinstance(url, str) and url.startswith("http://127.0.0.1:")
    assert str(free_port) in url
    assert srv.url == url
    assert srv.port == free_port
    assert srv.is_running is True
    # socket must already be listening the instant start() returns
    assert port_is_open(free_port), "start() returned before the socket was listening"


def test_start_raises_when_port_occupied(make_server, occupied_port):
    srv = make_server()
    with pytest.raises(RuntimeError):
        srv.start(host="127.0.0.1", port=occupied_port, allow_port_fallback=False)
    assert srv.is_running is False


def test_start_falls_back_to_other_port_when_allowed(make_server, occupied_port):
    srv = make_server()
    url = srv.start(host="127.0.0.1", port=occupied_port,
                    allow_port_fallback=True, max_fallback=10)

    assert isinstance(url, str)
    assert srv.port != occupied_port, "fallback must pick a different port"
    assert port_is_open(srv.port)
    assert srv.is_running is True


def test_root_serves_monitor_html(make_server, free_port):
    srv = make_server()
    srv.start(port=free_port, allow_port_fallback=False)

    status, headers, body = http_get(free_port, "/")
    text = body.decode("utf-8", "replace")

    assert status == 200
    assert "text/html" in headers.get("content-type", "")
    assert "<html" in text.lower()
    assert ("WhisperMark" in text) or ("阅卷" in text) or ("监控" in text), \
        "GET / does not look like the WhisperMark monitor page"


def test_api_state_matches_schema(make_server, free_port):
    srv = make_server()
    srv.start(port=free_port, allow_port_fallback=False)
    state_manager.start("zhixue", "数学第15题", total=10, rubric_version="1.1")
    state_manager.add_record(GradingRecord(student_name="张三", score=8.0,
                                           max_score=10, confidence="high"))

    status, headers, body = http_get(free_port, "/api/state")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert "application/json" in headers.get("content-type", "")
    for key in ("status", "platform", "question_id", "rubric_version", "total",
                "graded", "current_student", "current_score", "current_reasoning",
                "errors", "records", "started_at", "updated_at",
                "percentage", "remaining"):
        assert key in payload, f"/api/state missing key: {key}"

    assert payload["status"] == "running"
    assert payload["graded"] == 1
    assert payload["total"] == 10
    assert payload["percentage"] == pytest.approx(10.0)
    assert payload["remaining"] == 9
    assert isinstance(payload["records"], list) and len(payload["records"]) == 1
    assert payload["records"][0]["student_name"] == "张三"
    assert isinstance(payload["errors"], list)


def test_api_health(make_server, free_port):
    srv = make_server()
    srv.start(port=free_port, allow_port_fallback=False)

    status, headers, body = http_get(free_port, "/api/health")
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert isinstance(payload, dict)


def test_sse_delivers_initial_snapshot(make_server, free_port):
    srv = make_server()
    srv.start(port=free_port, allow_port_fallback=False)

    conn, resp = _sse_connect(free_port)
    try:
        assert resp.status == 200
        assert "text/event-stream" in resp.getheader("Content-Type", "")
        frame = _read_sse_data(resp)
        assert isinstance(frame, dict)
        assert "status" in frame
    finally:
        conn.close()


def test_sse_broadcasts_state_changes(make_server, free_port):
    srv = make_server()
    srv.start(port=free_port, allow_port_fallback=False)
    state_manager.start("zhixue", "q1", total=5)

    conn, resp = _sse_connect(free_port)
    try:
        _read_sse_data(resp)  # initial snapshot
        state_manager.add_record(GradingRecord(student_name="李四", score=7.0))
        frame = _read_sse_data(resp)
        assert frame["graded"] == 1
        assert frame["current_student"] == "李四"
    finally:
        conn.close()


def test_stop_frees_the_port(make_server, free_port):
    srv = make_server()
    srv.start(port=free_port, allow_port_fallback=False)
    assert port_is_open(free_port)

    srv.stop()

    assert srv.is_running is False
    assert wait_port_closed(free_port, timeout=5.0), "stop() did not free the port"


def test_same_instance_can_start_stop_start_again(make_server):
    srv = make_server()

    srv.start(port=free_port(), allow_port_fallback=False)
    first_port = srv.port
    assert port_is_open(first_port)
    srv.stop()
    assert wait_port_closed(first_port)

    srv.start(port=free_port(), allow_port_fallback=False)
    second_port = srv.port
    assert port_is_open(second_port)
    assert srv.is_running is True
    srv.stop()
    assert wait_port_closed(second_port)


def test_module_singleton_type():
    assert isinstance(module_singleton, MonitorServer)
