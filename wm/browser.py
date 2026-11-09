"""
浏览器管理模块（Playwright）
封装 Chromium 的启动、上下文管理、cookies 持久化，并注入 stealth 脚本
降低被阅卷平台风控识别的概率。

注意：Playwright 的同步 API 不是线程安全的 —— 必须在创建它的同一个线程里调用。
因此 CLI 的阅卷主循环运行在主线程，监控 / 通知等只读状态的模块在后台线程。
本模块对 playwright 做惰性导入，未安装时 `python main.py check-env` 仍可运行。
"""
import json
from typing import Any, Optional

from .config import config
from .utils.logging import logger

try:  # 惰性可用：没装 playwright 时不影响 check-env / demo
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于运行环境
    sync_playwright = None
    PlaywrightTimeoutError = TimeoutError  # type: ignore
    PLAYWRIGHT_AVAILABLE = False

# 注入的 stealth 脚本：抹除 webdriver 等自动化特征
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {};
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""


class BrowserManager:
    """浏览器管理器（单线程使用）"""

    def __init__(self):
        self._playwright: Optional[Any] = None
        self._browser: Optional[Any] = None
        self._context: Optional[Any] = None
        self._page: Optional[Any] = None

    # ---------- 生命周期 ----------
    def start(self):
        if self._browser:
            return
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError(
                "未安装 playwright。请执行：pip install -r requirements.txt && playwright install chromium")

        try:
            self._playwright = sync_playwright().start()
        except Exception as e:
            self._playwright = None
            raise RuntimeError(
                f"Playwright 初始化失败：{e}\n"
                "请确认依赖与浏览器内核已安装：pip install -r requirements.txt && playwright install chromium") from e

        try:
            self._browser = self._playwright.chromium.launch(
                headless=config.HEADLESS,
                slow_mo=config.SLOW_MO,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
        except Exception as e:
            self._playwright.stop()
            self._playwright = None
            raise RuntimeError(
                f"Chromium 启动失败：{e}\n请确认已安装浏览器内核：playwright install chromium") from e

        self._context = self._browser.new_context(
            viewport={"width": config.VIEWPORT_WIDTH, "height": config.VIEWPORT_HEIGHT},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        self._context.set_default_timeout(config.ELEMENT_WAIT_TIMEOUT)
        self._context.set_default_navigation_timeout(config.PAGE_LOAD_TIMEOUT)

        if config.STEALTH:
            try:
                # 必须加在 context 上，页面级 init script 对后续新建页面无效
                self._context.add_init_script(_STEALTH_JS)
            except Exception:
                pass

        self._page = self._context.new_page()
        self._load_cookies()

    def stop(self):
        try:
            self._save_cookies()
        except Exception:
            pass
        for obj in ("_page", "_context", "_browser"):
            attr = getattr(self, obj, None)
            if attr:
                try:
                    attr.close()
                except Exception:
                    pass
                setattr(self, obj, None)
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    @property
    def is_started(self) -> bool:
        return self._page is not None

    @property
    def page(self):
        if not self._page:
            raise RuntimeError("浏览器未启动，请先调用 start()")
        return self._page

    @property
    def context(self):
        if not self._context:
            raise RuntimeError("浏览器未启动，请先调用 start()")
        return self._context

    # ---------- 导航 ----------
    def goto(self, url: str, wait_until: str = "domcontentloaded") -> bool:
        try:
            self.page.goto(url, wait_until=wait_until, timeout=config.PAGE_LOAD_TIMEOUT)
            return True
        except PlaywrightTimeoutError:
            logger.warning("页面加载超时: %s", url)
            return False
        except Exception as e:
            logger.warning("页面打开失败 %s: %s", url, e)
            return False

    # ---------- cookies ----------
    def _save_cookies(self):
        if not self._context:
            return
        try:
            cookies = self._context.cookies()
            config.COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(config.COOKIES_FILE, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug("保存 cookies 失败: %s", e)

    def _load_cookies(self):
        if not self._context:
            return
        try:
            if config.COOKIES_FILE.exists():
                cookies = json.loads(config.COOKIES_FILE.read_text(encoding="utf-8"))
                if isinstance(cookies, list) and cookies:
                    self._context.add_cookies(cookies)
                    logger.info("已加载 %d 条登录 cookies", len(cookies))
        except Exception as e:
            logger.debug("加载 cookies 失败: %s", e)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
