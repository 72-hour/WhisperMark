"""
虚拟演示模式（WhisperMark 2.0）
==================================================
不启动浏览器、不调用 AI，完整模拟一次「批量阅卷」：
  - 真实通过 StateManager 上报状态（监控页 SSE / 进度 / 记录 / 异常全部联动）
  - 真实启动监控服务器，可在浏览器里看到实时进度
  - 结束后写入 data/history/*.json，可直接用 export 命令导出

用途：
  1. 首次安装后验证项目是否跑得通（无需 AI Key / 浏览器 / 平台账号）
  2. 向老师演示监控页面与批改流程
  3. 给 UI / 前端做真实数据联调的演示服务

用法：
  python main.py demo --papers 200 --interval 0.15
  python main.py demo --papers 20 --interval 0.05 --exit
"""
import json
import random
import sys
import time
from pathlib import Path
from typing import List, Optional

from .config import config
from .engine.rubric import Rubric, GradingPoint, rubric_manager
from .engine.state import (
    state_manager, GradingRecord,
    STATUS_DONE, STATUS_STOPPED,
)
from .monitor import monitor_server
from .notify import wechat_notifier

DEFAULT_NAMES = [
    "张子涵", "李雨桐", "王浩然", "刘思远", "陈欣怡", "杨博文", "赵若曦", "黄梓豪",
    "周语彤", "吴俊杰", "徐嘉怡", "孙一鸣", "马晨曦", "朱可欣", "胡明轩", "郭思彤",
    "何雨泽", "林诗涵", "高子墨", "罗宇轩", "郑雅琪", "梁俊熙", "谢婉清", "宋佳明",
    "唐若汐", "许泽宇", "邓子萱", "冯浩宇", "曾雨欣", "彭子睿",
]

REASON_PASS = [
    "采分点全部命中：公式书写正确，推导过程完整，结论与单位无误。",
    "解题步骤规范，关键采分点均命中，最终结果正确。",
    "思路清晰，计算准确，采分点逐项得分，书写规范。",
]
REASON_MID = [
    "前两步采分点命中，最后一步结论表述不完整，扣该点部分分值。",
    "主要步骤正确，中间计算存在一处笔误，相关采分点扣分。",
    "公式使用正确，但缺少必要的文字说明，按细则扣 1 分。",
]
REASON_LOW = [
    "仅列出已知条件，核心推导缺失，只得到起步分。",
    "关键采分点大部分未命中，结论错误，按细则给基础分。",
    "答题内容与采分点偏离较大，建议教师复核。",
]


def _demo_score(rng: random.Random, max_score: float) -> float:
    """生成一个接近真实分布的分数（0.5 分为粒度）"""
    ratio = rng.gauss(0.72, 0.16)
    ratio = min(max(ratio, 0.12), 1.0)
    score = round(max_score * ratio * 2) / 2  # 0.5 分粒度
    return max(0.0, min(score, max_score))


def build_demo_rubric(question_id: str, platform: str, max_score: float,
                      platform_name: str = "zhixue") -> Rubric:
    """构造一份与满分自洽的演示细则（不落盘，避免污染 data/rubrics）"""
    weights = [0.2, 0.3, 0.3, 0.2]
    descriptions = [
        "写出已知条件与所用公式（或定理），明确解题方向",
        "关键推导 / 计算步骤正确，中间过程无原则性错误",
        "得到正确结论，结论与题目要求一致",
        "书写规范、步骤完整，单位 / 取值范围标注正确",
    ]
    points: List[GradingPoint] = []
    used = 0.0
    for i, (weight, desc) in enumerate(zip(weights, descriptions), start=1):
        if i == len(weights):
            score = round(max_score - used, 2)
        else:
            score = round(max_score * weight, 2)
            used += score
        points.append(GradingPoint(
            id=i,
            description=desc,
            score=max(score, 0.0),
            keywords=["公式", "步骤", "结论"][: max(1, 4 - i)] or ["采分点"],
        ))
    return Rubric(
        question_id=question_id,
        platform=platform or platform_name,
        total_score=max_score,
        standard_answer="（演示数据）标准答案：按题目条件代入公式，分步求解并给出最终结论。",
        analysis="（演示数据）解析：先明确已知量与目标量，再选择公式，分步计算并检验结果。",
        grading_points=points,
        raw_text="（演示数据）按四个采分点逐项给分，总分与满分一致。",
        version="1.1",
        locked=True,
    )


def _confidence_for(rng: random.Random, score: float, max_score: float) -> str:
    ratio = (score / max_score) if max_score > 0 else 0
    roll = rng.random()
    if roll < 0.04:
        return "low"
    if ratio >= 0.82:
        return "high" if roll < 0.92 else "medium"
    if ratio >= 0.6:
        return "medium" if roll < 0.9 else "high"
    return "low" if roll > 0.25 else "medium"


def _reasoning_for(rng: random.Random, score: float, max_score: float) -> str:
    ratio = (score / max_score) if max_score > 0 else 0
    if ratio >= 0.8:
        return rng.choice(REASON_PASS)
    if ratio >= 0.55:
        return rng.choice(REASON_MID)
    return rng.choice(REASON_LOW)


