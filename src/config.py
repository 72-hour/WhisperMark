"""
配置管理模块
管理所有配置项，支持从环境变量、配置文件读取
"""
import os
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Config:
    """全局配置类"""
    
    # 基础配置
    BASE_URL: str = "https://www.zhixue.com"
    LOGIN_URL: str = "https://www.zhixue.com/login.html"
    
    # 浏览器配置
    HEADLESS: bool = False  # 默认显示浏览器，方便人工操作
    SLOW_MO: int = 100  # 操作延迟，毫秒
    VIEWPORT_WIDTH: int = 1920
    VIEWPORT_HEIGHT: int = 1080
    
    # 超时配置（毫秒）
    PAGE_LOAD_TIMEOUT: int = 30000
    ELEMENT_WAIT_TIMEOUT: int = 10000
    
    # 数据存储路径
    DATA_DIR: Path = field(default_factory=lambda: Path(__file__).parent.parent / "data")
    COOKIES_FILE: Path = field(default_factory=lambda: Path(__file__).parent.parent / "data" / "cookies.json")
    SCREENSHOTS_DIR: Path = field(default_factory=lambda: Path(__file__).parent.parent / "data" / "screenshots")
    
    # 阅卷配置
    DEFAULT_SCORE: int = 0
    MAX_SCORE: int = 13  # 第15题满分13分
    
    # 重试配置
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 1.0  # 秒
    
    # 图片配置
    IMAGE_ROTATION: int = 0  # 默认旋转角度，0=不旋转

    # AI 阅卷配置（DeepSeek 为主模型）
    AI_API_KEY: str = ""           # API Key (必填，通过环境变量 AI_API_KEY 设置)
    AI_BASE_URL: str = "https://api.deepseek.com/v1"  # DeepSeek 兼容 OpenAI 接口地址
    AI_MODEL: str = "deepseek-chat"                   # 默认模型

    def __post_init__(self):
        """初始化后创建必要的目录"""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量加载配置"""
        config = cls()
        
        # 从环境变量覆盖配置
        if os.getenv("ZHIXUE_HEADLESS"):
            config.HEADLESS = os.getenv("ZHIXUE_HEADLESS").lower() == "true"
        
        if os.getenv("ZHIXUE_SLOW_MO"):
            config.SLOW_MO = int(os.getenv("ZHIXUE_SLOW_MO"))
        
        if os.getenv("ZHIXUE_MAX_SCORE"):
            config.MAX_SCORE = int(os.getenv("ZHIXUE_MAX_SCORE"))

        # AI 配置从环境变量读取
        if os.getenv("AI_API_KEY"):
            config.AI_API_KEY = os.getenv("AI_API_KEY", "")
        if os.getenv("AI_BASE_URL"):
            config.AI_BASE_URL = os.getenv("AI_BASE_URL", "")
        if os.getenv("AI_MODEL"):
            config.AI_MODEL = os.getenv("AI_MODEL", "")

        return config


# 全局配置实例
config = Config.from_env()
