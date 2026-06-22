"""
浏览器管理模块
封装Playwright浏览器操作，提供稳定的浏览器环境
"""
import json
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from .config import config


class BrowserManager:
    """浏览器管理器，负责浏览器的启动、关闭和上下文管理"""
    
    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
    
    def start(self):
        """启动浏览器"""
        if self._browser:
            return
        
        self._playwright = sync_playwright().start()
        
        # 启动Chromium浏览器
        self._browser = self._playwright.chromium.launch(
            headless=config.HEADLESS,
            slow_mo=config.SLOW_MO,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-blink-features=AutomationControlled',
            ]
        )
        
        # 创建浏览器上下文
        self._context = self._browser.new_context(
            viewport={
                'width': config.VIEWPORT_WIDTH,
                'height': config.VIEWPORT_HEIGHT,
            },
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
        )
        
        # 设置默认超时
        self._context.set_default_timeout(config.ELEMENT_WAIT_TIMEOUT)
        self._context.set_default_navigation_timeout(config.PAGE_LOAD_TIMEOUT)
        
        # 创建新页面
        self._page = self._context.new_page()
        
        # 尝试加载已保存的cookies
        self._load_cookies()
    
    def stop(self):
        """关闭浏览器"""
        try:
            # 保存cookies
            self._save_cookies()
        except Exception:
            pass
        
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
    
    @property
    def page(self) -> Page:
        """获取当前页面"""
        if not self._page:
            raise RuntimeError("浏览器未启动，请先调用start()方法")
        return self._page
    
    @property
    def context(self) -> BrowserContext:
        """获取浏览器上下文"""
        if not self._context:
            raise RuntimeError("浏览器未启动，请先调用start()方法")
        return self._context
    
    def goto(self, url: str, wait_until: str = 'domcontentloaded') -> bool:
        """
        导航到指定URL
        
        Args:
            url: 目标URL
            wait_until: 等待加载完成的时机
            
        Returns:
            是否成功导航
        """
        try:
            self.page.goto(url, wait_until=wait_until, timeout=config.PAGE_LOAD_TIMEOUT)
            return True
        except PlaywrightTimeoutError:
            return False
        except Exception:
            return False
    
    def wait_for_selector(self, selector: str, timeout: Optional[int] = None) -> bool:
        """
        等待选择器出现
        
        Args:
            selector: CSS选择器
            timeout: 超时时间（毫秒），默认使用配置值
            
        Returns:
            是否找到元素
        """
        try:
            self.page.wait_for_selector(
                selector,
                timeout=timeout or config.ELEMENT_WAIT_TIMEOUT
            )
            return True
        except PlaywrightTimeoutError:
            return False
    
    def safe_click(self, selector: str, timeout: Optional[int] = None) -> bool:
        """
        安全点击元素
        
        Args:
            selector: CSS选择器
            timeout: 超时时间
            
        Returns:
            是否点击成功
        """
        try:
            self.page.click(selector, timeout=timeout or config.ELEMENT_WAIT_TIMEOUT)
            return True
        except Exception:
            return False
    
    def safe_fill(self, selector: str, value: str, timeout: Optional[int] = None) -> bool:
        """
        安全填充输入框
        
        Args:
            selector: CSS选择器
            value: 填充的值
            timeout: 超时时间
            
        Returns:
            是否填充成功
        """
        try:
            self.page.fill(selector, value, timeout=timeout or config.ELEMENT_WAIT_TIMEOUT)
            return True
        except Exception:
            return False
    
    def safe_input(self, selector: str, value: str, timeout: Optional[int] = None) -> bool:
        """
        安全输入文本（模拟键盘输入）
        
        Args:
            selector: CSS选择器
            value: 输入的文本
            timeout: 超时时间
            
        Returns:
            是否输入成功
        """
        try:
            self.page.type(selector, value, timeout=timeout or config.ELEMENT_WAIT_TIMEOUT)
            return True
        except Exception:
            return False
    
    def get_element_text(self, selector: str, timeout: Optional[int] = None) -> Optional[str]:
        """
        获取元素文本
        
        Args:
            selector: CSS选择器
            timeout: 超时时间
            
        Returns:
            元素文本，失败返回None
        """
        try:
            element = self.page.wait_for_selector(
                selector,
                timeout=timeout or config.ELEMENT_WAIT_TIMEOUT
            )
            return element.text_content()
        except Exception:
            return None
    
    def get_element_attribute(self, selector: str, attribute: str, timeout: Optional[int] = None) -> Optional[str]:
        """
        获取元素属性
        
        Args:
            selector: CSS选择器
            attribute: 属性名
            timeout: 超时时间
            
        Returns:
            属性值，失败返回None
        """
        try:
            element = self.page.wait_for_selector(
                selector,
                timeout=timeout or config.ELEMENT_WAIT_TIMEOUT
            )
            return element.get_attribute(attribute)
        except Exception:
            return None
    
    def screenshot(self, name: Optional[str] = None) -> Optional[Path]:
        """
        截图
        
        Args:
            name: 截图文件名，不指定则用时间戳
            
        Returns:
            截图文件路径，失败返回None
        """
        try:
            if not name:
                name = f"screenshot_{int(time.time())}.png"
            
            save_path = config.SCREENSHOTS_DIR / name
            self.page.screenshot(path=str(save_path), full_page=True)
            return save_path
        except Exception:
            return None
    
    def _save_cookies(self):
        """保存cookies到文件"""
        if not self._context:
            return
        
        try:
            cookies = self._context.cookies()
            with open(config.COOKIES_FILE, 'w', encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    def _load_cookies(self):
        """从文件加载cookies"""
        if not self._context:
            return
        
        try:
            if config.COOKIES_FILE.exists():
                with open(config.COOKIES_FILE, 'r', encoding='utf-8') as f:
                    cookies = json.load(f)
                self._context.add_cookies(cookies)
        except Exception:
            pass
    
    def is_logged_in(self) -> bool:
        """
        检查是否已登录
        通过访问首页并检查是否有登录后的特征元素来判断
        
        Returns:
            是否已登录
        """
        try:
            # 访问智学网首页
            self.goto(config.BASE_URL)
            
            # 等待页面加载
            time.sleep(2)
            
            # 检查是否存在用户信息相关元素（已登录状态）
            # 智学网登录后通常会显示用户名或用户头像
            user_selectors = [
                '.user-info',
                '.user-name',
                '.header-user',
                '.login-user',
                '#userInfo',
            ]
            
            for selector in user_selectors:
                try:
                    if self.page.is_visible(selector, timeout=2000):
                        return True
                except Exception:
                    continue
            
            # 检查URL是否包含登录相关路径
            current_url = self.page.url
            if 'login' in current_url.lower():
                return False
            
            return False
        except Exception:
            return False
    
    def wait_for_page_load(self, timeout: Optional[int] = None):
        """
        等待页面加载完成
        
        Args:
            timeout: 超时时间（毫秒）
        """
        try:
            self.page.wait_for_load_state(
                'domcontentloaded',
                timeout=timeout or config.PAGE_LOAD_TIMEOUT
            )
        except Exception:
            pass
    
    def reload_page(self) -> bool:
        """
        刷新页面
        
        Returns:
            是否刷新成功
        """
        try:
            self.page.reload(wait_until='domcontentloaded')
            return True
        except Exception:
            return False
    
    def go_back(self) -> bool:
        """
        返回上一页
        
        Returns:
            是否返回成功
        """
        try:
            self.page.go_back(wait_until='domcontentloaded')
            return True
        except Exception:
            return False
    
    def execute_script(self, script: str) -> Any:
        """
        执行JavaScript脚本
        
        Args:
            script: JS脚本
            
        Returns:
            脚本执行结果
        """
        try:
            return self.page.evaluate(script)
        except Exception:
            return None
    
    def get_page_html(self) -> str:
        """
        获取页面HTML内容
        
        Returns:
            HTML内容
        """
        try:
            return self.page.content()
        except Exception:
            return ""
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
