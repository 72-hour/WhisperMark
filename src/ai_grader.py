"""
AI 阅卷模块
1. 分析题干图片，生成答案、解析、评分细则
2. 对比学生答卷和评分细则，给出建议分数
支持 OpenAI 兼容接口
"""
import base64
import json
import re
from typing import Optional
from dataclasses import dataclass

from .config import config


@dataclass
class QuestionAnalysis:
    """题干分析结果"""
    model_answer: str = ""       # 标准答案
    explanation: str = ""        # 解析
    scoring_rubric: str = ""     # 评分细则
    max_score: float = 13.0      # 满分
    raw_response: str = ""


@dataclass
class AIGradingResult:
    """AI 阅卷结果"""
    score: float = 0.0
    max_score: float = 13.0
    reasoning: str = ""      # 评分理由
    details: str = ""        # 详细点评
    confidence: str = ""     # 置信度: high/medium/low
    raw_response: str = ""


class AIGrader:
    """AI 阅卷器"""

    def __init__(self):
        self._client = None
        self.question_analysis: Optional[QuestionAnalysis] = None

    def _get_client(self):
        """懒加载 OpenAI 客户端"""
        if self._client is not None:
            return self._client

        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("请先安装 openai: pip install openai")

        if not config.AI_API_KEY:
            raise RuntimeError("未配置 AI API Key，请在 config.py 中配置")

        self._client = OpenAI(
            api_key=config.AI_API_KEY,
            base_url=config.AI_BASE_URL,
        )
        return self._client

    def analyze_question(self, image_data: bytes, max_score: float = 13.0) -> QuestionAnalysis:
        """
        分析题干图片，生成标准答案、解析、评分细则

        Args:
            image_data: 题干图片二进制数据
            max_score: 满分

        Returns:
            QuestionAnalysis
        """
        result = QuestionAnalysis(max_score=max_score)
        img_b64 = base64.b64encode(image_data).decode('utf-8')

        system_prompt = f"""你是一位经验丰富的中学教师。
请根据题目图片，生成以下内容（满分 {max_score} 分）：
1. 标准答案（完整解题过程）
2. 解析（解题思路说明）
3. 评分细则（每个步骤/采分点的分值分配，要详细具体）

你的回复必须且只能包含以下 JSON 格式（不要输出其他内容）：

{{
  "model_answer": "标准答案，包含完整解题步骤",
  "explanation": "解析，说明解题思路和方法",
  "scoring_rubric": "评分细则，例如：\\n第一步xxx（2分）\\n第二步xxx（3分）\\n..."
}}"""

        user_prompt = "请分析这道题目，生成标准答案、解析和评分细则。"

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=config.AI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                        ]
                    }
                ],
                max_tokens=2048,
                temperature=0.1,
            )

            try:
                msg = response.choices[0].message
                raw = msg.content
                if not raw:
                    raw = getattr(msg, 'reasoning_content', None) or \
                          getattr(msg, 'text', None) or \
                          getattr(msg, 'reasoning', None) or ""
                if not raw:
                    raw = f"[空] msg={msg} | finish_reason={response.choices[0].finish_reason}"
                raw = raw.strip()
            except (AttributeError, IndexError) as e:
                raw = f"[异常] {type(e).__name__}: {e}"

            result.raw_response = raw

            data = self._extract_json(raw)
            if data:
                result.model_answer = str(data.get('model_answer', ''))
                result.explanation = str(data.get('explanation', ''))
                result.scoring_rubric = str(data.get('scoring_rubric', ''))
            else:
                result.model_answer = f"AI 返回的内容无法解析为JSON:\n{raw[:500]}"

        except Exception as e:
            result.model_answer = f"AI 调用失败: {type(e).__name__}: {e}"

        self.question_analysis = result
        return result

    def grade(
        self,
        image_data: bytes,
        max_score: float = 13.0,
        scoring_standard: str = "",
        question_info: str = "",
    ) -> AIGradingResult:
        """
        对学生答题图片进行 AI 评分
        自动使用已分析的题干信息
        """
        result = AIGradingResult(max_score=max_score)
        img_b64 = base64.b64encode(image_data).decode('utf-8')

        # 优先使用已分析的评分细则
        rubric = scoring_standard
        answer_text = question_info

        if self.question_analysis:
            if not rubric and self.question_analysis.scoring_rubric:
                rubric = self.question_analysis.scoring_rubric
            if not answer_text and self.question_analysis.model_answer:
                answer_text = f"标准答案：{self.question_analysis.model_answer}"

        if not rubric:
            rubric = "（未提供评分细则）"

        system_prompt = f"""你是一位经验丰富的中学教师，正在批改学生试卷。
满分：{max_score} 分
"""
        if answer_text:
            system_prompt += f"\n{answer_text}\n"
        system_prompt += f"\n评分细则（唯一评分依据）：\n{rubric}\n"

        system_prompt += f"""
【评分规则——必须严格遵守】
1. 上面评分细则中的每一条都是采分点，你只能按这些采分点逐项给分。
2. 严禁凭空编造评分细则里没有的采分点、步骤或分值；严禁凭常识或印象加分/扣分。
3. 学生答案与评分细则逐点对照：命中某采分点给该点分，未命中扣该点分。
4. 最终给分 = 各采分点得分之和，必须在 0 到 {max_score} 之间。
5. 若评分细则缺失，不得猜测，confidence 置为 "low" 并在 reasoning 中说明“未提供评分细则”。

【重要】你的回复必须且只能是一个JSON对象，不要输出任何其他文字、分析或解释！
格式如下：
{{"score": 8.5, "reasoning": "评分理由（逐采分点说明给/扣分）", "details": "详细点评", "confidence": "high"}}

score: 数字，0到{max_score}之间
confidence: "high"或"medium"或"low"

再次强调：只输出JSON，不要输出其他任何内容！"""

        user_prompt = f"请批改这份学生答卷（满分{max_score}分），只输出JSON。"

        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=config.AI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                        ]
                    }
                ],
                max_tokens=1024,
                temperature=0.1,
            )

            try:
                msg = response.choices[0].message
                raw = msg.content
                if not raw:
                    # 尝试其他字段
                    raw = getattr(msg, 'reasoning_content', None) or \
                          getattr(msg, 'text', None) or \
                          getattr(msg, 'reasoning', None) or ""
                if not raw:
                    # 打印完整对象调试
                    raw = f"[空] msg={msg} | finish_reason={response.choices[0].finish_reason}"
                raw = raw.strip()
            except (AttributeError, IndexError) as e:
                raw = f"[异常] {type(e).__name__}: {e}\nresponse={response}"

            result.raw_response = raw

            data = self._extract_json(raw)
            if data:
                result.score = min(float(data.get('score', 0)), max_score)
                result.score = max(result.score, 0)
                result.reasoning = str(data.get('reasoning', ''))
                result.details = str(data.get('details', ''))
                result.confidence = str(data.get('confidence', 'medium'))
            else:
                result.reasoning = f"AI 返回的内容无法解析为JSON"
                result.confidence = "error"

        except Exception as e:
            result.reasoning = f"AI 调用失败: {type(e).__name__}: {e}"
            result.confidence = "error"

        return result

    def _extract_json(self, text: str) -> Optional[dict]:
        """从 AI 回复中提取 JSON，容错处理"""
        if not text:
            return None

        json_str = text

        # 去掉 ```json ``` 包裹
        if '```' in json_str:
            match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', json_str, re.DOTALL)
            if match:
                json_str = match.group(1).strip()

        # 提取 { } 块
        if '{' in json_str and '}' in json_str:
            start = json_str.index('{')
            end = json_str.rindex('}') + 1
            json_str = json_str[start:end]

        try:
            return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            pass

        # 兜底：尝试从文本中提取分数
        # 匹配 "score": 8.5 或 分数: 8.5 或 得分: 8.5 或 8.5分
        patterns = [
            r'"score"\s*:\s*(\d+\.?\d*)',
            r'(?:score|分数|得分|给分|建议分数|建议给分)[:\s]*(\d+\.?\d*)',
            r'(\d+\.?\d*)\s*[/／]\s*\d+\s*分',
            r'(\d+\.?\d*)\s*分',
        ]
        for pat in patterns:
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                return {
                    "score": float(match.group(1)),
                    "reasoning": text[:300],
                    "details": "",
                    "confidence": "medium"
                }

        return None


# 全局实例
ai_grader = AIGrader()
