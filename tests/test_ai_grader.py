# -*- coding: utf-8 -*-
"""wm.engine.ai_grader: JSON extraction, analyze_question, grade (no network)."""
import json
import types

import pytest

from wm.engine.ai_grader import AIGrader, AIGradingResult, QuestionAnalysis
from wm.engine.rubric import GradingPoint, Rubric


# ---------------------------------------------------------------- helpers
def _fake_completions(content):
    """Build an OpenAI-client-shaped fake; records create() kwargs on .captured."""
    state = {"captured": []}

    def create(**kwargs):
        state["captured"].append(kwargs)
        msg = types.SimpleNamespace(content=content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    fake = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)),
        captured=state["captured"],
    )
    return fake


def _grader_with_chat(monkeypatch, reply):
    g = AIGrader()
    monkeypatch.setattr(g, "_chat", lambda *a, **k: reply)
    return g


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


# ---------------------------------------------------------------- _extract_json
@pytest.mark.parametrize("text,expected_score", [
    ('{"score": 8, "confidence": "high"}', 8.0),
    ('```json\n{"score": 6.5, "confidence": "low"}\n```', 6.5),
    ('```\n{"score": 4}\n```', 4.0),
    ('分析结果如下：\n```json\n{"score": 6.5}\n```\n希望有帮助', 6.5),
    ('The answer is {"score": 4, "confidence": "low"} done', 4.0),
])
def test_extract_json_plain_fenced_and_prose(text, expected_score):
    data = AIGrader()._extract_json(text)
    assert isinstance(data, dict)
    assert float(data["score"]) == pytest.approx(expected_score)


@pytest.mark.parametrize("text,expected_score", [
    ('{"score": 8.5, "reasoning": "missing quote}', 8.5),   # broken JSON -> regex fallback
    ("该生得分: 7 分，理由：步骤完整", 7.0),
    ("最终 8/10 分", 8.0),
])
def test_extract_json_falls_back_to_score_regex(text, expected_score):
    data = AIGrader()._extract_json(text)
    assert isinstance(data, dict)
    assert float(data["score"]) == pytest.approx(expected_score)


@pytest.mark.parametrize("text", ["", "抱歉我无法评分", "完全无关的文字", None])
def test_extract_json_garbage_returns_none(text):
    assert AIGrader()._extract_json(text) is None


def test_extract_json_non_dict_returns_none():
    """Callers assume a dict; a JSON array must not leak through as a 'success'."""
    assert AIGrader()._extract_json('["a", "b"]') is None
    assert AIGrader()._extract_json('"just a string"') is None
    assert AIGrader()._extract_json("42") is None


# ---------------------------------------------------------------- analyze_question
def test_analyze_question_valid_payload(monkeypatch):
    payload = json.dumps({
        "model_answer": "x = 2",
        "explanation": "移项后求解",
        "scoring_rubric": "列方程 5 分，求解 8 分",
        "grading_points": [
            {"id": 1, "description": "列出方程", "score": 5, "keywords": ["方程"]},
            {"id": 2, "description": "求解并作答", "score": 8, "keywords": ["结果"]},
        ],
    }, ensure_ascii=False)
    g = _grader_with_chat(monkeypatch, payload)

    result = g.analyze_question(PNG, 13.0)

    assert isinstance(result, QuestionAnalysis)
    assert result.ok is True
    assert not result.error
    assert result.model_answer == "x = 2"
    assert result.explanation == "移项后求解"
    assert result.scoring_rubric == "列方程 5 分，求解 8 分"
    assert result.max_score == pytest.approx(13.0)
    assert result.raw_response == payload
    assert isinstance(result.warnings, list)
    assert len(result.grading_points) == 2
    assert all(isinstance(p, GradingPoint) for p in result.grading_points)
    assert [p.id for p in result.grading_points] == [1, 2]
    assert [p.score for p in result.grading_points] == [5.0, 8.0]
    assert result.grading_points[0].keywords == ["方程"]


def test_analyze_question_fenced_json_payload(monkeypatch):
    payload = ('```json\n{"model_answer": "答案", "explanation": "解析", '
               '"scoring_rubric": "细则", "grading_points": '
               '[{"id": 1, "description": "点", "score": 10, "keywords": []}]}\n```')
    g = _grader_with_chat(monkeypatch, payload)
    result = g.analyze_question(PNG, 10.0)
    assert result.ok is True
    assert len(result.grading_points) == 1


def test_analyze_question_invalid_payload_signals_failure(monkeypatch):
    """Garbage AI output must NOT look like success (no silent empty rubric)."""
    g = _grader_with_chat(monkeypatch, "抱歉，我无法识别这张图片。")
    result = g.analyze_question(PNG, 13.0)

    assert isinstance(result, QuestionAnalysis)
    assert result.ok is False, "analyze_question must expose a failure signal"
    assert result.error, "failure must carry an error message"
    assert result.grading_points == []


def test_analyze_question_api_exception_never_raises(monkeypatch):
    g = AIGrader()

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(g, "_chat", boom)
    result = g.analyze_question(PNG, 13.0)  # must not raise

    assert isinstance(result, QuestionAnalysis)
    assert result.ok is False
    assert "network down" in result.error or "RuntimeError" in result.error