def _progress_bar(done: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return ""
    filled = int(width * done / total)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _save_demo_history() -> Optional[Path]:
    """把演示结果按正式历史格式落盘，方便 export 验证"""
    try:
        config.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = config.HISTORY_DIR / f"demo_{ts}.json"
        data = state_manager.snapshot()
        data["demo"] = True
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except OSError as e:
        print(f"[!] 演示历史保存失败（不影响演示）: {e}")
        return None


def run_demo(papers: int = 200,
             interval: float = 0.15,
             max_score: float = 20.0,
             platform: str = "zhixue",
             question_id: str = "数学第15题",
             seed: int = 42,
             monitor: bool = True,
             monitor_host: Optional[str] = None,
             monitor_port: Optional[int] = None,
             exit_after: bool = False,
             save_history: bool = True) -> int:
    """运行一次虚拟批改演示。返回进程退出码（0 成功）。"""
    papers = max(int(papers), 1)
    interval = max(float(interval), 0.0)
    max_score = float(max_score or 20.0)
    rng = random.Random(seed)

    # 优先复用已存在的同名细则（这样演示的「锚定版本」和真实批改一致）
    rubric = rubric_manager.load(question_id)
    if not rubric or rubric.total_score <= 0:
        rubric = build_demo_rubric(question_id, platform, max_score)

    print("=" * 62, flush=True)
    print(" WhisperMark 2.0 · 虚拟批改演示（不需要浏览器 / AI Key）", flush=True)
    print(f" 题目：{question_id}    平台：{platform}    满分：{rubric.total_score}", flush=True)
    print(f" 份数：{papers}    每份间隔：{interval}s    细则版本：{rubric.version}", flush=True)
    print("=" * 62, flush=True)

    # 1) 启动监控服务器（demo 的目的就是看页面，除非显式 --no-monitor 否则总是启动）
    monitor_url = None
    if monitor:
        try:
            monitor_url = monitor_server.start(host=monitor_host, port=monitor_port)
            print(f"\n  >>> 打开浏览器查看实时监控：{monitor_url}", flush=True)
            print(f"      （状态 JSON：{monitor_url}/api/state）\n", flush=True)
        except Exception as e:
            print(f"[!] 监控服务器启动失败: {e}", flush=True)

    if config.wechat_ready:
        wechat_notifier.reset()
        wechat_notifier.start()

    # 2) 初始化状态
    state_manager.start(
        platform=platform,
        question_id=question_id,
        total=papers,
        rubric_version=rubric.version,
    )

    start = time.time()
    interrupted = False
    try:
        log_every = max(1, papers // 20)
        for i in range(1, papers + 1):
            name = DEFAULT_NAMES[(i - 1) % len(DEFAULT_NAMES)]
            score = _demo_score(rng, rubric.total_score)
            confidence = _confidence_for(rng, score, rubric.total_score)
            reasoning = _reasoning_for(rng, score, rubric.total_score)

            state_manager.set_current(student_name=name, score=score, reasoning=reasoning)
            state_manager.add_record(GradingRecord(
                index=i,
                student_name=name,
                score=score,
                max_score=rubric.total_score,
                reasoning=reasoning,
                confidence=confidence,
                rubric_version=rubric.version,
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            ))

            # 真实场景里偶发的非致命异常（展示异常时间线用）
            if i % 37 == 0:
                state_manager.add_error(f"第 {i} 份：答题卡图片下载超时，已自动重试成功")
            if i == max(1, papers * 3 // 5):
                state_manager.add_error("AI 返回缺少 details 字段，已按 reasoning 兜底解析")

            if (i % log_every == 0 or i == papers) and sys.stdout.isatty():
                pct = round(i / papers * 100)
                print(f"\r  {_progress_bar(i, papers)} {i}/{papers} 份  {pct:3d}%  "
                      f"当前：{name} {score}/{rubric.total_score}", end="", flush=True)

            if interval > 0:
                time.sleep(interval)
    except KeyboardInterrupt:
        interrupted = True
        print("\n[!] 演示被中断", flush=True)
        state_manager.set_status(STATUS_STOPPED)
    else:
        state_manager.finish(STATUS_DONE)

    elapsed = time.time() - start
    print(flush=True)
    snapshot = state_manager.snapshot()
    print("=" * 62, flush=True)
    print(f" 演示完成：{snapshot['graded']}/{snapshot['total']} 份    "
          f"平均分 {snapshot['avg_score']}    异常 {len(snapshot['errors'])} 条    "
          f"耗时 {elapsed:.1f}s", flush=True)
    if save_history:
        path = _save_demo_history()
        if path:
            print(f" 批改历史已保存：{path}", flush=True)
            print(f" 可导出报告：python main.py export --format html --output demo_report.html",
                  flush=True)
    print("=" * 62, flush=True)

    if exit_after or interrupted:
        if config.wechat_ready:
            wechat_notifier.stop()
        if monitor_url:
            monitor_server.stop()
        return 0

    # 3) 保持监控页面可查看（用户按 Ctrl+C 退出）
    try:
        print(f"\n监控页面保持运行：{monitor_url or '(未启动)'}", flush=True)
        print("按 Ctrl+C 退出演示。", flush=True)
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[!] 关闭演示…", flush=True)
    finally:
        if config.wechat_ready:
            wechat_notifier.stop()
        if monitor_url:
            monitor_server.stop()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """供独立脚本调用（demo_monitor.py 兼容入口）"""
    import argparse
    parser = argparse.ArgumentParser(description="WhisperMark 2.0 虚拟批改演示")
    parser.add_argument("--papers", type=int, default=200)
    parser.add_argument("--interval", type=float, default=0.15)
    parser.add_argument("--max-score", type=float, default=20.0)
    parser.add_argument("--platform", default="zhixue")
    parser.add_argument("--question-id", default="数学第15题")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-monitor", action="store_true")
    parser.add_argument("--exit", action="store_true")
    args = parser.parse_args(argv)
    return run_demo(
        papers=args.papers, interval=args.interval, max_score=args.max_score,
        platform=args.platform, question_id=args.question_id, seed=args.seed,
        monitor=not args.no_monitor, monitor_host=args.host, monitor_port=args.port,
        exit_after=args.exit,
    )


if __name__ == "__main__":
    sys.exit(main())
