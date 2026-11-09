"""
阅小二平台适配器（翻译自 AI-Marker-Suite src/adapters/haoyuejuan/）
==================================================
www.haoyuejuan.com/yuejuan/#/reading — Vue 3 + Element Plus，无 iframe
与五岳阅卷同构。选择器（对应 JS 版 HAOYUEJUAN_SELECTORS）：
  答题卡：.outBox:not(.hideBox) .imgSection img
  小题  ：.computeItem（题号 .num，输入框 .el-input__inner，placeholder "满分X分"）
  单题  ：#inputOne
  提交  ：.btnSubmit（"提交得分"）
  回评  ：button.redBtn 含"取消回评"
"""
import re
import time
from typing import List, Optional

try:  # playwright 可选依赖
    from playwright.sync_api import ElementHandle
except ImportError:  # pragma: no cover
    from typing import Any
    ElementHandle = Any  # type: ignore

from .base import PlatformAdapter, ScoreInput
from ..utils.logging import logger


class HaoyuejuanAdapter(PlatformAdapter):
    name = "阅小二"
    id = "haoyuejuan"
    url_patterns = ["*://www.haoyuejuan.com/*", "*://haoyuejuan.com/*"]
    login_url = "https://www.haoyuejuan.com/"

    SEL_IMG = '.outBox:not(.hideBox) .imgSection img'
    SEL_IMG_ALL = '.imgSection img'
    SEL_INPUT_SINGLE = "#inputOne"
    SEL_SCORE_ITEM = ".computeItem"
    SEL_SCORE_ITEM_NUM = ".computeItem .num"
    SEL_SCORE_ITEM_INPUT = ".computeItem .el-input__inner"
    SEL_SUBMIT = ".btnSubmit"
    SEL_CANCEL_REGRADE = "button.redBtn"

    def __init__(self, browser):
        super().__init__(browser)
        self._cached_input_elements: List[ElementHandle] = []

    # ---------- 生命周期 ----------
    def is_marking_page(self) -> bool:
        try:
            hash_val = self.browser.page.evaluate("() => window.location.hash || ''")
        except Exception:
            hash_val = ""
        if "/reading" in (hash_val or ""):
            return True
        return bool(self.browser.page.query_selector(self.SEL_SUBMIT))

    def detect_marking_page(self) -> bool:
        if not self.is_marking_page():
            return False
        for _ in range(10):  # 最多等 10 秒
            try:
                has_img = bool(self.browser.page.query_selector(self.SEL_IMG))
                has_input = bool(self.browser.page.query_selector(self.SEL_INPUT_SINGLE)
                                 or self.browser.page.query_selector(self.SEL_SCORE_ITEM_INPUT))
                has_btn = bool(self.browser.page.query_selector(self.SEL_SUBMIT))
                if has_img and has_input and has_btn:
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def get_task_identifier(self) -> str:
        try:
            num = self.browser.page.evaluate(
                "() => document.querySelector('.computeItem .num')?.textContent?.trim() || ''")
        except Exception:
            num = ""
        return f"{self.browser.page.url}|{num}"

    # ---------- 图片 ----------
    def gather_answer_images(self) -> List[str]:
        urls: List[str] = []
        for _ in range(12):  # 最多等 3 秒（换卷时 outBox 会短暂重建）
            try:
                imgs = self.browser.page.query_selector_all(self.SEL_IMG)
                for img in imgs:
                    src = img.get_attribute("src") or ""
                    if src.startswith("http"):
                        urls.append(src)
                if urls:
                    return list(dict.fromkeys(urls))
            except Exception:
                pass
            time.sleep(0.25)

        # 兜底：不要求 naturalWidth
        try:
            imgs = self.browser.page.query_selector_all(self.SEL_IMG_ALL)
            for img in imgs:
                src = img.get_attribute("src") or ""
                if src.startswith("http"):
                    urls.append(src)
        except Exception:
            pass
        return list(dict.fromkeys(urls))

    # ---------- 分数输入 ----------
    def get_score_inputs(self) -> List[ScoreInput]:
        self._cached_input_elements = []
        inputs: List[ScoreInput] = []

        items = self.browser.page.query_selector_all(self.SEL_SCORE_ITEM)
        if items:
            for i, item in enumerate(items):
                try:
                    num_el = item.query_selector(".num")
                    input_el = item.query_selector(".el-input__inner")
                    if input_el:
                        label = (num_el.inner_text().strip() if num_el else "") or f"第{i + 1}题"
                        ms = self._max_score_from_placeholder(input_el)
                        inputs.append(ScoreInput(label=label, index=i, max_score=ms))
                        self._cached_input_elements.append(input_el)
                except Exception:
                    continue
            if inputs:
                return inputs

        # 单小题
        single = self.browser.page.query_selector(self.SEL_INPUT_SINGLE)
        if single:
            ms = self._max_score_from_placeholder(single)
            inputs.append(ScoreInput(label="总分", index=0, max_score=ms))
            self._cached_input_elements.append(single)

        return inputs

    def _max_score_from_placeholder(self, el: ElementHandle) -> float:
        try:
            ph = el.get_attribute("placeholder") or ""
            m = re.search(r"满分(\d+(?:\.\d+)?)分", ph)
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
        try:
            el = self.browser.page.query_selector(self.SEL_SUBMIT)
            if el:
                el.click()
                return True
        except Exception:
            pass
        return False

    def wait_for_next_paper(self, old_image_url: str = "") -> bool:
        old_path = self._img_path(old_image_url) if old_image_url else ""
        for _ in range(125):  # 最多等 25 秒
            time.sleep(0.2)
            try:
                imgs = self.browser.page.query_selector_all(self.SEL_IMG)
                for img in imgs:
                    src = img.get_attribute("src") or ""
                    if src and self._img_path(src) != old_path:
                        return True
            except Exception:
                continue
        return False

    def _img_path(self, url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).path
        except Exception:
            return url[:80]

    def is_regrade_mode(self) -> bool:
        try:
            el = self.browser.page.query_selector(self.SEL_CANCEL_REGRADE)
            if el and "取消回评" in (el.inner_text() or ""):
                return True
        except Exception:
            pass
        try:
            card = self.browser.page.query_selector(".cardBack")
            if card and "取消回评" in (card.inner_text() or ""):
                return True
        except Exception:
            pass
        return False
