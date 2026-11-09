"""
演示脚本（预览用，向后兼容入口）
==================================================
等价于：python main.py demo
启动监控服务器 + 模拟一批真实批改（不需要浏览器 / AI Key），
让展示页 / 监控页能看到真实的「批改中」效果。

用法：
  python demo_monitor.py
  python demo_monitor.py --papers 60 --interval 0.05
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from wm.demo import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
