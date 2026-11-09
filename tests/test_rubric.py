# -*- coding: utf-8 -*-
"""wm.engine.rubric: build / save / load / lock / markdown / sanitization."""
import json
import re

import pytest

from wm.config import config
from wm.engine.rubric import GradingPoint, Rubric, RubricManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUBRIC_DIR", tmp_path)
    return RubricManager()


def _sample_points():
    return [
        GradingPoint(id=1, description="列出方程", score=5.0, keywords=["方程", "未知数"]),
        GradingPoint(id=2, description="求解并作答", score=8.0, keywords=["结果"]),
    ]


def _sample_rubric(**overrides):
    base = dict(
        question_id="数学第15题",
        platform="zhixue",
        total_score=13.0,
        standard_answer="x = 2",
        analysis="移项后求解",
        grading_points=_sample_points(),
        raw_text="原始细则文本",
    )
    base.update(overrides)
    return Rubric(**base)


# ---------- build ----------
def test_build_rubric_from_raw(manager):
    rubric = manager.build_rubric_from_raw(
        question_id="q1", platform="zhixue", total_score=13.0,
        standard_answer="x=2", analysis="解析", raw_rubric="细则",
        grading_points=_sample_points(),
    )
    assert rubric.question_id == "q1"
    assert rubric.platform == "zhixue"
    assert rubric.total_score == pytest.approx(13.0)
    assert rubric.version == "1.0"
    assert rubric.locked is False
    assert len(rubric.grading_points) == 2
    assert rubric.raw_text == "细则"


def test_build_rubric_defaults_to_no_points(manager):
    rubric = manager.build_rubric_from_raw(
        question_id="q2", platform="haoyuejuan", total_score=10,
        standard_answer="", analysis="", raw_rubric="只有文本",
    )
    assert rubric.grading_points == []
    assert rubric.total_points_score == 0.0
    assert "只有文本" in rubric.to_system_prompt()


def test_total_points_score_sums_and_rounds():
    rubric = Rubric(grading_points=[
        GradingPoint(id=1, score=1.005),
        GradingPoint(id=2, score=2.005),
    ])
    assert rubric.total_points_score == pytest.approx(3.01)
    assert isinstance(rubric.total_points_score, float)


# ---------- save / load ----------
def test_save_load_roundtrip(manager, tmp_path):
    rubric = _sample_rubric()
    json_path = manager.save(rubric)

    assert json_path.exists()
    assert json_path.parent == tmp_path
    assert json_path.name.endswith(".json")

    md_path = tmp_path / f"{json_path.stem}.md"
    assert md_path.exists(), "save() must also export the markdown anchor"

    raw = json.loads(json_path.read_text(encoding="utf-8"))
    assert raw["question_id"] == "数学第15题"
    assert raw["version"] == "1.0"
    assert raw["locked"] is False
    assert raw["created_at"] and raw["updated_at"]
    assert len(raw["grading_points"]) == 2

    loaded = manager.load("数学第15题")
    assert loaded is not None
    assert loaded.question_id == "数学第15题"
    assert loaded.total_score == pytest.approx(13.0)
    assert loaded.standard_answer == "x = 2"
    assert loaded.total_points_score == pytest.approx(13.0)
    assert loaded.grading_points[0].keywords == ["方程", "未知数"]


def test_load_missing_returns_none(manager):
    assert manager.load("不存在的题目") is None


def test_load_corrupt_json_returns_none(manager, tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert manager.load("broken") is None


# ---------- lock ----------
def test_lock_bumps_version_and_persists(manager, tmp_path):
    rubric = _sample_rubric()
    manager.save(rubric)
    assert rubric.version == "1.0"

    locked = manager.lock(rubric)

    assert locked.locked is True
    assert locked.version == "1.1"
    on_disk = json.loads((tmp_path / "数学第15题.json").read_text(encoding="utf-8"))
    assert on_disk["locked"] is True
    assert on_disk["version"] == "1.1"

    # locking again is a no-op (idempotent, no double bump)
    again = manager.lock(locked)
    assert again.version == "1.1"
    assert again.locked is True


def test_lock_increments_existing_version(manager):
    rubric = _sample_rubric(version="1.1")
    manager.lock(rubric)
    assert rubric.version == "1.2"


def test_lock_from_non_numeric_version_still_locks(manager):
    rubric = _sample_rubric(version="v2")
    manager.lock(rubric)
    assert rubric.locked is True
    assert rubric.version


# ---------- markdown / system prompt ----------
def test_markdown_export_contains_anchor_content(manager):
    rubric = _sample_rubric()
    text = rubric.to_markdown()

    assert "# 评分细则" in text
    assert "数学第15题" in text
    assert "x = 2" in text
    assert "移项后求解" in text
    for p in rubric.grading_points:
        assert p.description in text
        assert str(p.score) in text
    assert "方程" in text
    assert "待确认" in text


def test_markdown_export_marks_locked_and_falls_back_to_raw(manager):
    rubric = _sample_rubric(grading_points=[], raw_text="手写细则：第一步 5 分", locked=True)
    text = rubric.to_markdown()
    assert "已锁定" in text
    assert "（未结构化）" in text
    assert "手写细则：第一步 5 分" in text


def test_to_system_prompt_includes_every_point():
    rubric = _sample_rubric()
    prompt = rubric.to_system_prompt()

    assert "1.0" in prompt  # version
    assert "13.0" in prompt  # total score
    for p in rubric.grading_points:
        assert p.description in prompt
        assert str(p.score) in prompt
    assert "方程" in prompt


def test_to_system_prompt_without_points_uses_raw_text():
    assert Rubric(raw_text="只有原文细则").to_system_prompt() == "只有原文细则"
    assert "未提供" in Rubric().to_system_prompt()


# ---------- filename sanitization ----------
@pytest.mark.parametrize("question_id", [
    "数学/第15题: 测试<1>?*|",
    "../../etc/passwd",
    "a\\b/c:d*e?f\"g<h>i|j",
    "?? ???",
])
def test_filename_sanitization(manager, tmp_path, question_id):
    rubric = _sample_rubric(question_id=question_id)
    path = manager.save(rubric)

    assert path.parent == tmp_path, "sanitized filename must stay in RUBRIC_DIR"
    assert not re.search(r'[<>:"/\\|?*]', path.name), f"illegal chars in {path.name!r}"
    assert path.name.endswith(".json")

    loaded = manager.load(question_id)
    assert loaded is not None, "must be loadable with the original question_id"
    assert loaded.question_id == question_id


def test_filename_sanitization_keeps_chinese(manager):
    path = manager.save(_sample_rubric(question_id="数学第15题"))
    assert "数学第15题" in path.name


def test_from_dict_tolerates_missing_fields():
    rubric = Rubric.from_dict({})
    assert rubric.question_id == ""
    assert rubric.version == "1.0"
    assert rubric.locked is False
    assert rubric.grading_points == []
    assert rubric.total_points_score == 0.0
