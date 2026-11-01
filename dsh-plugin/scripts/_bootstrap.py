"""公共引导模块：把 WhisperMark 项目加入 sys.path。

必须在 import src.* 之前调用，因为 src.config 在模块加载时就读取环境变量。
"""
import os
import sys
from pathlib import Path

# 兼容历史绝对路径，仅作为最后兜底。
_LEGACY_HOME = r"C:\Users\YYQ\projects\faint-studio\WhisperMark"


def project_home() -> Path:
    """解析 WhisperMark CLI 项目根目录（含 src/ 与 main.py 的目录）。

    优先级：环境变量 WHISPERMARK_HOME → 自动探测 → 历史绝对路径兜底。
    自动探测兼容两种布局（DSH 插件优先、CLI 为辅）：
      - 仓库内子目录   <repo>/dsh-plugin/scripts/  -> <repo>（dsh-plugin/.. ）
      - 独立插件目录   同级 _wm_probe/scripts/      -> 同级 WhisperMark/
    """
    env = os.environ.get("WHISPERMARK_HOME")
    if env:
        return Path(env)

    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent,                 # dsh-plugin/scripts -> 仓库根（或 _wm_probe/scripts -> 上级）
        here.parent.parent.parent / "WhisperMark", # 独立布局：上级 / WhisperMark
    ]
    for candidate in candidates:
        if (candidate / "src").is_dir() and (candidate / "main.py").exists():
            return candidate
    return Path(_LEGACY_HOME)


def bootstrap() -> Path:
    """将项目根目录加入 sys.path，返回项目根目录。"""
    home = project_home()
    if not home.is_dir():
        raise SystemExit(
            f"[X] 找不到 WhisperMark 项目: {home}\n"
            f"    请设置环境变量 WHISPERMARK_HOME 指向 WhisperMark 项目根目录"
        )
    home_str = str(home)
    if home_str not in sys.path:
        sys.path.insert(0, home_str)
    return home


def apply_ai_overrides(base_url=None, model=None, api_key=None):
    """把命令行传入的 AI 配置写入环境变量，供 src.config 读取。"""
    if api_key:
        os.environ["AI_API_KEY"] = api_key
    if base_url:
        os.environ["AI_BASE_URL"] = base_url
    if model:
        os.environ["AI_MODEL"] = model


def load_ai_grader():
    """返回 (ai_grader, config)。"""
    bootstrap()
    try:
        from src.config import config
        from src.ai_grader import ai_grader
    except ImportError as e:
        raise SystemExit(
            f"[X] 导入 WhisperMark 模块失败: {e}\n"
            f"    请先安装依赖: pip install -r {project_home() / 'requirements.txt'}"
        )
    if not config.AI_API_KEY:
        raise SystemExit(
            "[X] 未配置 AI_API_KEY。\n"
            "    方式一: setx AI_API_KEY sk-xxx\n"
            "    方式二: --api-key 参数传入\n"
            f"    方式三: 修改 {project_home() / 'src' / 'config.py'}"
        )
    return ai_grader, config


def collect_images(paths) -> list:
    """展开参数为图片文件列表（支持目录）。"""
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    files = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files.extend(sorted(f for f in path.iterdir() if f.suffix.lower() in exts))
        elif path.is_file():
            files.append(path)
        else:
            print(f"[!] 跳过不存在的路径: {p}", file=sys.stderr)
    return files


def dump_json(obj):
    import json
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(json.dumps(obj, ensure_ascii=False, indent=2))