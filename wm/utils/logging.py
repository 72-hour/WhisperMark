"""
日志模块
统一日志输出：控制台 + 文件（data/debug.log）
"""
import logging
import sys
from pathlib import Path
from datetime import datetime

from ..config import config

_configured = False


def get_logger(name: str = "wm") -> logging.Logger:
    global _configured
    logger = logging.getLogger(name)

    if not _configured:
        _configured = True
        logger.setLevel(logging.DEBUG)

        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 控制台 handler（INFO 级别）
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.INFO)
        console.setFormatter(fmt)
        logger.addHandler(console)

        # 文件 handler（DEBUG 级别）
        try:
            log_file = config.DATA_DIR / "debug.log"
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:
            pass

    return logger


logger = get_logger()


def debug_log(msg: str):
    """便捷调试日志写入（附加时间戳），供引擎快速记录"""
    try:
        log_file = config.DATA_DIR / "debug.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass
