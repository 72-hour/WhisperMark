"""
CLI 命令层（WhisperMark 2.0）
==================================================
薄封装：解析命令行参数，编排引擎 / 适配器 / 监控 / 通知。
所有业务逻辑都在引擎层，CLI 只负责「调起来」。

命令：
  check-env        环境自检（Python/依赖/AI Key/浏览器/监控端口）
  analyze-question 分析题干生成结构化评分细则（落盘 JSON + MD）
  grade            开始阅卷（核心）
  demo             虚拟批改演示（不需要浏览器 / AI Key）
  monitor          独立启动监控服务器
  export           导出批改历史（JSON/CSV/HTML）
"""
import argparse
import csv
import html as html_lib
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .config import config
from .utils.logging import logger
from .engine.ai_grader import ai_grader
from .engine.rubric import rubric_manager
from .engine.state import (
    state_manager, STATUS_DONE, STATUS_STOPPED,
)
from .engine.grader import Grader
from .platforms import ADAPTERS, get_adapter_by_id, detect_adapter
from .monitor import monitor_server
from .notify import wechat_notifier


# ============================================================
# check-env
# ============================================================
def _chromium_installed() -> bool:
    """粗略检查 Playwright 的 Chromium 内核是否已下载（不启动浏览器）"""
    roots = []
    env = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if env:
        roots.append(Path(env).expanduser())
    local = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    roots.append(Path(local) / "ms-playwright")
    roots.append(Path.home() / ".cache" / "ms-playwright")
    for root in roots:
        try:
            if root.exists():
                for p in root.iterdir():
                    if p.is_dir() and p.name.lower().startswith(("chromium", "chromium_headless_shell")):
                        return True
        except OSError:
            continue
    return False


def _port_available(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.4)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def cmd_check_env(args):
    print("=" * 52)
    print(" WhisperMark 2.0 环境自检")
    print("=" * 52)
    ok = True

    # Python
    py_ok = sys.version_info >= (3, 8)
    ok = ok and py_ok
    print(f"[{'OK' if py_ok else 'X'}] Python {sys.version.split()[0]}")

    # playwright
    try:
        import playwright  # noqa: F401
        print(f"[OK] playwright {getattr(playwright, '__version__', '')}".rstrip())
        if _chromium_installed():
            print("[OK] Chromium 内核已安装")
        else:
            print("[!] 未找到 Chromium 内核（实际阅卷前请执行：playwright install chromium）")
    except ImportError:
        ok = False
        print("[X] playwright 未安装（pip install -r requirements.txt）")

    # openai / Pillow
    try:
        import openai
        print(f"[OK] openai {getattr(openai, '__version__', '')}".rstrip())
    except ImportError:
        ok = False
        print("[X] openai 未安装（pip install openai）")
    try:
        import PIL  # noqa: F401
        print("[OK] Pillow")
    except ImportError:
        print("[!] Pillow 未安装（图片旋转/压缩功能受限）")

    # AI Key
    print(f"[{'OK' if config.ai_ready else 'X'}] AI API Key"
          + (f"（模型 {config.AI_MODEL}）" if config.ai_ready else
             "（未设置 WM_AI_API_KEY / AI_API_KEY，可用 demo 命令离线体验）"))
    if not config.ai_ready:
        ok = False

    # 微信
    print(f"[{'OK' if config.wechat_ready else '-'}] 微信推送"
          + ("（已启用）" if config.wechat_ready else
             "（可选，未配置 WM_WECHAT_WEBHOOK_URL）"))

    # 平台
    print(f"[OK] 平台适配器：{', '.join(a.id for a in ADAPTERS)}")

    # 监控端口
    port_ok = _port_available(config.MONITOR_HOST, config.MONITOR_PORT)
    print(f"[{'OK' if port_ok else '!'}] 监控端口 {config.MONITOR_HOST}:{config.MONITOR_PORT}"
          + ("" if port_ok else " 已被占用（启动时会自动顺延）"))

    # 数据目录
    print(f"[OK] 数据目录：{config.DATA_DIR}")
    print("=" * 52)
    if config.ai_ready and ok:
        print("环境就绪。可直接运行：python main.py demo  （虚拟体验）")
        print("或：python main.py grade --platform zhixue --rubric-id <细则ID> --auto")
    else:
        print("提示：缺少 AI Key 时仍可运行 `python main.py demo` 完整体验监控与流程。")
    return 0


