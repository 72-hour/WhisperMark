#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境自检：Python 版本、依赖包、AI 配置、Playwright 浏览器。"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import bootstrap, project_home  # noqa: E402

REQUIRED_PACKAGES = {
    "playwright": "浏览器自动化（交互阅卷必需）",
    "openai": "AI 评分（必需）",
    "PIL": "图片处理（Pillow）",
}


def main():
    print("=" * 56)
    print("WhisperMark 环境自检")
    print("=" * 56)

    home = project_home()
    ok = True

    # 项目路径
    if home.is_dir():
        print(f"[OK]   项目路径: {home}")
        if not (home / "main.py").exists():
            print("[!]    警告: 项目下未找到 main.py")
            ok = False
    else:
        print(f"[X]    项目路径不存在: {home}")
        return 1

    # Python 版本
    v = sys.version_info
    ver_ok = v >= (3, 8)
    print(f"[{'OK' if ver_ok else 'X'}]   Python {v.major}.{v.minor}.{v.micro}")
    ok = ok and ver_ok

    # 依赖包
    for pkg, desc in REQUIRED_PACKAGES.items():
        try:
            __import__(pkg)
            print(f"[OK]   依赖 {pkg:<12} {desc}")
        except ImportError:
            print(f"[X]    依赖 {pkg:<12} 缺失 - {desc}")
            print(f"       安装: pip install -r \"{home / 'requirements.txt'}\"")
            ok = False

    # AI 配置
    key = os.environ.get("AI_API_KEY", "")
    if key:
        shown = key[:8] + "*" * max(0, len(key) - 8)
        print(f"[OK]   AI_API_KEY   {shown}")
    else:
        try:
            bootstrap()
            from src.config import config

            key = config.AI_API_KEY
        except Exception:
            key = ""
        if key:
            shown = key[:8] + "*" * max(0, len(key) - 8)
            print(f"[OK]   AI_API_KEY   {shown} (来自 src/config.py)")
        else:
            print("[X]    AI_API_KEY   未配置")
            ok = False

    print(f"[i]    AI_BASE_URL  {os.environ.get('AI_BASE_URL', '(默认)')}")
    print(f"[i]    AI_MODEL     {os.environ.get('AI_MODEL', '(默认)')}")

    # Playwright 浏览器
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("[i]    Playwright 未安装，跳过浏览器检查")
    else:
        cache = Path.home() / "AppData" / "Local" / "ms-playwright"
        if cache.is_dir() and any(cache.iterdir()):
            print(f"[OK]   Playwright 浏览器缓存: {cache}")
        else:
            print("[!]    Playwright 浏览器未下载")
            print("       安装: playwright install chromium")
            ok = False

    print("=" * 56)
    if ok:
        print("[OK] 环境就绪")
        print("     无人值守评分已可用；交互阅卷请在自己的终端运行:")
        print(f"     cd \"{home}\" && python main.py --max-score 13")
        return 0
    print("[X] 环境未就绪，请按上方提示修复")
    return 1


if __name__ == "__main__":
    sys.exit(main())