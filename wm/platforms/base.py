"""
平台适配器抽象基类（WhisperMark 2.0）
==================================================
将 AI-Marker-Suite 的 JS 适配器接口（adapter-interface.js）翻译为
Playwright 驱动的 Python 接口。每个平台适配器继承本基类，实现平台特定
的 DOM 交互（答题卡定位、填分、提交、等待下一份）。

对应关系（JS → Python）：
  gatherAnswerImages   → gather_answer_images() -> list[str]
  fetchImageAsBase64   → download_image(url) -> bytes
  getScoreInputs       → get_score_inputs() -> list[ScoreInput]
  fillScores           → fill_scores(scores) -> bool
  submitGrade          → submit_grade() -> bool
  waitForNextPaper     → wait_for_next_paper(old_url) -> bool
  isRegradeMode        → is_regrade_mode() -> bool
"""
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional, List

try:  # playwright 是可选依赖：未安装时模块仍可导入（check-env / demo 可用）
    from playwright.sync_api import Frame, ElementHandle
except ImportError:  # pragma: no cover
    Frame = Any  # type: ignore
    ElementHandle = Any  # type: ignore

from ..browser import BrowserManager
from ..config import config
from ..utils.image_utils import auto_rotate_image
from ..utils.logging import logger

# 用 native setter 触发 Vue/React/Element Plus 响应式（关键！）
_SET_VALUE_JS = """
(el, v) => {
    const proto = el instanceof HTMLInputElement
        ? HTMLInputElement.prototype
        : HTMLTextAreaElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, String(v));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
}
"""


@dataclass
class ScoreInput:
    """分数输入框信息"""
    label: str = ""          # 如 "第1题"、"总分"
    index: int = 0
    max_score: float = 0.0


class PlatformAdapter(ABC):
    """平台适配器抽象基类"""

    # 子类必须覆盖
    name: str = "未命名平台"
    id: str = "unknown"
    url_patterns: List[str] = []
    login_url: str = ""

    def __init__(self, browser: BrowserManager):
        self.browser = browser

    # ---------- 生命周期 ----------
    def should_initialize(self) -> bool:
        """轻量级 URL 检查，判断当前页面是否属于本平台"""
        try:
            host = self.browser.page.url.lower()
            for pat in self.url_patterns:
                # 简单匹配：去掉协议和通配符后做包含判断
                domain = pat.replace("http://", "").replace("https://", "").replace("*://", "")
                domain = domain.split("/")[0].replace("*.", "")
                if domain and domain in host:
                    return True
            return False
        except Exception:
            return False

    def is_marking_page(self) -> bool:
        """快速页面检查（不等待 DOM），供 URL 变化监听"""
        return self.detect_marking_page()

    @abstractmethod
    def detect_marking_page(self) -> bool:
        """检测当前页面是否为批改页面（含答题卡图片和分数输入框）"""

    @abstractmethod
    def get_task_identifier(self) -> str:
        """返回当前题目唯一标识（用于细则绑定与方案切换）"""

    # ---------- 图片获取 ----------
    @abstractmethod
    def gather_answer_images(self) -> List[str]:
        """获取当前答卷所有答题卡图片的 src 列表"""

    def download_image(self, url: str) -> Optional[bytes]:
        """下载图片（带页面 cookie），并自动校正方向"""
        try:
            resp = self.browser.page.request.get(url)
            if resp.ok and len(resp.body()) > 500:
                return auto_rotate_image(resp.body())
        except Exception:
            pass
        return None

    def download_images(self, urls: List[str]) -> List[bytes]:
        """批量下载，返回非空数据列表"""
        out = []
        for u in urls:
            data = self.download_image(u)
            if data:
                out.append(data)
        return out

    # ---------- 分数输入 ----------
    @abstractmethod
    def get_score_inputs(self) -> List[ScoreInput]:
        """返回当前页面所有分数输入框信息"""

    def fill_scores(self, scores: List[float]) -> bool:
        """
        按顺序填入分数。基类提供默认实现：通过 get_score_inputs 定位
        输入框并用 native setter 填入。子类可覆盖。
        """
        inputs = self.get_score_inputs()
        if not inputs:
            return False
        success = 0
        for i in range(min(len(scores), len(inputs))):
            if scores[i] is None:
                continue
            el = self._find_input_element(inputs[i].index)
            if el and self._set_value(el, scores[i]):
                success += 1
        return success > 0

    def _set_value(self, element: ElementHandle, value) -> bool:
        """用 native setter 填值并触发事件（绕过 Vue/React 响应式）"""
        try:
            element.evaluate(_SET_VALUE_JS, value)
            return True
        except Exception:
            # 回退：普通 fill
            try:
                element.fill(str(value))
                return True
            except Exception:
                return False

    def _find_input_element(self, index: int) -> Optional[ElementHandle]:
        """子类可覆盖：根据 index 定位输入框元素"""
        raise NotImplementedError

    # ---------- 提交 ----------
    @abstractmethod
    def submit_grade(self) -> bool:
        """点击"提交分数"按钮"""

    @abstractmethod
    def wait_for_next_paper(self, old_image_url: str = "") -> bool:
        """提交后等待下一份答卷加载完成"""

    # ---------- 状态 ----------
    @abstractmethod
    def is_regrade_mode(self) -> bool:
        """当前是否处于回评/复核模式"""

    def get_current_student_name(self) -> str:
        """尝试获取当前学生姓名（可选实现）"""
        return ""

    # ---------- 工具 ----------
    def _frames(self) -> List[Frame]:
        """获取所有可操作的 frame（主页面 + iframe）"""
        frames = [self.browser.page]
        try:
            for frame in self.browser.page.frames:
                if frame != self.browser.page.main_frame:
                    frames.append(frame)
        except Exception:
            pass
        return frames

    def _first_in_frames(self, selector: str) -> Optional[ElementHandle]:
        """在所有 frame 中查找第一个匹配元素"""
        for frame in self._frames():
            try:
                el = frame.query_selector(selector)
                if el:
                    return el
            except Exception:
                continue
        return None

    def _all_in_frames(self, selector: str) -> List[ElementHandle]:
        """在所有 frame 中查找所有匹配元素"""
        out = []
        for frame in self._frames():
            try:
                out.extend(frame.query_selector_all(selector))
            except Exception:
                continue
        return out
