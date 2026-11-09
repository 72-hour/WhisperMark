"""
阅卷引擎（WhisperMark 2.0）
==================================================
平台无关的批改核心：把「平台适配器」+「AI 评分」+「细则锚定」+「状态上报」串联。

流程（单份）：
  获取答题卡图片 → 读满分 → AI 评分（锚定 rubric）→ 填分 → 提交 → 记录 → 等待下一份

线程模型（重要）：
  Playwright 的同步 API 不是线程安全的，只能在使用它的线程里调用。
  因此 CLI 用 `run_forever()` 在主线程跑批改循环；`start_auto()` 仅保留给
  程序化 / 测试场景（fake adapter 或纯 AI 调用）。

状态通过 StateManager 广播，监控服务器 / 微信推送通过观察者订阅自动接收。
"""
import threading
import time
from typing import Optional

from ..config import config
from ..browser import BrowserManager
from ..platforms.base import PlatformAdapter
from ..utils.logging import logger
from .ai_grader import ai_grader, AIGradingResult
from .rubric import Rubric
from .state import (
    state_manager, GradingRecord,
    STATUS_RUNNING, STATUS_PAUSED, STATUS_DONE, STATUS_STOPPED, STATUS_ERROR,
)


class Grader:
    """阅卷引擎"""

    def __init__(self, browser: Optional[BrowserManager],
                 adapter: PlatformAdapter,
                 rubric: Optional[Rubric] = None,
                 max_score: Optional[float] = None):
        self.browser = browser
        self.adapter = adapter
        self.rubric = rubric
        self.max_score: float = float(max_score) if max_score else config.MAX_SCORE
        self.max_score_override: float = 0.0   # --max-score 显式指定时优先
        self.print_progress: bool = True
        self._running = False
        self._paused = False
        self._stop_flag = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    # ---------- 满分 ----------
    def read_max_score(self) -> float:
        """从页面输入框读取满分（兜底用 rubric/config）"""
        if self.max_score_override > 0:
            self.max_score = self.max_score_override
            return self.max_score
        try:
            inputs = self.adapter.get_score_inputs()
            for si in inputs:
                if si.max_score > 0:
                    self.max_score = si.max_score
                    return si.max_score
        except Exception as e:
            logger.warning("读取页面满分失败: %s", e)
        if self.rubric and self.rubric.total_score > 0:
            self.max_score = self.rubric.total_score
        return self.max_score

    # ---------- 单份批改 ----------
    def grade_one(self) -> Optional[AIGradingResult]:
        """批改当前一份答卷，返回评分结果；失败返回 None（不抛异常给调用方）"""
        # 1. 获取答题卡图片
        urls = self.adapter.gather_answer_images()
        if not urls:
            state_manager.set_current(reasoning="未找到答题卡图片，等待下一份…")
            return None
        img_data = self.adapter.download_image(urls[0])
        if not img_data:
            state_manager.add_error("答题卡图片下载失败")
            return None

        # 2. 读满分
        max_s = self.read_max_score()

        # 3. 学生姓名
        student = ""
        try:
            student = self.adapter.get_current_student_name() or ""
        except Exception:
            student = ""
        state_manager.set_current(student_name=student)

        # 4. AI 评分（锚定 rubric）
        result = ai_grader.grade(img_data, self.rubric, max_s)
        if result.is_error:
            state_manager.add_error(f"AI 评分失败: {result.reasoning[:120]}")
            return None

        # 5. 填分 + 提交
        if not self.adapter.fill_scores([result.score]):
            state_manager.add_error(f"填分失败（得分 {result.score}/{max_s}）")
            return None
        if not self.adapter.submit_grade():
            state_manager.add_error("提交失败")
            return None

        # 6. 记录（index 由 StateManager 自动编号，避免无锁读取 graded）
        record = GradingRecord(
            student_name=student,
            score=result.score,
            max_score=max_s,
            reasoning=result.reasoning,
            confidence=result.confidence,
            rubric_version=self.rubric.version if self.rubric else "",
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        state_manager.add_record(record)
        logger.info("已批改 #%d: %s = %s/%s", record.index, student or "未知",
                    result.score, max_s)
        if self.print_progress:
            s = state_manager.snapshot()
            total_str = f"/{s['total']}" if s["total"] > 0 else ""
            print(f"\r[批改中] 已批 {s['graded']}{total_str} 份  当前: {student or '-'}  "
                  f"得分: {result.score}/{max_s}", end="", flush=True)

        # 7. 等待下一份
        try:
            self.adapter.wait_for_next_paper(urls[0])
        except Exception as e:
            logger.warning("等待下一份答卷失败: %s", e)
        return result

    # ---------- 批改循环 ----------
    def _loop_body(self) -> None:
        """批改循环主体（调用前必须已设置 _running / 清空 _stop_flag）"""
        state_manager.set_status(STATUS_RUNNING)
        consecutive_empty = 0
        try:
            while True:
                with self._lock:
                    if self._stop_flag:
                        break
                    paused = self._paused
                if paused:
                    self._sleep(0.2)
                    continue

                result = None
                try:
                    result = self.grade_one()
                except Exception as e:
                    logger.exception("批改异常")
                    state_manager.add_error(f"批改异常: {type(e).__name__}: {e}")
                    result = None

                if result is None:
                    consecutive_empty += 1
                    if consecutive_empty >= config.MAX_EMPTY_RETRIES:
                        logger.info("连续 %d 次未获取到答题卡，判定批改完成", consecutive_empty)
                        break
                    self._sleep(config.RETRY_DELAY)
                else:
                    consecutive_empty = 0
                    self._sleep(min(config.RETRY_DELAY, 0.5))
        finally:
            with self._lock:
                self._running = False
                stopped = self._stop_flag
            if stopped:
                state_manager.set_status(STATUS_STOPPED)
            elif state_manager.snapshot()["status"] != STATUS_ERROR:
                state_manager.finish(STATUS_DONE)
            if self.print_progress:
                print()  # 结束进度行

    def run_forever(self) -> None:
        """在当前线程（CLI 主线程）跑完批改循环，阻塞直到完成/停止"""
        with self._lock:
            self._running = True
            self._stop_flag = False
            self._paused = False
        self._loop_body()

    def auto_loop(self) -> None:
        """后台线程入口（仅供 start_auto / 程序化调用；Playwright 场景请用 run_forever）"""
        self._loop_body()

    def start_auto(self) -> threading.Thread:
        """启动自动批改（后台线程）。返回线程对象。

        注意：若 adapter 依赖 Playwright，请改用 run_forever()，因为 Playwright
        同步 API 不能跨线程使用。
        """
        with self._lock:
            if self._running and self._thread and self._thread.is_alive():
                return self._thread
            # 先置运行标记再起线程，避免调用方在竞态窗口里误判"已结束"
            self._running = True
            self._stop_flag = False
            self._paused = False
        state_manager.set_status(STATUS_RUNNING)
        self._thread = threading.Thread(target=self.auto_loop, name="grader-auto", daemon=True)
        self._thread.start()
        return self._thread

    def wait(self, timeout: Optional[float] = None) -> bool:
        """等待批改线程结束；返回线程是否已结束"""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def join(self, timeout: Optional[float] = None) -> bool:
        return self.wait(timeout)

    # ---------- 控制 ----------
    def pause(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._paused = True
        state_manager.set_status(STATUS_PAUSED)
        logger.info("已暂停")

    def resume(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._paused = False
        state_manager.set_status(STATUS_RUNNING)
        logger.info("继续批改")

    def stop(self) -> None:
        with self._lock:
            self._stop_flag = True
            self._paused = False
        logger.info("正在停止批改…")

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running and not self._stop_flag

    # ---------- 工具 ----------
    def _sleep(self, seconds: float) -> None:
        """可被 stop() 打断的 sleep"""
        end = time.time() + max(seconds, 0)
        while time.time() < end:
            with self._lock:
                if self._stop_flag:
                    return
            time.sleep(min(0.2, max(end - time.time(), 0.01)))
