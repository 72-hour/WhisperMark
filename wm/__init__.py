"""
WhisperMark 2.0 — Agent 驱动的多平台 AI 阅卷 CLI
==================================================
架构分层：
  - engine      ：阅卷引擎（平台无关，依赖 platform adapter）
  - platforms   ：平台适配器（智学网 / 阅小二），翻译自 AI-Marker-Suite
  - monitor     ：本地监控服务器（HTTP + SSE + 临时 HTML）
  - notify      ：微信推送（workbuddy 小龙虾 / 通用 webhook）
  - utils       ：图片处理、日志等工具

原则：业务逻辑全部在引擎/适配器层；CLI 与 skill 只是薄封装。
"""

__version__ = "2.0.0"
__all__ = ["__version__"]
