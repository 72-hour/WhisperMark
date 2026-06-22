"""
阅卷核心模块 - 重写版
从当前页面直接获取答案图片和提交分数，不自动导航
"""
import time
import re
import io
from typing import Optional, List
from dataclasses import dataclass, field

from .browser import BrowserManager
from .config import config
from .image_utils import auto_rotate_image, rotate_image, save_image

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


@dataclass
class StudentAnswer:
    """学生答案信息"""
    student_name: str = ""
    answer_image_data: Optional[bytes] = None
    current_score: Optional[float] = None
    is_graded: bool = False


@dataclass
class GradingProgress:
    """批改进度"""
    total: int = 0
    graded: int = 0

    @property
    def percentage(self) -> float:
        return (self.graded / self.total * 100) if self.total > 0 else 0.0

    @property
    def remaining(self) -> int:
        return self.total - self.graded


class Grader:
    """阅卷器 - 从当前页面直接工作"""

    def __init__(self, browser: BrowserManager):
        self.browser = browser
        self.progress = GradingProgress()
        self._current_answer: Optional[StudentAnswer] = None
        self._image_rotation: int = 0
        self._max_score: float = 13.0
        self._max_score_read: bool = False  # 是否已从页面读取满分
        # 缓存找到的有效选择器
        self._cached_img_selector: Optional[str] = None
        self._cached_score_selector: Optional[str] = None
        self._cached_next_selector: Optional[str] = None
        # 题目信息（给 AI 阅卷用）
        self.question_info: str = ""
        self.scoring_standard: str = ""

    def _get_content_frames(self):
        """获取所有可操作的 frame（主页面 + iframe）"""
        frames = [self.browser.page]
        try:
            for frame in self.browser.page.frames:
                if frame != self.browser.page.main_frame:
                    frames.append(frame)
        except Exception:
            pass
        return frames

    def analyze_page(self) -> dict:
        """
        分析当前页面结构（含 iframe），找到关键元素
        """
        result = {
            'url': self.browser.page.url,
            'title': '',
            'frames': [],
            'images': [],
            'inputs': [],
            'buttons': [],
        }

        try:
            result['title'] = self.browser.page.title()
        except Exception:
            pass

        # 列出所有 frame
        try:
            for i, frame in enumerate(self.browser.page.frames):
                result['frames'].append({'index': i, 'url': frame.url[:100]})
        except Exception:
            pass

        # 在所有 frame 中搜索
        for fi, frame in enumerate(self._get_content_frames()):
            prefix = f"[frame {fi}] " if fi > 0 else ""
            try:
                imgs = frame.query_selector_all('img')
                for i, img in enumerate(imgs):
                    try:
                        src = img.get_attribute('src') or ''
                        box = img.bounding_box()
                        cls = img.get_attribute('class') or ''
                        if box:
                            result['images'].append({
                                'frame': fi,
                                'index': i,
                                'src': src[:100] + '...' if len(src) > 100 else src,
                                'width': box['width'],
                                'height': box['height'],
                                'class': cls,
                            })
                    except Exception:
                        continue
            except Exception:
                pass

            try:
                inputs = frame.query_selector_all('input')
                for inp in inputs:
                    try:
                        typ = inp.get_attribute('type') or 'text'
                        name = inp.get_attribute('name') or ''
                        placeholder = inp.get_attribute('placeholder') or ''
                        cls = inp.get_attribute('class') or ''
                        result['inputs'].append({
                            'frame': fi,
                            'type': typ,
                            'name': name,
                            'placeholder': placeholder,
                            'class': cls,
                        })
                    except Exception:
                        continue
            except Exception:
                pass

            try:
                buttons = frame.query_selector_all('button, [role="button"], .btn, [class*="btn"]')
                for btn in buttons:
                    try:
                        text = btn.inner_text().strip()[:50]
                        cls = btn.get_attribute('class') or ''
                        if text:
                            result['buttons'].append({'frame': fi, 'text': text, 'class': cls})
                    except Exception:
                        continue
            except Exception:
                pass

        return result

    def debug_page(self):
        """打印页面调试信息（含 iframe）"""
        info = self.analyze_page()
        print(f"\n{'='*50}")
        print(f"页面URL: {info['url']}")
        print(f"页面标题: {info['title']}")

        print(f"\n--- Frames ({len(info['frames'])} 个) ---")
        for f in info['frames']:
            print(f"  [{f['index']}] {f['url']}")

        print(f"\n--- 图片 ({len(info['images'])} 个) ---")
        for img in info['images']:
            fr = f"[frame{img['frame']}] " if img['frame'] > 0 else ""
            print(f"  {fr}[{img['index']}] {img['width']:.0f}x{img['height']:.0f}  "
                  f"class={img['class'][:40]}  src={img['src'][:60]}")

        print(f"\n--- 输入框 ({len(info['inputs'])} 个) ---")
        for inp in info['inputs']:
            fr = f"[frame{inp['frame']}] " if inp['frame'] > 0 else ""
            print(f"  {fr}type={inp['type']} name={inp['name']} "
                  f"placeholder={inp['placeholder'][:30]} class={inp['class'][:30]}")

        print(f"\n--- 按钮 ({len(info['buttons'])} 个) ---")
        for btn in info['buttons'][:20]:
            fr = f"[frame{btn['frame']}] " if btn['frame'] > 0 else ""
            print(f"  {fr}[{btn['text'][:20]}] class={btn['class'][:40]}")

        print(f"{'='*50}\n")

    def find_answer_image(self) -> Optional[bytes]:
        """
        智能查找答题图片（搜索所有 frame）
        """
        js_selectors = [
            'img.enhance-definition-bright',
            'img[class*="enhance-definition"]',
            'img[src*="scanFile"]',
            'img[src*="marking"]',
        ]

        # 在所有 frame 中搜索
        for frame in self._get_content_frames():
            # 策略0: JS 直接提取已知选择器的 src
            for sel in js_selectors:
                try:
                    src = frame.evaluate(f'''
                        () => {{
                            const el = document.querySelector("{sel}");
                            return el ? el.src : null;
                        }}
                    ''')
                    if src and src.startswith('http'):
                        data = self._download_image(src)
                        if data:
                            self._cached_img_selector = sel
                            return data
                except Exception:
                    continue

            # 策略1: JS 找所有大图 src
            try:
                img_info = frame.evaluate('''
                    () => {
                        const imgs = Array.from(document.querySelectorAll('img'));
                        return imgs
                            .filter(i => i.naturalWidth > 100 && i.naturalHeight > 100)
                            .filter(i => !i.src.includes('logo') && !i.src.includes('icon') && !i.src.includes('avatar'))
                            .map(i => ({src: i.src, area: i.naturalWidth * i.naturalHeight}))
                            .sort((a, b) => b.area - a.area)
                            .slice(0, 5);
                    }
                ''')
                if img_info:
                    for info in img_info:
                        src = info.get('src', '')
                        if src and src.startswith('http'):
                            data = self._download_image(src)
                            if data:
                                return data
            except Exception:
                pass

        # 策略2: 元素截图兜底（也搜索所有 frame）
        for frame in self._get_content_frames():
            for sel in js_selectors:
                try:
                    el = frame.query_selector(sel)
                    if el:
                        el.scroll_into_view_if_needed(timeout=3000)
                        time.sleep(0.5)
                        data = el.screenshot()
                        if data and len(data) > 500:
                            return auto_rotate_image(data)
                except Exception:
                    continue

        return None

    def _download_image(self, url: str) -> Optional[bytes]:
        """通过页面上下文下载图片（带cookie）"""
        try:
            resp = self.browser.page.request.get(url)
            if resp.ok and len(resp.body()) > 500:
                return auto_rotate_image(resp.body())
        except Exception:
            pass
        return None

    def _extract_image(self, element) -> Optional[bytes]:
        """从元素提取图片数据"""
        try:
            # 方法1: 元素截图
            data = element.screenshot()
            if data and len(data) > 500:
                return auto_rotate_image(data)
        except Exception:
            pass

        try:
            # 方法2: 从src下载
            src = element.get_attribute('src')
            if src:
                if src.startswith('data:image'):
                    import base64
                    return base64.b64decode(src.split(',', 1)[1])
                elif src.startswith('http'):
                    resp = self.browser.page.request.get(src)
                    if resp.ok:
                        return auto_rotate_image(resp.body())
        except Exception:
            pass

        return None

    def get_current_answer(self) -> Optional[StudentAnswer]:
        """获取当前学生答案"""
        answer = StudentAnswer()

        # 获取答题图片
        img_data = self.find_answer_image()
        if img_data:
            answer.answer_image_data = img_data

        # 尝试获取学生姓名
        name_selectors = [
            '.student-name', '.stu-name', '[class*="student"][class*="name"]',
            '.name', '.student-info .name',
        ]
        for sel in name_selectors:
            try:
                el = self.browser.page.query_selector(sel)
                if el and el.is_visible(timeout=500):
                    text = el.inner_text().strip()
                    if text and len(text) < 20:
                        answer.student_name = text
                        break
            except Exception:
                continue

        # 尝试获取当前分数
        score_selectors = [
            'input[name="score"]', 'input[name*="score"]',
            '.score-input input', 'input.score-input',
            '#scoreInput', '[class*="score"] input',
        ]
        for sel in score_selectors:
            try:
                el = self.browser.page.query_selector(sel)
                if el and el.is_visible(timeout=500):
                    val = el.input_value()
                    if val:
                        try:
                            answer.current_score = float(val)
                            answer.is_graded = True
                        except ValueError:
                            pass
                    break
            except Exception:
                continue

        self._current_answer = answer
        return answer

    def _log_debug(self, msg: str):
        """写调试日志到文件"""
        try:
            from pathlib import Path
            log_file = Path(__file__).parent.parent / "data" / "debug.log"
            with open(log_file, 'a', encoding='utf-8') as f:
                import datetime
                f.write(f"[{datetime.datetime.now():%H:%M:%S}] {msg}\n")
        except Exception:
            pass

    def read_max_score(self) -> float:
        """从页面输入框的 score 属性读取满分（搜索所有 frame）"""
        for frame in self._get_content_frames():
            try:
                el = frame.query_selector('input[name="topicTxt"]')
                if el:
                    score_attr = el.get_attribute('score')
                    if score_attr:
                        val = float(score_attr)
                        if val > 0:
                            self._max_score = val
                            self._max_score_read = True
                            self._log_debug(f"读取满分: {val}")
                            return val
            except Exception:
                continue
        return self._max_score

    def submit_score(self, score: float) -> bool:
        """提交分数"""
        # 首次提交时自动读取满分
        if not self._max_score_read:
            self.read_max_score()

        if score < 0 or score > self._max_score:
            print(f"[!] 分数 {score} 超出范围 [0, {self._max_score}]")
            return False

        # 找分数输入框（在所有 frame 中搜索）
        score_selectors = [
            'input[name="topicTxt"]',              # 智学网实际使用
            'input.scores.topictxt_input',          # 智学网实际 class
            'input[id^="txt_marking_"]',            # id 模式
            'input[score]',                         # 带 score 属性
            'input[name="score"]',
            '.score-input input',
            '#scoreInput',
        ]

        input_el = None
        input_frame = None
        time.sleep(0.3)  # 等页面稳定
        for frame in self._get_content_frames():
            for sel in score_selectors:
                try:
                    el = frame.query_selector(sel)
                    if el:
                        # 不检查可见性，直接用
                        input_el = el
                        input_frame = frame
                        self._cached_score_selector = sel
                        self._log_debug(f"找到输入框: {sel} (frame: {frame.url[:50]})")
                        break
                except Exception:
                    continue
            if input_el:
                break

        if not input_el:
            self._log_debug(f"找不到分数输入框，尝试过: {score_selectors}")
            return False

        try:
            input_el.scroll_into_view_if_needed(timeout=2000)
            time.sleep(0.2)
            input_el.click()
            time.sleep(0.1)
            # 清空
            input_el.press('Control+a')
            input_el.press('Backspace')
            time.sleep(0.1)
            input_el.type(str(score), delay=30)
            time.sleep(0.2)
            val = input_el.input_value()
            self._log_debug(f"输入框填入: '{val}'")
        except Exception as e:
            self._log_debug(f"输入分数失败: {e}")
            return False

        # 点击提交按钮（在输入框所在的 frame 中搜索）
        submit_selectors = [
            '#bnt_save',                                       # 智学网 id
            '.score_box_button .el-button--primary',           # 容器内主按钮
            'button.el-button--primary:has-text("提交分数")',
            'button.el-button--primary:has-text("提交")',
            'button:has-text("提交分数")',
            'button:has-text("提交")',
        ]

        search_frame = input_frame or self.browser.page
        for sel in submit_selectors:
            try:
                elements = search_frame.query_selector_all(sel)
                for el in elements:
                    try:
                        if el.is_visible(timeout=500):
                            el.scroll_into_view_if_needed(timeout=1000)
                            time.sleep(0.1)
                            # 记录当前图片 src 用于等待变化
                            old_img_src = ""
                            try:
                                old_img = search_frame.query_selector('img.enhance-definition-bright')
                                if old_img:
                                    old_img_src = (old_img.get_attribute('src') or "")[:80]
                            except Exception:
                                pass

                            el.click()
                            self._log_debug(f"点击提交: {sel}")
                            self._cached_next_selector = sel
                            self.progress.graded += 1
                            self._image_rotation = 0

                            # 等待新答题卡加载（图片 src 变化）
                            self._log_debug(f"等待新答题卡... (旧src: {old_img_src[:40]})")
                            for _ in range(30):  # 最多等 3 秒
                                time.sleep(0.1)
                                try:
                                    new_img = search_frame.query_selector('img.enhance-definition-bright')
                                    if new_img:
                                        new_src = (new_img.get_attribute('src') or "")[:80]
                                        if new_src and new_src != old_img_src:
                                            self._log_debug(f"新答题卡已加载: {new_src[:40]}")
                                            time.sleep(0.3)  # 额外等图片渲染
                                            return True
                                except Exception:
                                    continue
                            self._log_debug("等待超时，继续")
                            return True
                    except Exception:
                        continue
            except Exception:
                continue

        # 尝试回车提交
        try:
            self._log_debug("回车提交")
            input_el.press('Enter')
            self.progress.graded += 1
            self._image_rotation = 0
            time.sleep(1.5)
            return True
        except Exception as e:
            self._log_debug(f"回车失败: {e}")

        self._log_debug(f"找不到提交按钮")
        return False

    def go_next(self) -> bool:
        """下一份"""
        if self._cached_next_selector:
            try:
                el = self.browser.page.query_selector(self._cached_next_selector)
                if el and el.is_visible(timeout=500):
                    el.click()
                    time.sleep(1.5)
                    return True
            except Exception:
                pass

        next_selectors = [
            'button:has-text("下一份")',
            'button:has-text("下一个")',
            '.next-btn', '.btn-next',
            '[class*="next"]',
        ]
        for sel in next_selectors:
            try:
                el = self.browser.page.query_selector(sel)
                if el and el.is_visible(timeout=500):
                    el.click()
                    time.sleep(1.5)
                    return True
            except Exception:
                continue
        return False

    def go_previous(self) -> bool:
        """上一份"""
        prev_selectors = [
            'button:has-text("上一份")',
            'button:has-text("上一个")',
            '.prev-btn', '.btn-prev',
            '[class*="prev"]',
        ]
        for sel in prev_selectors:
            try:
                el = self.browser.page.query_selector(sel)
                if el and el.is_visible(timeout=500):
                    el.click()
                    time.sleep(1.5)
                    return True
            except Exception:
                continue
        return False

    def rotate_image(self, angle: int = 90) -> Optional[bytes]:
        """旋转当前图片"""
        if not self._current_answer or not self._current_answer.answer_image_data:
            return None
        self._image_rotation = (self._image_rotation + angle) % 360
        self._current_answer.answer_image_data = rotate_image(
            self._current_answer.answer_image_data, angle
        )
        return self._current_answer.answer_image_data

    def save_current_answer_image(self, filename: Optional[str] = None) -> Optional[str]:
        """保存当前答案图片"""
        if not self._current_answer or not self._current_answer.answer_image_data:
            return None
        if not filename:
            filename = f"answer_{int(time.time())}.jpg"
        save_path = config.SCREENSHOTS_DIR / filename
        if save_image(self._current_answer.answer_image_data, save_path):
            return str(save_path)
        return None

    def get_progress_bar(self, width: int = 30) -> str:
        """进度条"""
        filled = int(width * self.progress.percentage / 100)
        bar = '#' * filled + '-' * (width - filled)
        return f"[{bar}] {self.progress.percentage:.1f}%"

    @property
    def max_score(self) -> float:
        return self._max_score

    @max_score.setter
    def max_score(self, value: float):
        self._max_score = value