# ============================================================
# analyze-question
# ============================================================
def _fmt_points(points) -> str:
    if not points:
        return "（未结构化，将使用原始细则文本锚定）"
    lines = []
    for p in points:
        kw = f"  关键词：{'、'.join(p.keywords)}" if p.keywords else ""
        lines.append(f"  {p.id}. {p.description}  [{p.score} 分]{kw}")
    return "\n".join(lines)


def cmd_analyze_question(args):
    img_path = Path(args.image)
    if not img_path.is_file():
        print(f"[X] 文件不存在: {img_path}")
        return 1
    try:
        img_data = img_path.read_bytes()
    except OSError as e:
        print(f"[X] 读取图片失败: {e}")
        return 1

    max_score = float(args.max_score or config.MAX_SCORE)
    print(f"[*] 正在分析题干（满分 {max_score} 分）…")
    analysis = ai_grader.analyze_question(img_data, max_score)

    if not analysis.ok:
        print(f"[X] 分析失败：{analysis.error or 'AI 未返回可解析结果'}")
        if analysis.raw_response:
            print(f"    原始返回（前 500 字）：\n{analysis.raw_response[:500]}")
        return 1

    qid = args.question_id or img_path.stem
    rubric = rubric_manager.build_rubric_from_raw(
        question_id=qid,
        platform=args.platform or "unknown",
        total_score=max_score,
        standard_answer=analysis.model_answer,
        analysis=analysis.explanation,
        raw_rubric=analysis.scoring_rubric,
        grading_points=analysis.grading_points,
    )
    try:
        json_path = rubric_manager.save(rubric)
        md_path = rubric_manager._md_file_path(qid)  # save() 已同步导出 MD
    except OSError as e:
        print(f"[X] 细则保存失败: {e}")
        return 1

    print("\n" + "=" * 52)
    print(f"标准答案:\n{analysis.model_answer or '（空）'}")
    print(f"\n解析:\n{analysis.explanation or '（空）'}")
    print(f"\n评分细则:\n{_fmt_points(analysis.grading_points)}")
    if analysis.scoring_rubric:
        print(f"\n原始细则文本:\n{analysis.scoring_rubric}")
    print("=" * 52)
    for w in analysis.warnings:
        print(f"[!] {w}")
    print("\n[OK] 细则已保存:")
    print(f"  JSON: {json_path}")
    print(f"  MD  : {md_path}")

    if args.confirm:
        rubric_manager.lock(rubric)
        print(f"[OK] 已锁定，版本 {rubric.version}")
    else:
        print("[!] 请教师检查 MD 后，用 --confirm 重新运行或手动把 JSON 的 locked 置 true")
    return 0


# ============================================================
# grade
# ============================================================
def _resolve_adapter(browser, platform_id: Optional[str], url: Optional[str] = None):
    """解析平台适配器：指定 or 自动检测"""
    if platform_id and platform_id != "auto":
        adapter = get_adapter_by_id(platform_id, browser)
        if not adapter:
            ids = " / ".join(a.id for a in ADAPTERS)
            print(f"[X] 未知平台: {platform_id}（可选：{ids}）")
            return None
        return adapter

    adapter = detect_adapter(browser)
    if adapter:
        print(f"[*] 自动识别平台：{adapter.name}")
        return adapter
    if url:
        print(f"[*] 打开 {url} 后重新识别平台…")
        browser.goto(url)
        time.sleep(1)
        adapter = detect_adapter(browser)
        if adapter:
            print(f"[*] 自动识别平台：{adapter.name}")
            return adapter
    print("[X] 无法自动识别平台：浏览器当前还没有打开任何阅卷平台页面。")
    print("    请用 --platform zhixue|haoyuejuan 显式指定，或用 --url 指定登录页。")
    return None


