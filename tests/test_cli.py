# -*- coding: utf-8 -*-
"""wm.cli: parser contract, check-env, export (csv/json/html + XSS), monitor, analyze."""
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from wm.cli import build_parser, main
from wm.config import config
from wm.engine import rubric as rubric_module
from wm.engine.ai_grader import AIGradingResult, QuestionAnalysis
from wm.engine.rubric import GradingPoint

from support import free_port, http_get, run_cli, wait_port_open

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- parser
def test_parser_check_env():
    assert build_parser().parse_args(["check-env"]).command == "check-env"


def test_parser_analyze_question_flags():
    args = build_parser().parse_args([
        "analyze-question", "--image", "q.png", "--question-id", "数学第15题",
        "--platform", "zhixue", "--max-score", "20", "--confirm",
    ])
    assert args.image == "q.png"
    assert args.question_id == "数学第15题"
    assert args.platform == "zhixue"
    assert args.max_score == pytest.approx(20.0)
    assert args.confirm is True


def test_parser_grade_flags():
    args = build_parser().parse_args([
        "grade", "--platform", "zhixue", "--url", "https://x", "--rubric-id", "q1",
        "--question-image", "q.png", "--max-score", "20", "--total", "100",
        "--auto", "--headless", "--no-monitor", "--monitor-port", "9001",
    ])
    assert args.platform == "zhixue"
    assert args.rubric_id == "q1"
    assert args.total == 100
    assert args.auto is True
    assert args.headless is True
    assert args.no_monitor is True
    assert args.monitor_port == 9001


def test_parser_monitor_flags():
    args = build_parser().parse_args(["monitor", "--host", "127.0.0.1", "--port", "9002"])
    assert args.command == "monitor"
    assert args.port == 9002
    assert args.host == "127.0.0.1"


def test_parser_export_flags():
    for fmt in ("csv", "json", "html"):
        args = build_parser().parse_args([
            "export", "--format", fmt, "--input", "h.json", "--output", "out.x",
        ])
        assert args.format == fmt
        assert args.input == "h.json"
        assert args.output == "out.x"


def test_parser_demo_flags():
    args = build_parser().parse_args([
        "demo", "--papers", "5", "--interval", "0.01", "--seed", "7",
        "--platform", "haoyuejuan", "--question-id", "q9", "--max-score", "20",
        "--port", "9003", "--no-monitor", "--exit", "--no-browser",
    ])
    assert args.command == "demo"
    assert args.papers == 5
    assert args.interval == pytest.approx(0.01)
    assert args.seed == 7
    assert args.platform == "haoyuejuan"
    assert args.question_id == "q9"
    assert args.max_score == pytest.approx(20.0)
    assert args.port == 9003
    assert args.no_monitor is True
    assert args.exit is True
    assert args.no_browser is True


def test_main_without_command_returns_zero(capsys):
    assert main([]) in (0, None)
    assert "usage" in capsys.readouterr().out.lower()


# ---------------------------------------------------------------- check-env
def test_check_env_subprocess_returns_zero():
    result = run_cli(["check-env"])
    assert result.returncode == 0, result.stderr
    assert "WhisperMark" in result.stdout or "环境自检" in result.stdout


# ---------------------------------------------------------------- export
def _history_payload():
    return {
        "status": "done",
        "platform": "zhixue",
        "question_id": "数学第15题",
        "rubric_version": "1.1",
        "total": 2,
        "graded": 2,
        "percentage": 100.0,
        "remaining": 0,
        "started_at": "2026-01-01 10:00:00",
        "updated_at": "2026-01-01 10:05:00",
        "errors": [],
        "records": [
            {"index": 1, "student_name": "张三", "score": 8.5, "max_score": 13,
             "confidence": "high", "rubric_version": "1.1",
             "reasoning": "步骤完整", "timestamp": "2026-01-01 10:00:01"},
            {"index": 2, "student_name": "<script>alert('xss')</script>",
             "score": 7, "max_score": 13, "confidence": "medium",
             "rubric_version": "1.1",
             "reasoning": '"quoted" & <b>bold</b>',
             "timestamp": "2026-01-01 10:00:02"},
        ],
    }


