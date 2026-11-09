"""
配置管理模块（WhisperMark 2.0）
管理所有配置项，支持从环境变量、.env 文件、命令行参数三层覆盖。

优先级：命令行参数 > 环境变量 > .env 文件 > 默认值
"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# 项目根目录（wm 包的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """加载 .env 文件（如存在），不覆盖已存在的环境变量"""
    dotenv_path = PROJECT_ROOT / ".env"
    if not dotenv_path.exists():
        return
    try:
        with open(dotenv_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].lstrip()
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # 去掉成对的引号（支持 .env 常见写法）
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


_load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


@dataclass
class Config:
    """全局配置类"""

    # ========== 基础 ==========
    APP_NAME: str = "WhisperMark 2.0"
    APP_VERSION: str = "2.0.0"

    # ========== 浏览器 ==========
    HEADLESS: bool = False          # 默认显示浏览器，便于人工登录
    SLOW_MO: int = 100              # 操作延迟（毫秒）
    VIEWPORT_WIDTH: int = 1920
    VIEWPORT_HEIGHT: int = 1080
    PAGE_LOAD_TIMEOUT: int = 30000
    ELEMENT_WAIT_TIMEOUT: int = 10000
    # 隐藏自动化特征，降低被平台风控识别的概率
    STEALTH: bool = True

    # ========== 数据目录 ==========
    DATA_DIR: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    RUBRIC_DIR: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "rubrics")
    HISTORY_DIR: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "history")
    SCREENSHOTS_DIR: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "screenshots")
    COOKIES_FILE: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "cookies.json")

    # ========== 临时监控目录（启动时清理） ==========
    RUNTIME_DIR: Path = field(default_factory=lambda: PROJECT_ROOT / "runtime")
    MONITOR_DIR: Path = field(default_factory=lambda: PROJECT_ROOT / "runtime" / "monitor")

    # ========== AI 配置（OpenAI 兼容接口） ==========
    AI_API_KEY: str = ""
    AI_BASE_URL: str = "https://api.deepseek.com/v1"
    AI_MODEL: str = "deepseek-chat"
    AI_TEMPERATURE: float = 0.1
    AI_MAX_TOKENS_GRADE: int = 1024
    AI_MAX_TOKENS_ANALYZE: int = 2048

    # ========== 监控服务器 ==========
    MONITOR_HOST: str = "127.0.0.1"
    MONITOR_PORT: int = 8321
    MONITOR_PORT_FALLBACK: bool = True   # 端口被占用时自动顺延
    MONITOR_ENABLED: bool = True

    # ========== 微信推送（workbuddy 小龙虾 / 通用 webhook） ==========
    WECHAT_ENABLED: bool = False
    WECHAT_WEBHOOK_URL: str = ""       # 小龙虾/Server酱 等 webhook 地址
    WECHAT_PROGRESS_INTERVAL: int = 50  # 每 N 份推一次进度
    WECHAT_PAYLOAD_KEY: str = "text"    # 推送 JSON 的字段名（Server酱 用 "content" 等）

    # ========== 阅卷 ==========
    DEFAULT_SCORE: float = 0.0
    MAX_SCORE: float = 13.0            # 兜底满分，优先从页面读取
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 1.0
    MAX_EMPTY_RETRIES: int = 5         # 连续 N 次拿不到答题卡判定批改完成
    IMAGE_ROTATION: int = 0

    def __post_init__(self):
        """应用环境变量覆盖 → 创建必要的目录"""
        self._apply_env()
        for d in (self.DATA_DIR, self.RUBRIC_DIR, self.HISTORY_DIR,
                  self.SCREENSHOTS_DIR, self.RUNTIME_DIR, self.MONITOR_DIR):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                # 只读目录（例如在受限沙箱里跑 demo）不应导致整个程序无法启动
                pass

    def _apply_env(self):
        """从环境变量覆盖配置（.env 已在模块加载时导入到 os.environ）"""
        # 浏览器
        self.HEADLESS = _env_bool("WM_HEADLESS", self.HEADLESS)
        self.SLOW_MO = _env_int("WM_SLOW_MO", self.SLOW_MO)
        self.STEALTH = _env_bool("WM_STEALTH", self.STEALTH)

        # AI（兼容旧变量名 AI_* 与新变量名 WM_AI_*）
        self.AI_API_KEY = os.getenv("WM_AI_API_KEY") or os.getenv("AI_API_KEY", self.AI_API_KEY)
        self.AI_BASE_URL = os.getenv("WM_AI_BASE_URL") or os.getenv("AI_BASE_URL", self.AI_BASE_URL)
        self.AI_MODEL = os.getenv("WM_AI_MODEL") or os.getenv("AI_MODEL", self.AI_MODEL)
        self.AI_TEMPERATURE = _env_float("WM_AI_TEMPERATURE", self.AI_TEMPERATURE)
        self.AI_MAX_TOKENS_GRADE = _env_int("WM_AI_MAX_TOKENS_GRADE", self.AI_MAX_TOKENS_GRADE)
        self.AI_MAX_TOKENS_ANALYZE = _env_int("WM_AI_MAX_TOKENS_ANALYZE", self.AI_MAX_TOKENS_ANALYZE)

        # 数据 / 运行时目录（便于端口隔离测试、把运行时数据放到别处）
        data_dir = os.getenv("WM_DATA_DIR")
        if data_dir:
            base = Path(data_dir).expanduser()
            self.DATA_DIR = base
            self.RUBRIC_DIR = base / "rubrics"
            self.HISTORY_DIR = base / "history"
            self.SCREENSHOTS_DIR = base / "screenshots"
            self.COOKIES_FILE = base / "cookies.json"
        runtime_dir = os.getenv("WM_RUNTIME_DIR")
        if runtime_dir:
            self.RUNTIME_DIR = Path(runtime_dir).expanduser()
            self.MONITOR_DIR = self.RUNTIME_DIR / "monitor"

        # 监控
        self.MONITOR_HOST = os.getenv("WM_MONITOR_HOST", self.MONITOR_HOST)
        self.MONITOR_PORT = _env_int("WM_MONITOR_PORT", self.MONITOR_PORT)
        self.MONITOR_PORT_FALLBACK = _env_bool("WM_MONITOR_PORT_FALLBACK",
                                               self.MONITOR_PORT_FALLBACK)
        self.MONITOR_ENABLED = _env_bool("WM_MONITOR_ENABLED", self.MONITOR_ENABLED)

        # 微信
        self.WECHAT_ENABLED = _env_bool("WM_WECHAT_ENABLED", self.WECHAT_ENABLED)
        self.WECHAT_WEBHOOK_URL = os.getenv("WM_WECHAT_WEBHOOK_URL", self.WECHAT_WEBHOOK_URL)
        self.WECHAT_PROGRESS_INTERVAL = _env_int(
            "WM_WECHAT_PROGRESS_INTERVAL", self.WECHAT_PROGRESS_INTERVAL)
        self.WECHAT_PAYLOAD_KEY = os.getenv("WM_WECHAT_PAYLOAD_KEY", self.WECHAT_PAYLOAD_KEY)

        # 阅卷
        self.MAX_SCORE = _env_float("WM_MAX_SCORE", self.MAX_SCORE)
        self.MAX_RETRIES = _env_int("WM_MAX_RETRIES", self.MAX_RETRIES)
        self.RETRY_DELAY = _env_float("WM_RETRY_DELAY", self.RETRY_DELAY)
        self.MAX_EMPTY_RETRIES = _env_int("WM_MAX_EMPTY_RETRIES", self.MAX_EMPTY_RETRIES)

    @property
    def ai_ready(self) -> bool:
        """AI 是否已配置（有 key 即可用）"""
        return bool(self.AI_API_KEY)

    @property
    def wechat_ready(self) -> bool:
        """微信推送是否已配置"""
        return self.WECHAT_ENABLED and bool(self.WECHAT_WEBHOOK_URL)


# 全局配置实例
config = Config()