def _load_rubric(args, adapter, max_score: float):
    """加载或生成评分细则；返回 (rubric, 错误信息)"""
    if args.question_image:
        img_path = Path(args.question_image)
        if not img_path.is_file():
            return None, f"题干图片不存在: {img_path}"
        try:
            img_data = img_path.read_bytes()
        except OSError as e:
            return None, f"读取题干图片失败: {e}"
        print(f"[*] 生成评分细则（满分 {max_score}）…")
        analysis = ai_grader.analyze_question(img_data, max_score)
        if not analysis.ok:
            return None, f"细则生成失败：{analysis.error or 'AI 返回不可解析'}"
        qid = args.rubric_id or img_path.stem
        rubric = rubric_manager.build_rubric_from_raw(
            question_id=qid,
            platform=adapter.id,
            total_score=max_score,
            standard_answer=analysis.model_answer,
            analysis=analysis.explanation,
            raw_rubric=analysis.scoring_rubric,
            grading_points=analysis.grading_points,
        )
        for w in analysis.warnings:
            print(f"[!] {w}")
        try:
            rubric_manager.save(rubric)
            rubric_manager.lock(rubric)  # 无人值守场景：生成即锁定
        except OSError as e:
            return None, f"细则保存失败: {e}"
        print(f"[OK] 细则已生成并锁定（{qid}，版本 {rubric.version}）")
        return rubric, ""

    if args.rubric_id:
        rubric = rubric_manager.load(args.rubric_id)
        if rubric is None:
            print(f"[!] 未找到细则 {args.rubric_id}，本次批改将不锚定（AI 低置信度）")
            return None, ""
        print(f"[OK] 已加载细则（{rubric.question_id}，版本 {rubric.version}，"
              f"{'已锁定' if rubric.locked else '未锁定'}）")
        if not rubric.locked:
            print("[!] 细则未锁定：建议教师确认后锁定，避免评分标准漂移")
        for w in rubric.validate():
            print(f"[!] {w}")
        return rubric, ""
    print("[!] 未提供 --rubric-id / --question-image：本次批改不锚定评分细则")
    return None, ""


class _ConsoleController(threading.Thread):
    """交互控制台（后台线程读键盘命令，不碰 Playwright）"""

    def __init__(self, grader: Grader):
        super().__init__(name="console-controller", daemon=True)
        self.grader = grader
        self._alive = True

    def run(self):
        print("命令：q=退出  p=暂停  r=继续  s=状态", flush=True)
        while self._alive and self.grader.is_running:
            try:
                cmd = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                break
            if cmd in ("q", "quit", "exit"):
                self.grader.stop()
                break
            if cmd in ("p", "pause"):
                self.grader.pause()
            elif cmd in ("r", "resume", "c", "continue"):
                self.grader.resume()
            elif cmd in ("s", "status"):
                s = state_manager.snapshot()
                brief = {k: s[k] for k in ("status", "graded", "total", "percentage",
                                           "current_student", "current_score",
                                           "avg_score")}
                print(json.dumps(brief, ensure_ascii=False, indent=2))
            elif cmd:
                print("命令：q=退出  p=暂停  r=继续  s=状态")

    def stop(self):
        self._alive = False