@pytest.fixture
def history_file(tmp_path):
    p = tmp_path / "history.json"
    p.write_text(json.dumps(_history_payload(), ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def test_export_csv(history_file, tmp_path):
    out = tmp_path / "out.csv"
    rc = main(["export", "--format", "csv", "--input", str(history_file),
               "--output", str(out)])
    assert rc == 0
    assert out.exists()
    with open(out, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 3  # header + 2 records
    assert "学生" in rows[0]
    assert rows[1][1] == "张三"


def test_export_json(history_file, tmp_path):
    out = tmp_path / "out.json"
    rc = main(["export", "--format", "json", "--input", str(history_file),
               "--output", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["graded"] == 2
    assert payload["records"][0]["student_name"] == "张三"
    assert payload["records"][1]["student_name"].startswith("<script>")


def test_export_html_escapes_untrusted_content(history_file, tmp_path):
    out = tmp_path / "out.html"
    rc = main(["export", "--format", "html", "--input", str(history_file),
               "--output", str(out)])
    assert rc == 0
    html = out.read_text(encoding="utf-8")

    assert "<table" in html
    assert "张三" in html
    # XSS: raw script tag from history data must never reach the HTML
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "<b>bold</b>" not in html
    assert "&quot;" in html or "&#34;" in html
    # page's own markup is intact
    assert "<script" not in html.lower().replace("&lt;script", "")


def test_export_without_history_returns_error(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HISTORY_DIR", tmp_path / "empty_history")
    (tmp_path / "empty_history").mkdir()
    rc = main(["export", "--format", "csv"])
    assert rc == 1


def test_export_missing_input_returns_error(tmp_path):
    rc = main(["export", "--format", "csv", "--input", str(tmp_path / "nope.json"),
               "--output", str(tmp_path / "x.csv")])
    assert rc == 1


# ---------------------------------------------------------------- monitor subcommand
def test_monitor_subcommand_serves_on_requested_port(tmp_path):
    port = free_port()
    env = dict(os.environ)
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "WM_DATA_DIR": str(tmp_path / "data"),
        "WM_RUNTIME_DIR": str(tmp_path / "runtime"),
    })
    proc = subprocess.Popen(
        [sys.executable, "main.py", "monitor", "--host", "127.0.0.1",
         "--port", str(port)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        assert wait_port_open(port, timeout=20), "monitor server never listened on --port"
        status, headers, body = http_get(port, "/api/state")
        assert status == 200
        assert json.loads(body.decode("utf-8"))["status"] in {
            "idle", "running", "paused", "done", "error", "stopped"}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


# ---------------------------------------------------------------- analyze-question
@pytest.fixture
def isolated_rubric_dir(tmp_path, monkeypatch):
    rubric_dir = tmp_path / "rubrics"
    rubric_dir.mkdir()
    monkeypatch.setattr(config, "RUBRIC_DIR", rubric_dir)
    monkeypatch.setattr(rubric_module.rubric_manager, "_dir", rubric_dir)
    return rubric_dir


def _fake_image(tmp_path):
    p = tmp_path / "题干.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return p


def test_analyze_question_saves_rubric_on_success(tmp_path, isolated_rubric_dir,
                                                  monkeypatch):
    from wm.engine.ai_grader import ai_grader as global_ai_grader

    payload = json.dumps({
        "model_answer": "x=2", "explanation": "移项",
        "scoring_rubric": "列方程 5 分",
        "grading_points": [{"id": 1, "description": "列出方程", "score": 13,
                            "keywords": ["方程"]}],
    }, ensure_ascii=False)
    monkeypatch.setattr(global_ai_grader, "_chat", lambda *a, **k: payload)
    img = _fake_image(tmp_path)

    rc = main(["analyze-question", "--image", str(img), "--max-score", "13"])

    assert rc == 0
    files = list(isolated_rubric_dir.glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["standard_answer"] == "x=2"
    assert saved["grading_points"][0]["description"] == "列出方程"
    assert files[0].with_suffix(".md").exists()


def test_analyze_question_invalid_ai_output_fails_without_saving(tmp_path,
                                                                 isolated_rubric_dir,
                                                                 monkeypatch):
    from wm.engine.ai_grader import ai_grader as global_ai_grader
    monkeypatch.setattr(global_ai_grader, "_chat", lambda *a, **k: "抱歉，无法识别图片。")
    img = _fake_image(tmp_path)

    rc = main(["analyze-question", "--image", str(img), "--max-score", "13"])

    assert rc == 1, "CLI must fail when the AI output cannot be parsed"
    assert list(isolated_rubric_dir.glob("*.json")) == [], \
        "no rubric file may be written from an invalid AI response"


def test_analyze_question_missing_image_returns_error(tmp_path):
    rc = main(["analyze-question", "--image", str(tmp_path / "nope.png")])
    assert rc == 1
