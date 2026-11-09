"""
评分细则锚定模块（WhisperMark 2.0 核心防幻觉机制）
==================================================
理念（源自 WhisperMark 的 analyze_question + grade 强制复用同一份细则）：
  1. AI 生成结构化评分细则（grading_points）
  2. 教师确认后「锁定」（locked=true）+ 版本号
  3. 批改时每份答卷强制注入同一份已锁定细则，禁止 AI 重写细则
  4. 细则落盘到 data/rubrics/，与对话流解耦，杜绝上下文漂移
"""
import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List

from ..config import config
from ..utils.logging import logger

# 文件名清洗：只保留中英文/数字/下划线/连字符
_SAFE_NAME_RE = re.compile(r"[^\w\u4e00-\u9fff\-]+")


def safe_filename(name: str) -> str:
    """把 question_id 转成安全文件名（保留中文）"""
    safe = _SAFE_NAME_RE.sub("_", str(name or "")).strip("_")
    return safe or "default"


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class GradingPoint:
    """单个采分点"""
    id: int = 0
    description: str = ""          # 采分点描述
    score: float = 0.0             # 该采分点分值
    keywords: List[str] = field(default_factory=list)   # 采分关键词
    deductions: List[dict] = field(default_factory=list)  # [{"condition": ..., "deduct": ...}]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GradingPoint":
        d = d or {}
        keywords = d.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords]
        deductions = d.get("deductions") or []
        if not isinstance(deductions, list):
            deductions = []
        return cls(
            id=int(_as_float(d.get("id", 0), 0)),
            description=str(d.get("description", "")),
            score=_as_float(d.get("score", 0.0)),
            keywords=[str(k) for k in keywords],
            deductions=list(deductions),
        )


@dataclass
class Rubric:
    """评分细则（结构化 + 版本化 + 可锁定）"""
    question_id: str = ""
    platform: str = ""
    total_score: float = 0.0
    standard_answer: str = ""      # 标准答案
    analysis: str = ""             # 解析
    grading_points: List[GradingPoint] = field(default_factory=list)
    raw_text: str = ""             # 原始细则文本（兜底，未结构化时使用）
    version: str = "1.0"
    locked: bool = False
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Rubric":
        d = d or {}
        points = d.get("grading_points") or []
        if not isinstance(points, list):
            points = []
        return cls(
            question_id=str(d.get("question_id", "")),
            platform=str(d.get("platform", "")),
            total_score=_as_float(d.get("total_score", 0.0)),
            standard_answer=str(d.get("standard_answer", "")),
            analysis=str(d.get("analysis", "")),
            grading_points=[GradingPoint.from_dict(p) for p in points if isinstance(p, dict)],
            raw_text=str(d.get("raw_text", "")),
            version=str(d.get("version", "1.0")),
            locked=bool(d.get("locked", False)),
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
        )

    @property
    def total_points_score(self) -> float:
        """各采分点分值之和（用于校验）"""
        return round(sum(p.score for p in self.grading_points), 2)

    def validate(self) -> List[str]:
        """校验细则自洽性，返回警告列表（不抛异常）"""
        warnings: List[str] = []
        if self.total_score <= 0:
            warnings.append("细则满分未设置或为 0")
        if not self.grading_points:
            if not self.raw_text.strip():
                warnings.append("细则既没有结构化采分点，也没有原始细则文本")
            return warnings
        for p in self.grading_points:
            if p.score < 0:
                warnings.append(f"采分点 {p.id} 分值为负数：{p.score}")
            if not p.description.strip():
                warnings.append(f"采分点 {p.id} 缺少描述")
        diff = round(self.total_points_score - self.total_score, 2)
        if self.total_score > 0 and abs(diff) > 0.01:
            warnings.append(
                f"采分点分值之和 {self.total_points_score} 与满分 {self.total_score} 不一致（差 {diff:+g}）")
        return warnings

    def to_markdown(self) -> str:
        """导出为独立 MD 文件内容（供教师审阅/存档，防幻觉锚点）"""
        lines = [
            f"# 评分细则（{self.question_id}）",
            "",
            f"- 平台：`{self.platform}`",
            f"- 满分：`{self.total_score}` 分",
            f"- 版本：`{self.version}`",
            f"- 状态：`{'已锁定' if self.locked else '待确认'}`",
            f"- 更新时间：`{self.updated_at or '—'}`",
            "",
            "## 标准答案",
            "",
            self.standard_answer or "（未提供）",
            "",
            "## 解析",
            "",
            self.analysis or "（未提供）",
            "",
            "## 采分点",
            "",
        ]
        if self.grading_points:
            lines.append("| # | 采分点 | 分值 | 采分关键词 |")
            lines.append("|---|--------|------|------------|")
            for p in self.grading_points:
                desc = (p.description or "").replace("|", "\\|")
                kw = "、".join(p.keywords) if p.keywords else "—"
                lines.append(f"| {p.id} | {desc} | {p.score} | {kw} |")
            lines.append("")
            if abs(self.total_points_score - self.total_score) > 0.01:
                lines.append(f"> ⚠️ 采分点合计 {self.total_points_score} 分，与满分 {self.total_score} 分不一致，请教师核对。")
                lines.append("")
        else:
            lines.append("（未结构化）")
            lines.append("")
            lines.append("## 原始细则")
            lines.append("")
            lines.append(self.raw_text or "（无）")
            lines.append("")
        return "\n".join(lines)

    def to_system_prompt(self) -> str:
        """生成注入批改 system prompt 的细则文本（锚定）"""
        if self.grading_points:
            parts = [f"评分细则（版本 {self.version}，唯一评分依据，满分 {self.total_score} 分）："]
            for p in self.grading_points:
                line = f"{p.id}. {p.description}（{p.score} 分）"
                if p.keywords:
                    line += f"，采分关键词：{'、'.join(p.keywords)}"
                parts.append(line)
            return "\n".join(parts)
        return self.raw_text or "（未提供评分细则）"


