"""公共引导模块：把 WhisperMark 2.0 项目加入 sys.path。

必须在 `import wm.*` 之前调用，因为 `wm.config` 在模块加载时就会读取环境变量。
插件既可以从仓库内 `dsh-plugin/` 直接运行，也可以从独立安装目录运行，
通过 WHISPERMARK_HOME 环境变量可显式指定 CLI 项目根目录。
"""
import os
import sys
from pathlib import Path


def project_home() -> Path:
    """解析 WhisperMark 2.0 项目根目录（含 `wm/` 包与 main.py 的目录）。

    优先级：环境变量 WHISPERMARK_HOME → 自动探测（仓库内 dsh-plugin/scripts → 上一级）
    → 兼容 `WhisperMark` / `WhisperMark2.0` 目录名。
    """
    env = os.environ.get("WHISPERMARK_HOME")
    if env:
        return Path(env).expanduser()

    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent
    candidates = [
        repo_root,                        # <repo>/dsh-plugin/scripts -> <repo>
        repo_root / "WhisperMark",        # 独立布局：同级 / WhisperMark
        repo_root / "WhisperMark2.0",     # 独立布局：同级 / WhisperMark2.0
    ]
    for candidate in candidates:
        if (candidate / "wm").is_dir() and (candidate / "main.py").exists():
            return candidate
    return candidates[0]


def bootstrap() -> Path:
    """将项目根目录加入 sys.path，返回项目根目录。"""
    home = project_home()
    if not home.is_dir():
        raise SystemExit(
            f"[X] 找不到 WhisperMark 2.0 项目: {home}\n"
            f"    请设置环境变量 WHISPERMARK_HOME 指向项目根目录"
        )
    home_str = str(home)
    if home_str not in sys.path:
        sys.path.insert(0, home_str)
    return home


def apply_ai_overrides(base_url=None, model=None, api_key=None):
    """把命令行传入的 AI 配置写入环境变量，供 wm.config 读取。

    同时写入 WM_AI_*（2.0 新变量）与 AI_*（旧变量，兼容历史配置/文档）。
    """
    if api_key:
        os.environ["WM_AI_API_KEY"] = api_key
        os.environ["AI_API_KEY"] = api_key
    if base_url:
        os.environ["WM_AI_BASE_URL"] = base_url
        os.environ["AI_BASE_URL"] = base_url
    if model:
        os.environ["WM_AI_MODEL"] = model
        os.environ["AI_MODEL"] = model


def load_ai_grader():
    """返回 (ai_grader, config)，并校验 AI Key 是否已配置。"""
    bootstrap()
    try:
        from wm.config import config
        from wm.engine.ai_grader import ai_grader
    except ImportError as e:
        raise SystemExit(
            f"[X] 导入 WhisperMark 模块失败: {e}\n"
            f"    请先安装依赖: pip install -r \"{project_home() / 'requirements.txt'}\""
        )
    if not config.AI_API_KEY:
        raise SystemExit(
            "[X] 未配置 AI Key。\n"
            "    方式一: 设置环境变量 WM_AI_API_KEY（或 AI_API_KEY）\n"
            "    方式二: 在 .env 中配置 WM_AI_API_KEY=sk-xxx\n"
            "    方式三: --api-key 参数传入"
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
    """向 stdout 输出 UTF-8 JSON（供 DSH 工具解析）。"""
    import json
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(json.dumps(obj, ensure_ascii=False, indent=2))
