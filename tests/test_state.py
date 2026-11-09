# -*- coding: utf-8 -*-
"""wm.engine.state: observer pattern, progress math, reset semantics, thread safety."""
import threading

import pytest

from wm.engine.state import (
    GradingRecord,
    GradingState,
    StateManager,
    state_manager,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_RUNNING,
    STATUS_STOPPED,
)


def test_percentage_and_remaining():
    st = GradingState(total=10, graded=4)
    assert st.percentage == pytest.approx(40.0)
    assert st.remaining == 6

    assert GradingState(total=0, graded=0).percentage == 0.0
    assert GradingState(total=5, graded=9).remaining == 0  # never negative


def test_to_dict_contains_computed_fields():
    d = GradingState(total=4, graded=1).to_dict()
    assert d["percentage"] == pytest.approx(25.0)
    assert d["remaining"] == 3
    assert "records" in d and "errors" in d


def test_subscribe_and_notify():
    received = []
    state_manager.subscribe(received.append)

    state_manager.set_status(STATUS_RUNNING)

    assert received, "listener was never notified"
    assert received[-1]["status"] == STATUS_RUNNING


def test_unsubscribe_stops_notifications():
    received = []

    def listener(snapshot):
        received.append(snapshot)

    state_manager.subscribe(listener)
    state_manager.set_status(STATUS_RUNNING)
    assert len(received) == 1

    state_manager.unsubscribe(listener)
    state_manager.set_status(STATUS_DONE)
    assert len(received) == 1, "listener kept receiving after unsubscribe"


def test_duplicate_subscribe_registers_once():
    received = []
    state_manager.subscribe(received.append)
    state_manager.subscribe(received.append)
    state_manager.set_status(STATUS_RUNNING)
    assert len(received) == 1


def test_start_sets_fields_and_status():
    state_manager.start("zhixue", "数学第15题", total=200, rubric_version="1.1")
    snap = state_manager.snapshot()
    assert snap["status"] == STATUS_RUNNING
    assert snap["platform"] == "zhixue"
    assert snap["question_id"] == "数学第15题"
    assert snap["total"] == 200
    assert snap["graded"] == 0
    assert snap["rubric_version"] == "1.1"
    assert snap["started_at"]
    assert snap["percentage"] == 0.0


def test_start_clears_records_and_errors_from_previous_run():
    """A second run must not inherit the previous run's records/errors/current."""
    state_manager.start("zhixue", "q1", total=10)
    state_manager.add_record(GradingRecord(index=1, student_name="张三", score=8.0))
    state_manager.add_error("旧错误")
    state_manager.set_current(student_name="张三", score=8.0, reasoning="旧理由")
    assert state_manager.snapshot()["graded"] == 1

    state_manager.start("haoyuejuan", "q2", total=5)

    snap = state_manager.snapshot()
    assert snap["records"] == [], "start() must clear records from the previous run"
    assert snap["errors"] == [], "start() must clear errors from the previous run"
    assert snap["graded"] == 0
    assert snap["current_student"] == ""
    assert snap["current_score"] is None
    assert snap["current_reasoning"] == ""
    assert snap["platform"] == "haoyuejuan"
    assert snap["total"] == 5


def test_add_record_auto_numbers_index():
    state_manager.start("zhixue", "q1", total=3)
    state_manager.add_record(GradingRecord(student_name="甲", score=1.0))
    state_manager.add_record(GradingRecord(student_name="乙", score=2.0))

    records = state_manager.snapshot()["records"]
    assert [r["index"] for r in records] == [1, 2]
    assert [r["student_name"] for r in records] == ["甲", "乙"]
    assert state_manager.snapshot()["graded"] == 2


def test_add_record_updates_current_and_percentage():
    state_manager.start("zhixue", "q1", total=2)
    state_manager.add_record(GradingRecord(student_name="甲", score=9.0, reasoning="理由"))

    snap = state_manager.snapshot()
    assert snap["current_student"] == "甲"
    assert snap["current_score"] == pytest.approx(9.0)
    assert snap["current_reasoning"] == "理由"
    assert snap["percentage"] == pytest.approx(50.0)
    assert snap["remaining"] == 1


def test_finish_sets_status_and_backfills_total():
    state_manager.start("zhixue", "q1", total=0)
    state_manager.add_record(GradingRecord(student_name="甲", score=1.0))
    state_manager.add_record(GradingRecord(student_name="乙", score=2.0))

    state_manager.finish()

    snap = state_manager.snapshot()
    assert snap["status"] == STATUS_DONE
    assert snap["total"] == 2, "finish() should backfill total from graded when total was 0"
    assert snap["percentage"] == pytest.approx(100.0)


def test_finish_with_explicit_status():
    state_manager.start("zhixue", "q1", total=1)
    state_manager.finish(STATUS_STOPPED)
    assert state_manager.snapshot()["status"] == STATUS_STOPPED


def test_add_error_fatal_sets_error_status():
    state_manager.start("zhixue", "q1", total=1)
    state_manager.add_error("boom", fatal=True)
    snap = state_manager.snapshot()
    assert snap["status"] == STATUS_ERROR
    assert snap["errors"][0]["message"] == "boom"
    assert snap["errors"][0]["fatal"] is True


def test_reset_returns_to_idle():
    state_manager.start("zhixue", "q1", total=3)
    state_manager.add_record(GradingRecord(student_name="甲"))
    state_manager.reset()
    snap = state_manager.snapshot()
    assert snap["status"] == STATUS_IDLE
    assert snap["records"] == []
    assert snap["errors"] == []
    assert snap["graded"] == 0
    assert snap["total"] == 0


def test_snapshot_is_a_copy():
    state_manager.start("zhixue", "q1", total=1)
    snap = state_manager.snapshot()
    snap["records"].append({"index": 999})
    assert state_manager.snapshot()["records"] == []


def test_concurrent_add_record_smoke():
    state_manager.start("zhixue", "q1", total=400)
    errors = []

    def worker(n):
        try:
            for i in range(50):
                state_manager.add_record(GradingRecord(student_name=f"t{n}-{i}", score=1.0))
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"add_record raised under concurrency: {errors!r}"
    snap = state_manager.snapshot()
    assert snap["graded"] == 400
    assert len(snap["records"]) == 400
    assert snap["percentage"] == pytest.approx(100.0)


def test_state_manager_is_singleton():
    assert StateManager() is state_manager