class RubricManager:
    """评分细则管理器：生成 / 确认锁定 / 落盘 / 加载"""

    def __init__(self, rubric_dir: Optional[Path] = None):
        self._dir: Path = Path(rubric_dir) if rubric_dir else config.RUBRIC_DIR
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    @property
    def directory(self) -> Path:
        return self._dir

    def _file_path(self, question_id: str) -> Path:
        return self._dir / f"{safe_filename(question_id)}.json"

    def _md_file_path(self, question_id: str) -> Path:
        return self._dir / f"{safe_filename(question_id)}.md"

    def save(self, rubric: Rubric) -> Path:
        """落盘 JSON（机器读取锚点），并同步导出独立 MD"""
        if not rubric.created_at:
            rubric.created_at = time.strftime("%Y-%m-%d %H:%M:%S")
        rubric.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        path = self._file_path(rubric.question_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rubric.to_dict(), ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except OSError as e:
            logger.error("评分细则保存失败 %s: %s", path, e)
            raise
        self.export_markdown(rubric)
        logger.info("评分细则已保存: %s", path)
        return path

    def export_markdown(self, rubric: Rubric) -> Path:
        """导出独立 MD 文件"""
        path = self._md_file_path(rubric.question_id)
        path.write_text(rubric.to_markdown(), encoding="utf-8")
        return path

    def load(self, question_id: str) -> Optional[Rubric]:
        """加载已有细则"""
        path = self._file_path(question_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            return Rubric.from_dict(data)
        except Exception as e:
            logger.warning("加载细则失败 %s: %s", path, e)
            return None

    def lock(self, rubric: Rubric) -> Rubric:
        """教师确认后锁定：locked=true，版本号 +0.1"""
        if rubric.locked:
            return rubric
        try:
            cur = float(rubric.version)
            rubric.version = f"{cur + 0.1:.1f}"
        except ValueError:
            rubric.version = f"{rubric.version}.1"
        rubric.locked = True
        self.save(rubric)
        logger.info("评分细则已锁定: 版本 %s", rubric.version)
        return rubric

    def build_rubric_from_raw(self, question_id: str, platform: str,
                              total_score: float, standard_answer: str,
                              analysis: str, raw_rubric: str,
                              grading_points: Optional[List[GradingPoint]] = None) -> Rubric:
        """从 AI 结果构建 Rubric 对象（未锁定）"""
        return Rubric(
            question_id=question_id,
            platform=platform,
            total_score=_as_float(total_score),
            standard_answer=standard_answer or "",
            analysis=analysis or "",
            grading_points=grading_points or [],
            raw_text=raw_rubric or "",
            version="1.0",
            locked=False,
        )

    def list_rubrics(self) -> List[Rubric]:
        """列出所有已保存的细则（按更新时间倒序）"""
        out: List[Rubric] = []
        try:
            files = sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            files = []
        for f in files:
            r = self.load(f.stem)
            if r:
                out.append(r)
        return out


# 全局实例
rubric_manager = RubricManager()
