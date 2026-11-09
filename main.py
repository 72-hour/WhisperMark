#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WhisperMark 2.0 入口
====================
Agent 驱动的多平台 AI 阅卷 CLI。

用法：
  python main.py check-env          # 环境自检
  python main.py demo               # 虚拟批改演示（无需浏览器 / AI Key，先看监控页）
  python main.py analyze-question --image 题干.jpg --platform zhixue --max-score 20
  python main.py grade --platform zhixue --rubric-id <id> --auto
  python main.py monitor --port 8321
  python main.py export --format html
"""
import sys
from pathlib import Path

# Windows 终端颜色支持（可选）
if sys.platform == "win32":
    try:
        import os
        os.system("")
    except Exception:
        pass

# 确保可从项目根目录导入 wm 包
sys.path.insert(0, str(Path(__file__).parent))

from wm.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
