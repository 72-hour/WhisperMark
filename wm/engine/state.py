"""
阅卷状态机与状态上报模块
==================================================
维护全局阅卷状态，通过「观察者回调」向上报：
  - 监控服务器（HTTP + SSE）
  - 微信推送（workbuddy 小龙虾）
状态变化实时广播，供临时 HTML 监控页展示。
"""
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, List, Optional, Any

from ..utils.logging import logger

# 状态常量
STATUS_IDLE = "idle"           # 未开始
STATUS_RUNNING = "running"     # 批改中
STATUS_PAUSED = "paused"       # 已暂停
STATUS_DONE = "done"           # 完成
STATUS_ERROR = "error"         # 出错（可恢复）
STATUS_STOPPED = "stopped"     # 已停止

ALL_STATUS = (STATUS_IDLE, STATUS_RUNNING, STATUS_PAUSED,
              STATUS_DONE, STATUS_ERROR, STATUS_STOPPED)


@dataclass
class GradingRecord:
    """单份答卷的批改记录"""
    index: int = 0
    student_name: str = ""
    score: float = 0.0
    max_score: float = 0.0
    reasoning: str = ""
    confidence: str = ""
    rubric_version: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GradingState:
    """全局阅卷状态"""
    status: str = STATUS_IDLE
    platform: str = ""
    question_id: str = ""
    rubric_version: str = ""
    total: int = 0
    graded: int = 0
    current_student: str = ""
    current_score: Optional[float] = None
    current_reasoning: str = ""
    errors: List[dict] = field(default_factory=list)
    records: List[GradingRecord] = field(default_factory=list)
    started_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["percentage"] = self.percentage
        d["remaining"] = self.remaining
        d["avg_score"] = self.avg_score
        return d

    @property
    def percentage(self) -> float:
        return round(self.graded / self.total * 100, 1) if self.total > 0 else 0.0

    @property
    def remaining(self) -> int:
        return max(self.total - self.graded, 0)

    @property
    def avg_score(self) -> float:
        """已批答卷平均分（忽略满分未知的记录）"""
        valid = [r.score for r in self.records if r.max_score > 0]
        return round(sum(valid) / len(valid), 2) if valid else 0.0


class StateManager:
    """状态管理器（线程安全 + 观察者模式）"""

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        with self._instance_lock:
            if getattr(self, "_initialized", False):
                return
            self._initialized = True
            self.state = GradingState()
            self._listeners: List[Callable[[dict], Any]] = []
            self._lock = threading.RLock()
            self._notify_lock = threading.Lock()

    # ---------- 观察者 ----------
    def subscribe(self, listener: Callable[[dict], Any]) -> None:
        with self._notify_lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[dict], Any]) -> None:
        with self._notify_lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    def _notify(self) -> None:
        snapshot = self.snapshot()
        with self._notify_lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(snapshot)
            except Exception:
                # 单个观察者出错不能影响阅卷主流程
                logger.debug("状态观察者回调异常", exc_info=True)

    def snapshot(self) -> dict:
        with self._lock:
            return self.state.to_dict()

    # ---------- 状态更新 ----------
    def reset(self) -> None:
        with self._lock:
            self.state = GradingState()
        self._notify()

    def start(self, platform: str, question_id: str, total: int,
              rubric_version: str = "") -> None:
        """开始新一轮阅卷：必须清空上一轮的记录/异常/当前答卷"""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self.state = GradingState(
                status=STATUS_RUNNING,
                platform=platform,
                question_id=question_id,
                rubric_version=rubric_version,
                total=max(int(total or 0), 0),
                started_at=now,
                updated_at=now,
            )
        self._notify()

    def set_total(self, total: int) -> None:
        with self._lock:
            self.state.total = max(int(total or 0), 0)
            self.state.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._notify()

    def set_status(self, status: str) -> None:
        if status not in ALL_STATUS:
            logger.warning("未知状态: %s", status)
            return
        with self._lock:
            self.state.status = status
            self.state.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._notify()

    def set_current(self, student_name: str = "", score: Optional[float] = None,
                    reasoning: str = "") -> None:
        with self._lock:
            if student_name:
                self.state.current_student = student_name
            if score is not None:
                self.state.current_score = score
            if reasoning:
                self.state.current_reasoning = reasoning
            self.state.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._notify()

    def add_record(self, record: GradingRecord) -> GradingRecord:
        """追加一条批改记录；index 未指定时自动编号"""
        with self._lock:
            if not record.index or record.index <= 0:
                record.index = len(self.state.records) + 1
            self.state.records.append(record)
            self.state.graded = len(self.state.records)
            self.state.current_score = record.score
            self.state.current_reasoning = record.reasoning
            self.state.current_student = record.student_name
            self.state.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._notify()
        return record

    def add_error(self, message: str, fatal: bool = False) -> None:
        with self._lock:
            self.state.errors.append({
                "time": time.strftime("%H:%M:%S"),
                "message": str(message),
                "fatal": bool(fatal),
            })
            if fatal:
                self.state.status = STATUS_ERROR
            self.state.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._notify()
        logger.warning("阅卷异常: %s", message)

    def finish(self, status: str = STATUS_DONE) -> None:
        with self._lock:
            self.state.status = status
            if self.state.total <= 0:
                # 未知总份数时，用已批份数兜底，保证百分比有意义
                self.state.total = self.state.graded
            self.state.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._notify()


# 全局状态管理器实例
state_manager = StateManager()
