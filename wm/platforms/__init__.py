"""平台适配器层：智学网 / 阅小二"""
from .base import PlatformAdapter, ScoreInput
from .registry import (
    ADAPTERS, get_adapter_classes, get_adapter_by_id, detect_adapter,
)

__all__ = [
    "PlatformAdapter", "ScoreInput",
    "ADAPTERS", "get_adapter_classes", "get_adapter_by_id", "detect_adapter",
]