def _save_history(prefix: str = "") -> Optional[Path]:
    """保存批改历史到 data/history/"""
    try:
        config.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = config.HISTORY_DIR / f"{prefix}{ts}.json"
        path.write_text(json.dumps(state_manager.snapshot(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        logger.info("批改历史已保存: %s", path)
        return path
    except OSError as e:
        logger.error("保存批改历史失败: %s", e)
        print(f"[!] 批改历史保存失败: {e}")
        return None


def cmd_grade(args):
    # 1) 监控服务器
    monitor_url = None
    if config.MONITOR_ENABLED and not args.no_monitor:
        try:
            monitor_url = monitor_server.start(port=args.monitor_port)
            print(f"[*] 监控页面: {monitor_url}")
        except Exception as e:
            print(f"[!] 监控服务器启动失败（不影响阅卷）: {e}")

    # 2) 微信推送
    if config.wechat_ready:
        wechat_notifier.reset()
        wechat_notifier.start()

    # 3) 浏览器
    from .browser import BrowserManager  # 惰性导入：未装 playwright 时也能跑 demo/check-env
    browser = BrowserManager()
    try:
        browser.start()
    except Exception as e:
        print(f"[X] 浏览器启动失败: {e}")
        if config.wechat_ready:
            wechat_notifier.stop()
        if monitor_url:
            monitor_server.stop()
        return 1

    grader: Optional[Grader] = None
    controller: Optional[_ConsoleController] = None
    interrupted = False
    exit_code = 0
    try:
        adapter = _resolve_adapter(browser, args.platform, args.url)
        if adapter is None:
            exit_code = 1
            raise _GradeAbort

        print(f"[*] 平台: {adapter.name}")
        login_url = args.url or adapter.login_url
        if login_url:
            print(f"[*] 打开 {login_url}")
            if not browser.goto(login_url):
                print("[!] 登录页加载失败或超时，请检查网络后重试")

        if not args.auto:
            print("\n" + "=" * 52)
            print("请在浏览器中完成：登录 → 进入阅卷中心 → 选择题目 → 看到答题卡")
            print("=" * 52)
            try:
                input("准备好后按回车继续…")
            except (EOFError, KeyboardInterrupt):
                print("\n[!] 已取消")
                exit_code = 130
                raise _GradeAbort

        if not adapter.detect_marking_page():
            print("[!] 未检测到批改页面，请确认已进入阅卷界面")
            if args.auto:
                print("[X] 自动模式下未检测到批改页面，退出")
                exit_code = 1
                raise _GradeAbort
            try:
                input("已确认进入批改页面？按回车强制继续…")
            except (EOFError, KeyboardInterrupt):
                print("\n[!] 已取消")
                exit_code = 130
                raise _GradeAbort

        # 4) 满分 + 细则
        grader = Grader(browser, adapter)
        if args.max_score and args.max_score > 0:
            grader.max_score_override = float(args.max_score)
        max_score = grader.read_max_score()
        print(f"[*] 满分: {max_score}")

        rubric, err = _load_rubric(args, adapter, max_score)
        if err:
            print(f"[X] {err}")
            exit_code = 1
            raise _GradeAbort
        grader.rubric = rubric

        # 5) 状态初始化
        qid = adapter.get_task_identifier() or args.rubric_id or "unknown"
        state_manager.start(
            platform=adapter.id,
            question_id=qid,
            total=args.total or 0,
            rubric_version=rubric.version if rubric else "",
        )

        # 6) 主线程跑批改循环（Playwright 同步 API 只能在同一线程使用）
        if args.auto:
            print("[*] 自动批改中…（Ctrl+C 停止）")
        else:
            controller = _ConsoleController(grader)
            controller.start()
            grader.print_progress = False
        try:
            grader.run_forever()
        except KeyboardInterrupt:
            interrupted = True
            print("\n[!] 收到中断，正在停止…")
            grader.stop()
            grader.join(timeout=15)

    except _GradeAbort:
        pass
    except KeyboardInterrupt:
        interrupted = True
        print("\n[!] 已中断")
    except Exception as e:
        logger.exception("阅卷失败")
        print(f"[X] 阅卷失败: {type(e).__name__}: {e}")
        exit_code = 1
    finally:
        if controller is not None:
            controller.stop()
        if grader is not None:
            grader.stop()
            grader.join(timeout=15)

        # 收尾状态：确保前端不会永远停在 running
        snap = state_manager.snapshot()
        if snap["status"] in ("running", "paused", "idle") and snap["graded"] > 0:
            state_manager.finish(STATUS_STOPPED if interrupted else STATUS_DONE)
        elif snap["status"] in ("running", "paused") and snap["graded"] == 0:
            state_manager.set_status(STATUS_STOPPED)

        snap = state_manager.snapshot()
        if snap["graded"] > 0:
            history_path = _save_history()
            if history_path:
                print(f"[*] 批改历史已保存: {history_path}")
        else:
            print("[!] 本次没有成功批改任何答卷，未写入历史")
        snap = state_manager.snapshot()
        print(f"[*] 本次共批改 {snap['graded']} 份，平均分 {snap['avg_score']}，"
              f"异常 {len(snap['errors'])} 条")
        browser.stop()
        if config.wechat_ready:
            wechat_notifier.stop()
        if monitor_url:
            print("[*] 监控服务器已停止（如需保留请用 python main.py monitor 单独启动）")
            monitor_server.stop()
    return exit_code


class _GradeAbort(Exception):
    """内部：用于跳出 grade 流程但继续走 finally 清理"""


# ============================================================
# monitor
# ============================================================
def cmd_monitor(args):
    try:
        url = monitor_server.start(host=args.host, port=args.port)
    except Exception as e:
        print(f"[X] 监控服务器启动失败: {e}")
        return 1
    print(f"[*] 监控服务器: {url}")
    print("    浏览器打开上面的地址查看实时阅卷状态；按 Ctrl+C 停止")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[*] 正在停止监控服务器…")
    finally:
        monitor_server.stop()
    return 0


# ============================================================
# export
# ============================================================
def _pick_history_file(input_path: Optional[str]) -> Optional[Path]:
    if input_path:
        p = Path(input_path)
        return p if p.is_file() else None
    try:
        files = sorted(config.HISTORY_DIR.glob("*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        files = []
    return files[0] if files else None


def _export_csv(data: dict, out: Path) -> int:
    records = data.get("records", []) or []
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["序号", "学生", "得分", "满分", "置信度", "细则版本", "理由", "时间"])
        for r in records:
            w.writerow([r.get("index"), r.get("student_name"), r.get("score"),
                        r.get("max_score"), r.get("confidence"), r.get("rubric_version"),
                        r.get("reasoning"), r.get("timestamp")])
    return len(records)


def _export_html(data: dict, out: Path) -> int:
    """导出独立 HTML 报告（所有动态内容必须转义）"""
    esc = html_lib.escape
    records = data.get("records", []) or []
    errors = data.get("errors", []) or []
    graded = int(data.get("graded", len(records)) or 0)
    total = int(data.get("total", 0) or 0)
    pct = data.get("percentage", 0) or 0
    avg = data.get("avg_score", 0) or 0
    confidence_count = {"high": 0, "medium": 0, "low": 0, "error": 0}
    for r in records:
        c = str(r.get("confidence", "") or "")
        if c in confidence_count:
            confidence_count[c] += 1
    status_map = {"done": "已完成", "stopped": "已停止", "error": "出错",
                  "running": "批改中", "paused": "已暂停", "idle": "待机"}
    status_text = status_map.get(str(data.get("status", "")), "未知")

    rows = []
    for r in records:
        score = r.get("score", 0)
        max_s = r.get("max_score", 0) or 1
        try:
            ratio = max(0.0, min(float(score) / float(max_s) * 100, 100))
        except (TypeError, ValueError, ZeroDivisionError):
            ratio = 0
        conf = str(r.get("confidence", "") or "")
        rows.append(
            "<tr>"
            f"<td class=\"num\">{esc(str(r.get('index', '')))}</td>"
            f"<td>{esc(str(r.get('student_name', '') or '—'))}</td>"
            f"<td class=\"score\">{esc(str(score))}<span class=\"mut\">/{esc(str(r.get('max_score', '')))}</span>"
            f"<div class=\"mini\"><i style=\"width:{ratio:.0f}%\"></i></div></td>"
            f"<td><span class=\"chip {esc(conf)}\">{esc(conf or '—')}</span></td>"
            f"<td class=\"reason\">{esc(str(r.get('reasoning', '') or ''))}</td>"
            f"<td class=\"mut time\">{esc(str(r.get('timestamp', '') or ''))}</td>"
            "</tr>")
    error_rows = "".join(
        f"<li><span class=\"time\">{esc(str(e.get('time', '')))}</span>"
        f"{esc(str(e.get('message', '')))}</li>"
        for e in errors) or "<li class=\"empty\">无异常</li>"

    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WhisperMark 2.0 · 批改报告</title>
<style>
 :root{{--bg:#0b1020;--panel:#121a2e;--panel2:#0f1626;--line:#26304a;--text:#e8eefc;
        --muted:#8fa0c0;--accent:#6d8cff;--accent2:#39d0d8;--green:#3ddc97;--yellow:#f5c451;--red:#ff6b6b}}
 *{{box-sizing:border-box}}
 body{{margin:0;background:radial-gradient(1200px 600px at 20% -10%,#1b2a52 0,transparent 60%),var(--bg);
       color:var(--text);font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;padding:40px 24px}}
 .wrap{{max-width:1120px;margin:0 auto}}
 header{{display:flex;justify-content:space-between;align-items:flex-end;gap:24px;flex-wrap:wrap;margin-bottom:28px}}
 h1{{font-size:26px;margin:0 0 6px;letter-spacing:.3px}}
 .sub{{color:var(--muted);font-size:13.5px}}
 .badge{{padding:6px 14px;border-radius:999px;font-size:13px;font-weight:600;
         background:rgba(109,140,255,.14);color:#a9bcff;border:1px solid rgba(109,140,255,.3)}}
 .kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:24px}}
 .kpi{{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
       border-radius:14px;padding:16px 18px}}
 .kpi .l{{font-size:12px;color:var(--muted);margin-bottom:8px}}
 .kpi .v{{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}}
 .kpi .v.g{{color:var(--green)}} .kpi .v.b{{color:#9db4ff}} .kpi .v.y{{color:var(--yellow)}}
 .panel{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px;margin-bottom:22px}}
 .panel h2{{font-size:15px;margin:0 0 14px;color:#dfe8ff}}
 table{{width:100%;border-collapse:collapse;font-size:13.5px}}
 th{{text-align:left;color:var(--muted);font-weight:500;padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap}}
 td{{padding:10px 12px;border-bottom:1px solid rgba(38,48,74,.55);vertical-align:top}}
 tr:last-child td{{border-bottom:none}}
 .num{{font-variant-numeric:tabular-nums;color:var(--muted)}}
 .score{{font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}}
 .mut{{color:var(--muted);font-weight:400}}
 .time{{font-size:12px;white-space:nowrap}}
 .reason{{color:#c9d6f0;line-height:1.5;max-width:420px}}
 .mini{{height:4px;border-radius:99px;background:#1b2742;margin-top:6px;overflow:hidden;width:92px}}
 .mini i{{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2))}}
 .chip{{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;border:1px solid transparent}}
 .chip.high{{background:rgba(61,220,151,.13);color:var(--green);border-color:rgba(61,220,151,.3)}}
 .chip.medium{{background:rgba(245,196,81,.13);color:var(--yellow);border-color:rgba(245,196,81,.3)}}
 .chip.low{{background:rgba(255,107,107,.13);color:var(--red);border-color:rgba(255,107,107,.3)}}
 .chip.error{{background:rgba(255,107,107,.2);color:#ffb4b4;border-color:rgba(255,107,107,.45)}}
 ul.errors{{list-style:none;margin:0;padding:0}}
 ul.errors li{{padding:9px 12px;border-left:3px solid var(--red);background:rgba(255,107,107,.07);
              border-radius:8px;margin-bottom:8px;font-size:13px;color:#ffd4d4}}
 ul.errors li .time{{margin-right:8px;color:var(--muted)}}
 ul.errors li.empty{{border-left-color:var(--line);background:transparent;color:var(--muted)}}
 footer{{color:var(--muted);font-size:12.5px;text-align:center;margin-top:28px}}
 @media(max-width:820px){{.kpis{{grid-template-columns:repeat(2,1fr)}} body{{padding:24px 14px}} .reason{{max-width:none}}}}
 @media print{{body{{background:#fff;color:#111}} .panel,.kpi{{border-color:#ddd;background:#fff}}}}
</style></head><body><div class="wrap">
<header>
  <div><h1>WhisperMark 2.0 · 批改报告</h1>
    <div class="sub">{esc(str(data.get('platform','') or '未知平台'))} ·
      <b>{esc(str(data.get('question_id','') or '未命名题目'))}</b> ·
      细则版本 {esc(str(data.get('rubric_version','') or '—'))} ·
      {esc(str(data.get('started_at','') or ''))}</div></div>
  <div class="badge">{esc(status_text)} · {graded}{('/' + str(total)) if total else ''} 份</div>
</header>
<div class="kpis">
  <div class="kpi"><div class="l">已批份数</div><div class="v g">{graded}</div></div>
  <div class="kpi"><div class="l">总份数</div><div class="v">{total or '—'}</div></div>
  <div class="kpi"><div class="l">进度</div><div class="v b">{pct}%</div></div>
  <div class="kpi"><div class="l">平均分</div><div class="v">{avg}</div></div>
  <div class="kpi"><div class="l">异常</div><div class="v {'y' if errors else ''}">{len(errors)}</div></div>
</div>
<div class="panel">
  <h2>置信度分布</h2>
  <div class="sub">高：{confidence_count['high']}　中：{confidence_count['medium']}　
    低：{confidence_count['low']}　错误：{confidence_count['error']}</div>
</div>
<div class="panel">
  <h2>异常记录（{len(errors)}）</h2>
  <ul class="errors">{error_rows}</ul>
</div>
<div class="panel">
  <h2>批改明细（{len(records)} 条）</h2>
  <div style="overflow-x:auto"><table>
    <thead><tr><th>#</th><th>学生</th><th>得分</th><th>置信度</th><th>评分理由</th><th>时间</th></tr></thead>
    <tbody>{''.join(rows) or '<tr><td colspan="6" class="mut">暂无记录</td></tr>'}</tbody>
  </table></div>
</div>
<footer>WhisperMark 2.0 · AI 评分结果仅供参考，请教师复核后提交</footer>
</div></body></html>"""
    out.write_text(html, encoding="utf-8")
    return len(records)


def cmd_export(args):
    path = _pick_history_file(args.input)
    if path is None:
        print(f"[!] 未找到批改历史（目录：{config.HISTORY_DIR}）")
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[X] 读取历史失败 {path}: {e}")
        return 1
    if not isinstance(data, dict):
        print(f"[X] 历史文件格式不正确: {path}")
        return 1

    print(f"[*] 历史文件: {path}")
    out = Path(args.output) if args.output else None
    if args.format == "json":
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if out:
            out.write_text(text, encoding="utf-8")
            print(f"[OK] 已导出 JSON: {out}")
        else:
            print(text)
    elif args.format == "csv":
        out = out or Path(f"history_{path.stem}.csv")
        n = _export_csv(data, out)
        print(f"[OK] 已导出 CSV: {out}（{n} 条记录）")
    elif args.format == "html":
        out = out or Path(f"history_{path.stem}.html")
        n = _export_html(data, out)
        print(f"[OK] 已导出 HTML: {out}（{n} 条记录）")
    return 0


# ============================================================
# demo
# ============================================================
def cmd_demo(args):
    from .demo import run_demo
    return run_demo(
        papers=args.papers,
        interval=args.interval,
        max_score=args.max_score,
        platform=args.platform,
        question_id=args.question_id,
        seed=args.seed,
        monitor=not args.no_monitor,
        monitor_host=args.host,
        monitor_port=args.port,
        exit_after=args.exit,
    )


# ============================================================
# parser
# ============================================================
def build_parser():
    parser = argparse.ArgumentParser(
        prog="whispermark",
        description="WhisperMark 2.0 — Agent 驱动的多平台 AI 阅卷 CLI",
    )
    parser.add_argument("--version", action="version",
                        version=f"WhisperMark {config.APP_VERSION}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check-env", help="环境自检")

    p_analyze = sub.add_parser("analyze-question", help="分析题干生成评分细则")
    p_analyze.add_argument("--image", required=True, help="题干图片路径")
    p_analyze.add_argument("--question-id", help="题目唯一标识（默认用图片文件名）")
    p_analyze.add_argument("--platform", help="平台 id（zhixue/haoyuejuan）")
    p_analyze.add_argument("--max-score", type=float, help="满分")
    p_analyze.add_argument("--confirm", action="store_true", help="生成后直接锁定")

    p_grade = sub.add_parser("grade", help="开始阅卷")
    p_grade.add_argument("--platform", help="平台 id（zhixue/haoyuejuan/auto，默认自动检测）")
    p_grade.add_argument("--url", help="登录/入口 URL（默认用平台默认）")
    p_grade.add_argument("--rubric-id", help="已保存的细则 question_id（锚定）")
    p_grade.add_argument("--question-image", help="题干图片路径（现场生成并锁定细则）")
    p_grade.add_argument("--max-score", type=float, help="满分（默认从页面读取）")
    p_grade.add_argument("--total", type=int, help="总份数（可选，用于进度显示）")
    p_grade.add_argument("--auto", action="store_true", help="无人值守模式（跳过人工确认）")
    p_grade.add_argument("--headless", action="store_true", help="无头模式")
    p_grade.add_argument("--monitor-port", type=int, help="监控端口（默认 8321）")
    p_grade.add_argument("--no-monitor", action="store_true", help="禁用监控服务器")

    p_demo = sub.add_parser("demo", help="虚拟批改演示（无需浏览器 / AI Key）")
    p_demo.add_argument("--papers", type=int, default=200, help="模拟份数（默认 200）")
    p_demo.add_argument("--interval", type=float, default=0.15, help="每份间隔秒数（默认 0.15）")
    p_demo.add_argument("--max-score", type=float, default=20.0, help="满分（默认 20）")
    p_demo.add_argument("--platform", default="zhixue", help="模拟平台 id（默认 zhixue）")
    p_demo.add_argument("--question-id", default="数学第15题", help="模拟题目名称")
    p_demo.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    p_demo.add_argument("--host", default=None, help="监控监听地址")
    p_demo.add_argument("--port", type=int, default=None, help="监控端口（默认 8321）")
    p_demo.add_argument("--no-monitor", action="store_true", help="不启动监控服务器")
    p_demo.add_argument("--no-browser", action="store_true",
                        help="已废弃参数（演示模式本来就不启动浏览器），仅为兼容保留")
    p_demo.add_argument("--exit", action="store_true",
                        help="演示完成后退出（默认保持监控页面运行）")

    p_monitor = sub.add_parser("monitor", help="独立启动监控服务器")
    p_monitor.add_argument("--host", default=None, help="监听地址（默认 127.0.0.1）")
    p_monitor.add_argument("--port", type=int, default=None, help="端口（默认 8321）")

    p_export = sub.add_parser("export", help="导出批改历史")
    p_export.add_argument("--format", choices=["json", "csv", "html"], default="csv")
    p_export.add_argument("--output", help="输出文件路径")
    p_export.add_argument("--input", help="指定历史 JSON 文件（默认取最新一份）")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    if getattr(args, "headless", False):
        config.HEADLESS = True

    handlers = {
        "check-env": cmd_check_env,
        "analyze-question": cmd_analyze_question,
        "grade": cmd_grade,
        "demo": cmd_demo,
        "monitor": cmd_monitor,
        "export": cmd_export,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    try:
        return int(handler(args) or 0)
    except KeyboardInterrupt:
        print("\n[!] 已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