# ---------------------------------------------------------------- grade
def _locked_rubric(total_score=13.0):
    return Rubric(
        question_id="q1", platform="zhixue", total_score=total_score,
        version="1.1", locked=True,
        grading_points=[
            GradingPoint(id=1, description="列出方程", score=5.0, keywords=["方程"]),
            GradingPoint(id=2, description="求解并作答", score=8.0, keywords=["结果"]),
        ],
    )


def test_grade_clamps_score_to_max(monkeypatch):
    g = _grader_with_chat(monkeypatch, '{"score": 99, "reasoning": "r", "confidence": "high"}')
    result = g.grade(PNG, _locked_rubric(), 13.0)
    assert isinstance(result, AIGradingResult)
    assert result.score == pytest.approx(13.0)
    assert result.max_score == pytest.approx(13.0)
    assert result.confidence == "high"


@pytest.mark.parametrize("raw_score,expected", [
    (-3, 0.0),
    (8.5, 8.5),
    ("abc", 0.0),
])
def test_grade_score_bounds_and_bad_types(monkeypatch, raw_score, expected):
    payload = json.dumps({"score": raw_score, "confidence": "medium"})
    g = _grader_with_chat(monkeypatch, payload)
    result = g.grade(PNG, _locked_rubric(), 13.0)
    assert result.score == pytest.approx(expected)
    assert 0.0 <= result.score <= 13.0


@pytest.mark.parametrize("raw_conf,expected", [
    ("HIGH", "high"),
    ("  Low ", "low"),
    ("Medium", "medium"),
])
def test_grade_normalizes_confidence(monkeypatch, raw_conf, expected):
    payload = json.dumps({"score": 5, "reasoning": "采分点命中", "confidence": raw_conf})
    g = _grader_with_chat(monkeypatch, payload)
    assert g.grade(PNG, _locked_rubric(), 13.0).confidence == expected


def test_grade_invalid_confidence_falls_back_to_allowed_value(monkeypatch):
    payload = json.dumps({"score": 5, "reasoning": "r", "confidence": "totally-bogus"})
    g = _grader_with_chat(monkeypatch, payload)
    confidence = g.grade(PNG, _locked_rubric(), 13.0).confidence
    assert confidence in {"high", "medium", "low"}, confidence


def test_grade_without_rubric_is_low_confidence(monkeypatch):
    g = _grader_with_chat(monkeypatch, '{"score": 5, "confidence": "high"}')
    result = g.grade(PNG, None, 13.0)
    assert result.confidence == "low"
    assert "未提供评分细则" in result.reasoning


def test_grade_api_exception_sets_error_confidence(monkeypatch):
    g = AIGrader()

    def boom(*args, **kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(g, "_chat", boom)
    result = g.grade(PNG, None, 13.0)  # must not raise

    assert result.confidence == "error"
    assert "rate limited" in result.reasoning
    assert result.score == 0.0


def test_grade_unparseable_reply_sets_error_confidence(monkeypatch):
    g = _grader_with_chat(monkeypatch, "抱歉，无法评分。")
    result = g.grade(PNG, None, 13.0)
    assert result.confidence == "error"
    assert result.score == 0.0


def test_grade_anchors_locked_rubric_in_system_prompt(monkeypatch):
    rubric = Rubric(
        question_id="q1", platform="zhixue", total_score=13.0,
        version="1.1", locked=True,
        grading_points=[
            GradingPoint(id=1, description="列出方程", score=5.0, keywords=["方程"]),
            GradingPoint(id=2, description="求解并作答", score=8.0, keywords=["结果"]),
        ],
    )
    captured = {}

    def fake_chat(system_prompt, user_text, image_data=None, max_tokens=1024):
        captured["system"] = system_prompt
        captured["user"] = user_text
        return '{"score": 8, "confidence": "high"}'

    g = AIGrader()
    monkeypatch.setattr(g, "_chat", fake_chat)
    result = g.grade(PNG, rubric, 13.0)

    assert result.score == pytest.approx(8.0)
    system = captured["system"]
    assert "列出方程" in system
    assert "求解并作答" in system
    assert "1.1" in system           # rubric version anchored
    assert "已锁定" in system
    assert "13.0" in system


def test_chat_uses_correct_mime_type_for_png_and_jpeg(monkeypatch):
    """The data: URL mime must match the real image bytes, not be hardcoded jpeg."""
    for image_bytes, expected_mime in ((PNG, "image/png"), (JPEG, "image/jpeg")):
        g = AIGrader()
        fake = _fake_completions('{"score": 5, "reasoning": "ok", "confidence": "high"}')
        monkeypatch.setattr(g, "_client", fake)

        result = g.grade(image_bytes, _locked_rubric(), 13.0)
        assert result.confidence == "high"

        assert fake.captured, "_chat never reached the fake OpenAI client"
        messages = fake.captured[0]["messages"]
        user_content = messages[1]["content"]
        assert isinstance(user_content, list)
        image_parts = [p for p in user_content if p.get("type") == "image_url"]
        assert image_parts, "image payload missing from chat request"
        url = image_parts[0]["image_url"]["url"]
        assert url.startswith(f"data:{expected_mime};base64,"), (
            f"wrong mime for {expected_mime} input: {url[:40]!r}")
