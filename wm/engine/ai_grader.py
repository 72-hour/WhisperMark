"""
AI 阅卷模块（WhisperMark 2.0）
==================================================
1. analyze_question：分析题干图片，生成标准答案 + 解析 + 结构化评分细则（grading_points）
2. grade：基于已锁定细则（Rubric）对学生答卷评分，强制锚定同一份细则，防幻觉
支持任意 OpenAI 兼容接口（DeepSeek / 5+1 AI / 火山引擎 / 硅基流动 等）

容错原则：
- analyze_question 失败时返回 ok=False + error，绝不把错误文本伪装成「标准答案」；
- grade 失败时返回 confidence="error"，由引擎跳过该份并记录异常，不中断整批。
"""
import base64
import json
import re
from typing import Optional, List
from dataclasses import dataclass, field

from ..config import config
from ..utils.logging import logger
from .rubric import GradingPoint


@dataclass
class QuestionAnalysis:
    """题干分析结果"""
    model_answer: str = ""
    explanation: str = ""
    scoring_rubric: str = ""                       # 原始细则文本
    grading_points: List[GradingPoint] = field(default_factory=list)  # 结构化采分点
    max_score: float = 0.0
    raw_response: str = ""
    ok: bool = False                               # 是否成功解析出可用结果
    error: str = ""                                # 失败原因（ok=False 时）
    warnings: List[str] = field(default_factory=list)


@dataclass
class AIGradingResult:
    """AI 阅卷结果"""
    score: float = 0.0
    max_score: float = 0.0
    reasoning: str = ""
    details: str = ""
    confidence: str = ""       # high / medium / low / error
    raw_response: str = ""

    @property
    def is_error(self) -> bool:
        return self.confidence == "error"


_VALID_CONFIDENCE = ("high", "medium", "low")


def _guess_mime(image_data: bytes) -> str:
    """从文件头猜测图片 MIME，避免把 PNG 当作 jpeg 传给多模态模型"""
    try:
        head = image_data[:16]
    except Exception:
        return "image/jpeg"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"%PDF"):
        return "application/pdf"
    return "image/jpeg"


