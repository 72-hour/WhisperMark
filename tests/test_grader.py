# -*- coding: utf-8 -*-
"""wm.engine.grader: single-paper pipeline, thread lifecycle, stop/wait cleanliness."""
import threading
import time

import pytest

from wm.config import config
from wm.engine import grader as grader_module
from wm.engine.ai_grader import AIGradingResult
from wm.engine.grader import Grader
from wm.engine.state import state_manager, STATUS_DONE, STATUS_STOPPED
from wm.platforms.base import ScoreInput


class FakeAdapter:
    """Minimal PlatformAdapter stand-in (no browser, no Playwright)."""

    name = "fake"
    id = "fake"

    def __init__(self, max_score=13.0):
        self.images = ["https://example.test/answer-1.png"]
        self.max_score_value = max_score
        self.student = "张三"
        self.filled = []
        self.submitted = 0
        self.waits = 0

    def gather_answer_images(self):
        return list(self.images)

    def download_image(self, url):
        return b"fake-image-bytes"

    def get_score_inputs(self):
        if self.max_score_value:
            return [ScoreInput(label="总分", index=0, max_score=self.max_score_value)]
        return []

    def get_current_student_name(self):
        return self.student

    def fill_scores(self, scores):
        self.filled.append(list(scores))
        return True

    def submit_grade(self):
        self.submitted += 1
        return True

    def wait_for_next_paper(self, old_image_url=""):
        self.waits += 1
        return True


class StubAI:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def grade(self, image_data, rubric, max_score):
        self.calls += 1
        return self.result


def _patch_ai(monkeypatch, result):
    stub = StubAI(result)
    monkeypatch.setattr(grader_module, "ai_grader", stub)
    return stub


def test_grade_one_full_pipeline(monkeypatch):
    adapter = FakeAdapter(max_score=13.0)
    monkeypatch.setattr(config, "RETRY_DELAY", 0.0)
    stub = _patch_ai(monkeypatch, AIGradingResult(
        score=8.0, max_score=13.0, reasoning="采分点 1 命中", confidence="high"))

    grader = Grader(browser=None, adapter=adapter)
    result = grader.grade_one()

    assert result is not None
    assert result.score == pytest.approx(8.0)
    assert adapter.filled == [[8.0]]
    assert adapter.submitted == 1
    assert adapter.waits == 1

    snap = state_manager.snapshot()
    assert snap["graded"] == 1
    record = snap["records"][0]
    assert record["student_name"] == "张三"
    assert record["score"] == pytest.approx(8.0)
    assert record["max_score"] == pytest.approx(13.0)
    assert record["confidence"] == "high"
    assert record["index"] == 1


def test_grade_one_ai_error_is_recorded_not_raised(monkeypatch):
    adapter = FakeAdapter()
    monkeypatch.setattr(config, "RETRY_DELAY", 0.0)
    _patch_ai(monkeypatch, AIGradingResult(score=0.0, confidence="error",
                                           reasoning="AI 调用失败: timeout"))

    grader = Grader(browser=None, adapter=adapter)
    result = grader.grade_one()

    assert result is None
    snap = state_manager.snapshot()
    assert snap["graded"] == 0
    assert snap["records"] == []
    assert snap["errors"], "AI failure must be surfaced in state.errors"
    assert adapter.filled == []
    assert adapter.submitted == 0


def test_read_max_score_from_adapter(monkeypatch):
    adapter = FakeAdapter(max_score=20.0)
    grader = Grader(browser=None, adapter=adapter)
    assert grader.read_max_score() == pytest.approx(20.0)


def test_read_max_score_falls_back_to_rubric_then_config():
    from wm.engine.rubric import Rubric

    adapter = FakeAdapter(max_score=0.0)
    grader = Grader(browser=None, adapter=adapter, rubric=Rubric(total_score=15.0))
    assert grader.read_max_score() == pytest.approx(15.0)

    grader2 = Grader(browser=None, adapter=FakeAdapter(max_score=0.0))
    assert grader2.read_max_score() == pytest.approx(config.MAX_SCORE)


def test_start_auto_sets_running_before_thread_spawns(monkeypatch):
    """Race regression: is_running must already be True when start_auto returns."""
    adapter = FakeAdapter()
    grader = Grader(browser=None, adapter=adapter)
    release = threading.Event()
    monkeypatch.setattr(grader, "auto_loop", lambda: release.wait(5))

    thread = grader.start_auto()
    try:
        assert grader.is_running is True, \
            "start_auto() must set _running before the thread gets scheduled"
    finally:
        grader.stop()
        release.set()
        grader.wait(timeout=10)
    assert not thread.is_alive()


def test_start_auto_wait_finishes_naturally(monkeypatch):
    adapter = FakeAdapter()
    adapter.gather_answer_images = lambda: []  # 5 empty rounds -> loop ends
    monkeypatch.setattr(config, "RETRY_DELAY", 0.01)
    grader = Grader(browser=None, adapter=adapter)

    thread = grader.start_auto()
    try:
        assert grader.is_running is True
        grader.wait(timeout=10)
    finally:
        if thread.is_alive():
            grader.stop()
            grader.wait(timeout=10)

    assert not thread.is_alive(), "auto loop thread should exit after grading completes"
    assert state_manager.snapshot()["status"] == STATUS_DONE


def test_stop_and_wait_joins_cleanly(monkeypatch):
    adapter = FakeAdapter()
    release = threading.Event()

    def blocking_gather():
        release.wait(10)
        return []

    adapter.gather_answer_images = blocking_gather
    monkeypatch.setattr(config, "RETRY_DELAY", 0.01)
    grader = Grader(browser=None, adapter=adapter)

    thread = grader.start_auto()
    time.sleep(0.1)  # let the loop enter grade_one
    assert thread.is_alive()

    grader.stop()
    release.set()
    grader.wait(timeout=10)

    assert not thread.is_alive(), "stop()+wait() did not join the grading thread"
    snap = state_manager.snapshot()
    assert snap["status"] == STATUS_STOPPED
    assert grader.is_running is False


def test_run_forever_runs_in_calling_thread_and_terminates(monkeypatch):
    adapter = FakeAdapter()
    adapter.gather_answer_images = lambda: []
    monkeypatch.setattr(config, "RETRY_DELAY", 0.01)
    grader = Grader(browser=None, adapter=adapter)

    start = time.monotonic()
    grader.run_forever()  # must return in this (main) thread, no background thread needed
    elapsed = time.monotonic() - start

    assert elapsed < 10
    assert state_manager.snapshot()["status"] == STATUS_DONE
    assert grader.is_running is False


def test_pause_and_resume_status(monkeypatch):
    adapter = FakeAdapter()
    adapter.gather_answer_images = lambda: []
    monkeypatch.setattr(config, "RETRY_DELAY", 0.2)
    monkeypatch.setattr(config, "MAX_EMPTY_RETRIES", 50)  # keep the loop alive while we pause
    grader = Grader(browser=None, adapter=adapter)

    thread = grader.start_auto()
    try:
        assert state_manager.snapshot()["status"] == "running"

        grader.pause()
        assert state_manager.snapshot()["status"] == "paused"
        assert grader.is_paused is True

        grader.resume()
        assert state_manager.snapshot()["status"] == "running"
        assert grader.is_paused is False
    finally:
        grader.stop()
        grader.wait(timeout=10)
        assert not thread.is_alive()
