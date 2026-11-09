"""
平台注册表
==================================================
集中管理所有平台适配器，提供：
  - 按 id 获取适配器
  - 自动检测当前页面所属平台（should_initialize）
"""
from typing import List, Optional, Type

from ..browser import BrowserManager
from .base import PlatformAdapter
from .zhixue import ZhixueAdapter
from .haoyuejuan import HaoyuejuanAdapter

# 所有已注册的适配器（按 id 排序，便于扩展）
ADAPTERS: List[Type[PlatformAdapter]] = [
    ZhixueAdapter,
    HaoyuejuanAdapter,
]


def get_adapter_classes() -> List[Type[PlatformAdapter]]:
    return ADAPTERS


def get_adapter_by_id(adapter_id: str, browser: BrowserManager) -> Optional[PlatformAdapter]:
    """按 id 获取适配器实例"""
    for cls in ADAPTERS:
        if cls.id == adapter_id:
            return cls(browser)
    return None


def detect_adapter(browser: BrowserManager) -> Optional[PlatformAdapter]:
    """自动检测当前页面所属平台，返回匹配的适配器实例"""
    for cls in ADAPTERS:
        adapter = cls(browser)
        try:
            if adapter.should_initialize():
                return adapter
        except Exception:
            continue
    return None