class AIGrader:
    """AI 阅卷器（OpenAI 兼容接口）"""

    def __init__(self):
        self._client = None

    # ---------- 基础设施 ----------
    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("请先安装 openai: pip install openai")
        if not config.AI_API_KEY:
            raise RuntimeError("未配置 AI API Key，请设置环境变量 WM_AI_API_KEY 或 AI_API_KEY")
        self._client = OpenAI(api_key=config.AI_API_KEY, base_url=config.AI_BASE_URL)
        return self._client

    def reset_client(self) -> None:
        """配置变更后重建 client（测试/热更新用）"""
        self._client = None

    def _chat(self, system_prompt: str, user_text: str,
              image_data: Optional[bytes] = None,
              max_tokens: int = 1024) -> str:
        """统一调用，返回文本内容"""
        client = self._get_client()
        messages = [{"role": "system", "content": system_prompt}]
        if image_data:
            img_b64 = base64.b64encode(image_data).decode("utf-8")
            mime = _guess_mime(image_data)
            content = [
                {"type": "text", "text": user_text},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
            ]
        else:
            content = user_text
        messages.append({"role": "user", "content": content})

        response = client.chat.completions.create(
            model=config.AI_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=config.AI_TEMPERATURE,
        )
        msg = response.choices[0].message
        raw = getattr(msg, "content", None)
        if not raw:
            # 兼容部分推理模型的字段命名
            raw = (getattr(msg, "reasoning_content", None)
                   or getattr(msg, "text", None)
                   or getattr(msg, "reasoning", None) or "")
        return (raw or "").strip()

    # ---------- 题干分析 ----------
    def analyze_question(self, image_data: bytes, max_score: float) -> QuestionAnalysis:
        """分析题干图片，生成标准答案、解析、结构化评分细则"""
        result = QuestionAnalysis(max_score=float(max_score or 0))

        system_prompt = f"""你是一位经验丰富的中学教师。请根据题目图片，生成以下内容（满分 {max_score} 分）：

你的回复必须且只能是一个 JSON 对象，格式如下：
{{
  "model_answer": "标准答案，包含完整解题步骤",
  "explanation": "解析，说明解题思路和方法",
  "scoring_rubric": "评分细则的完整文字描述",
  "grading_points": [
    {{"id": 1, "description": "采分点描述", "score": 分值数字, "keywords": ["采分关键词1", "关键词2"]}},
    {{"id": 2, "description": "采分点描述", "score": 分值数字, "keywords": ["关键词"]}}
  ]
}}

要求：
1. grading_points 中所有采分点的 score 之和必须等于 {max_score} 分。
2. 每个采分点要具体、可判定，避免主观表述。
3. 只输出 JSON，不要输出任何其他内容。"""

        try:
            raw = self._chat(system_prompt, "请分析这道题目，生成标准答案、解析和结构化评分细则。",
                             image_data=image_data,
                             max_tokens=config.AI_MAX_TOKENS_ANALYZE)
        except Exception as e:
            result.error = f"AI 调用失败：{type(e).__name__}: {e}"
            logger.error(result.error)
            return result

        result.raw_response = raw
        data = self._extract_json(raw)
        if not isinstance(data, dict):
            result.error = "AI 返回内容无法解析为 JSON"
            return result

        result.model_answer = str(data.get("model_answer", "") or "").strip()
        result.explanation = str(data.get("explanation", "") or "").strip()
        result.scoring_rubric = str(data.get("scoring_rubric", "") or "").strip()

        gp_list = data.get("grading_points", [])
        if isinstance(gp_list, list):
            for idx, gp in enumerate(gp_list):
                if not isinstance(gp, dict):
                    continue
                try:
                    result.grading_points.append(GradingPoint(
                        id=int(gp.get("id", idx + 1) or idx + 1),
                        description=str(gp.get("description", "") or ""),
                        score=float(gp.get("score", 0) or 0),
                        keywords=[str(k) for k in (gp.get("keywords") or [])],
                        deductions=list(gp.get("deductions") or []),
                    ))
                except (ValueError, TypeError):
                    continue

        if not (result.model_answer or result.scoring_rubric or result.grading_points):
            result.error = "AI 返回的 JSON 缺少标准答案/评分细则内容"
            return result

        # 细则自洽性校验（只警告，便于教师确认）
        if result.grading_points and result.max_score > 0:
            total = round(sum(p.score for p in result.grading_points), 2)
            if abs(total - result.max_score) > 0.01:
                result.warnings.append(
                    f"采分点分值之和 {total} 与满分 {result.max_score} 不一致，请教师核对")
        if not result.grading_points and not result.scoring_rubric:
            result.warnings.append("未生成结构化采分点，将使用原始细则文本锚定")

        result.ok = True
        return result

    # ---------- 评分 ----------
    def grade(self, image_data: bytes, rubric, max_score: float) -> AIGradingResult:
        """
        对学生答卷评分，强制锚定已锁定细则。
        rubric: Rubric 对象（或 None，此时细则缺失，强制低置信度）
        """
        max_score = float(max_score or 0)
        result = AIGradingResult(max_score=max_score)
        if max_score <= 0:
            result.reasoning = "满分无效（<=0），无法评分"
            result.confidence = "error"
            return result

        rubric_text = rubric.to_system_prompt() if rubric else "（未提供评分细则）"
        locked_note = "（已锁定，唯一评分依据）" if (rubric and rubric.locked) else "（未锁定）"

        system_prompt = f"""你是一位经验丰富的中学教师，正在批改学生试卷。满分：{max_score} 分。

评分细则{locked_note}：
{rubric_text}

【评分规则——必须严格遵守】
1. 上面评分细则中的每一条都是采分点，你只能按这些采分点逐项给分。
2. 严禁凭空编造评分细则里没有的采分点、步骤或分值；严禁凭常识或印象加分/扣分。
3. 学生答案与评分细则逐点对照：命中某采分点给该点分，未命中扣该点分。
4. 最终给分 = 各采分点得分之和，必须在 0 到 {max_score} 之间。
5. 若评分细则缺失，不得猜测，confidence 置为 "low" 并在 reasoning 中说明"未提供评分细则"。

【重要】你的回复必须且只能是一个 JSON 对象，格式如下：
{{"score": 8.5, "reasoning": "评分理由（逐采分点说明给/扣分）", "details": "详细点评", "confidence": "high"}}

score: 数字，0到{max_score}之间
confidence: "high"、"medium" 或 "low"
只输出 JSON，不要输出其他任何内容。"""

        try:
            raw = self._chat(system_prompt, f"请批改这份学生答卷（满分{max_score}分），只输出JSON。",
                             image_data=image_data,
                             max_tokens=config.AI_MAX_TOKENS_GRADE)
        except Exception as e:
            result.reasoning = f"AI 调用失败: {type(e).__name__}: {e}"
            result.confidence = "error"
            logger.error(result.reasoning)
            return result

        result.raw_response = raw
        data = self._extract_json(raw)
        if not isinstance(data, dict):
            result.reasoning = f"AI 返回无法解析为 JSON：{raw[:200]}"
            result.confidence = "error"
            return result

        # 分数：支持 "8.5" / "8.5分" / 8.5
        raw_score = data.get("score", 0)
        score = 0.0
        try:
            if isinstance(raw_score, str):
                m = re.search(r"-?\d+(?:\.\d+)?", raw_score)
                score = float(m.group(0)) if m else 0.0
            else:
                score = float(raw_score)
        except (TypeError, ValueError):
            score = 0.0
        result.score = max(0.0, min(round(score, 2), max_score))

        result.reasoning = str(data.get("reasoning", "") or "").strip()
        result.details = str(data.get("details", "") or "").strip()

        confidence = str(data.get("confidence", "") or "").strip().lower()
        if confidence not in _VALID_CONFIDENCE:
            confidence = "medium"
        if rubric is None:
            confidence = "low"
            note = "未提供评分细则，评分仅供参考"
            result.reasoning = f"{note}。{result.reasoning}".strip("。 ")
        result.confidence = confidence

        # 证据链校验：解析出分数但没有任何理由，降低置信度便于教师复核
        if not result.reasoning:
            result.reasoning = "（AI 未给出评分理由）"
            if result.confidence == "high":
                result.confidence = "medium"

        return result

    # ---------- JSON 解析 ----------
    @staticmethod
    def _try_parse(text: str) -> Optional[dict]:
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
        return obj if isinstance(obj, dict) else None

    def _extract_json(self, text: str) -> Optional[dict]:
        """从 AI 回复中提取 JSON 对象，容错：代码块 / 前后废话 / 多个对象 / 非法 JSON"""
        if not text or not isinstance(text, str):
            return None
        stripped = text.strip()

        # 1) ```json ... ``` 代码块
        for m in re.finditer(r"```(?:json|JSON)?\s*(.*?)```", stripped, re.DOTALL):
            obj = self._try_parse(m.group(1).strip())
            if obj is not None:
                return obj

        # 2) 整段就是 JSON
        obj = self._try_parse(stripped)
        if obj is not None:
            return obj

        # 3) 从任意 '{' 开始尝试 raw_decode（兼容前后有解释文字 / 多个对象）
        decoder = json.JSONDecoder()
        for i, ch in enumerate(stripped):
            if ch != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(stripped, i)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(candidate, dict):
                return candidate

        # 4) 最后兜底：只正则提取分数（保证极端情况下不崩，置信度 medium）
        patterns = [
            r'"score"\s*:\s*"?(-?\d+(?:\.\d+)?)"?',
            r'(?:score|分数|得分|给分|建议分数)[:：\s]*(-?\d+(?:\.\d+)?)',
            r'(-?\d+(?:\.\d+)?)\s*[/／]\s*\d+\s*分',
            r'(-?\d+(?:\.\d+)?)\s*分',
        ]
        for pat in patterns:
            m = re.search(pat, stripped, re.IGNORECASE)
            if m:
                try:
                    value = float(m.group(1))
                except (TypeError, ValueError):
                    continue
                return {"score": value, "reasoning": stripped[:300],
                        "details": "", "confidence": "medium"}
        return None


# 全局实例
ai_grader = AIGrader()
