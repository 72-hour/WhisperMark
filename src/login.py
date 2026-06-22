"""
登录模块 - 手动模式
打开浏览器后由用户手动操作登录，CLI 等待确认
"""
import time
from typing import Optional
from .browser import BrowserManager
from .config import config


class LoginManager:
    """登录管理器 - 手动模式"""

    def __init__(self, browser: BrowserManager):
        self.browser = browser

    def open_login_page(self) -> bool:
        """打开登录页面，返回是否成功"""
        print("[*] 正在打开智学网...")
        return self.browser.goto(config.LOGIN_URL)

    def wait_for_manual_login(self) -> bool:
        """
        等待用户手动登录
        持续检测页面URL变化，判断是否登录成功
        """
        print("\n" + "=" * 50)
        print("请在浏览器中完成以下操作：")
        print("  1. 登录智学网")
        print("  2. 进入阅卷中心")
        print("  3. 选择要批改的考试/任务")
        print("  4. 选择要批改的题目")
        print("  5. 确保能看到学生答题图片")
        print("=" * 50)

        input("\n准备好后，按回车键继续...")

        # 检查当前页面状态
        current_url = self.browser.page.url
        print(f"\n[*] 当前页面: {current_url}")

        return True

    def check_page_ready(self) -> dict:
        """
        检查当前页面是否处于阅卷状态
        返回页面信息字典
        """
        info = {
            'url': self.browser.page.url,
            'title': '',
            'has_images': False,
            'image_count': 0,
            'image_urls': [],
            'page_text_preview': '',
            'ready': False,
        }

        try:
            info['title'] = self.browser.page.title()
        except Exception:
            pass

        # 获取页面中所有图片
        try:
            images = self.browser.page.query_selector_all('img')
            info['image_count'] = len(images)
            for img in images:
                src = img.get_attribute('src') or ''
                if src and not src.startswith('data:image/svg') and 'icon' not in src.lower():
                    box = img.bounding_box()
                    if box and box['width'] > 100 and box['height'] > 100:
                        info['image_urls'].append(src)
                        info['has_images'] = True
        except Exception:
            pass

        # 获取页面文本摘要
        try:
            body = self.browser.page.query_selector('body')
            if body:
                text = body.inner_text()[:500]
                info['page_text_preview'] = text
        except Exception:
            pass

        # 判断是否在阅卷页面
        url = info['url'].lower()
        if any(kw in url for kw in ['marking', 'grade', 'review', 'exam']):
            info['ready'] = True

        return info
