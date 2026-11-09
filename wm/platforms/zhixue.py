"""
智学网平台适配器（翻译自 AI-Marker-Suite src/adapters/zhixue/）
==================================================
特征：
  - 新版微前端架构：iframe 内渲染阅卷页面
  - 旧版：主页面 /webmarking/ 路径
选择器（对应 JS 版 ZHIXUE_SELECTORS）：
  答题卡：.enhance-definition-bright（新） / div[name="topicImg"] img（旧）
  分数框：input[name="topicTxt"]（小题） / #txt_marking_all（总分新） / input[placeholder*="分"]（总分旧）
  提交  ：#bnt_save（新） / 按钮文字"提交分数"（旧）
"""
import time
from typing import List, Optional
from urllib.parse import unquote

try:  # playwright 可选依赖
    from playwright.sync_api import Frame, ElementHandle
except ImportError:  # pragma: no cover
    from typing import Any
    Frame = Any  # type: ignore
    ElementHandle = Any  # type: ignore

from .base import PlatformAdapter, ScoreInput
from ..utils.logging import logger


class ZhixueAdapter(PlatformAdapter):
    name = "智学网"
    id = "zhixue"
    url_patterns = ["*://www.zhixue.com/*", "*://zhixue.com/*", "*://*.zhixue.com/*"]
    login_url = "https://www.zhixue.com/login.html"

    SEL_IMG_NEW = ".enhance-definition-bright"
    SEL_IMG_OLD = 'div[name="topicImg"] img'
    SEL_INPUT_SUB = 'input[name="topicTxt"]'
    SEL_INPUT_TOTAL_NEW = "#txt_marking_all"
    SEL_INPUT_TOTAL_OLD = 'input[placeholder*="分"]'
    SEL_SUBMIT_NEW = "#bnt_save"
    SEL_SUBMIT_TEXT = "提交分数"

    def __init__(self, browser):
        super().__init__(browser)
        self._cached_input_elements: List[ElementHandle] = []

    # ---------- URL 判断 ----------
    def _is_marking_url(self) -> bool:
        """判断当前 URL 是否为阅卷页面（支持新版 SPA app-N 参数双重编码）"""
        try:
            info = self.browser.page.evaluate("""
                () => {
                    const pathname = window.location.pathname;
                    const params = new URLSearchParams(window.location.search);
                    let appParam = null;
                    for (const [k, v] of params.entries()) {
                        if (/^app-\\d+$/.test(k)) { appParam = v; break; }
                    }
                    return { pathname, appParam };
                }
            """)
        except Exception:
            return False

        if "/webmarking/" in (info.get("pathname") or ""):
            return True
        app = info.get("appParam")
        if app:
            try:
                decoded = unquote(unquote(app))
                return "/webmarking/" in decoded
            except Exception:
                return False
        return False

    # ---------- 定位阅卷 iframe ----------
    def _marking_frame(self) -> Frame:
        """返回包含阅卷元素的 frame（新版在 iframe 内）"""
        for frame in self.browser.page.frames:
            if frame == self.browser.page.main_frame:
                continue
            try:
                if (frame.query_selector(self.SEL_IMG_NEW)
                        or frame.query_selector(self.SEL_INPUT_SUB)
                        or frame.query_selector(self.SEL_SUBMIT_NEW)):
                    return frame
            except Exception:
                continue
        return self.browser.page.main_frame

    # ---------- 生命周期 ----------
    def is_marking_page(self) -> bool:
        return self._is_marking_url()

    def detect_marking_page(self) -> bool:
        if not self._is_marking_url():
            return False
        frame = self._marking_frame()
        for _ in range(10):  # 最多等 10 秒
            try:
                has_input = bool(frame.query_selector(self.SEL_INPUT_SUB)
                                 or frame.query_selector(self.SEL_INPUT_TOTAL_NEW)
                                 or frame.query_selector(self.SEL_INPUT_TOTAL_OLD))
                has_btn = bool(frame.query_selector(self.SEL_SUBMIT_NEW)) or \
                    self._has_text_button(frame, self.SEL_SUBMIT_TEXT)
                if has_input and has_btn:
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def _has_text_button(self, frame: Frame, text: str) -> bool:
        try:
            btns = frame.query_selector_all("button")
            for b in btns:
                if text in (b.inner_text() or ""):
                    return True
        except Exception:
            pass
        return False

    def get_task_identifier(self) -> str:
        base = self.browser.page.url.split("&_t=")[0]
        return base

    # ---------- 图片 ----------
    def gather_answer_images(self) -> List[str]:
        frame = self._marking_frame()
        urls: List[str] = []
        for sel in (self.SEL_IMG_NEW, self.SEL_IMG_OLD):
            try:
                imgs = frame.query_selector_all(sel)
                for img in imgs:
                    src = img.get_attribute("src") or ""
                    if src.startswith("http"):
                        urls.append(src)
            except Exception:
                continue
            if urls:
                break
        return list(dict.fromkeys(urls))

    # ---------- 分数输入 ----------
    def get_score_inputs(self) -> List[ScoreInput]:
        self._cached_input_elements = []
        frame = self._marking_frame()
        inputs: List[ScoreInput] = []

        # 分小题（多个 input[name="topicTxt"]）
        try:
            subs = frame.query_selector_all(self.SEL_INPUT_SUB)
            if len(subs) > 1:
                for i, el in enumerate(subs):
                    label = self._sub_label(el, i)
                    ms = self._max_score_from_el(el)
                    inputs.append(ScoreInput(label=label, index=i, max_score=ms))
                    self._cached_input_elements.append(el)
                return inputs
        except Exception:
            pass

        # 总分（新版 #txt_marking_all）
        try:
            el = frame.query_selector(self.SEL_INPUT_TOTAL_NEW)
            if el:
                ms = self._max_score_from_el(el)
                inputs.append(ScoreInput(label="总分", index=0, max_score=ms))
                self._cached_input_elements.append(el)
                return inputs
        except Exception:
            pass

        # 总分（旧版 placeholder 含"分"）
        try:
            el = frame.query_selector(self.SEL_INPUT_TOTAL_OLD)
            if el:
                ms = self._max_score_from_el(el)
                inputs.append(ScoreInput(label="总分", index=0, max_score=ms))
                self._cached_input_elements.append(el)
                return inputs
        except Exception:
            pass

        return inputs

    def _sub_label(self, el: ElementHandle, idx: int) -> str:
        try:
            label = el.evaluate(
                "el => el.closest('li')?.querySelector('.label')?.textContent?.trim()")
            if label:
                return str(label)
        except Exception:
            pass
        return f"第{idx + 1}题"

    def _max_score_from_el(self, el: ElementHandle) -> float:
        try:
            score = el.get_attribute("score")
            if score:
                return float(score)
        except Exception:
            pass
        try:
            ph = el.get_attribute("placeholder") or ""
            import re
            m = re.search(r"(\d+(?:\.\d+)?)", ph)
            if m:
                return float(m.group(1))
        except Exception:
            pass
        return 0.0

    def _find_input_element(self, index: int) -> Optional[ElementHandle]:
        if 0 <= index < len(self._cached_input_elements):
            return self._cached_input_elements[index]
        return None

    # ---------- 提交 ----------
    def submit_grade(self) -> bool:
        frame = self._marking_frame()
        # 新版 #bnt_save
        try:
            el = frame.query_selector(self.SEL_SUBMIT_NEW)
            if el:
                el.click()
                return True
        except Exception:
            pass
        # 旧版文字按钮
        try:
            btns = frame.query_selector_all("button")
            for b in btns:
                if self.SEL_SUBMIT_TEXT in (b.inner_text() or ""):
                    b.click()
                    return True
        except Exception:
            pass
        return False

    def wait_for_next_paper(self, old_image_url: str = "") -> bool:
        frame = self._marking_frame()
        for _ in range(50):  # 最多等 10 秒
            time.sleep(0.2)
            try:
                img = frame.query_selector(self.SEL_IMG_NEW) or frame.query_selector(self.SEL_IMG_OLD)
                if img:
                    src = img.get_attribute("src") or ""
                    if src and src != old_image_url:
                        return True
            except Exception:
                continue
        return False

    def is_regrade_mode(self) -> bool:
        try:
            return bool(self.browser.page.evaluate(
                "() => sessionStorage.getItem('ai-grading-regrade')"))
        except Exception:
            return False

    def get_current_student_name(self) -> str:
        for sel in ('.student-name', '.stu-name', '[class*="student"][class*="name"]', '.name'):
            try:
                el = self._marking_frame().query_selector(sel)
                if el:
                    text = (el.inner_text() or "").strip()
                    if text and len(text) < 20:
                        return text
            except Exception:
                continue
        return ""
