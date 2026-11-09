"""
微信推送模块（WhisperMark 2.0）
==================================================
通过 webhook 推送阅卷状态到微信（workbuddy 小龙虾 / Server酱 / 企业微信机器人 等）。

触发点：
  - 开始 / 暂停 / 恢复 / 完成 / 停止
  - 进度（每 N 份）
  - 异常（新增即推送，最高优先级）

推送是异步的：webhook 网络请求在后台线程串行发送，绝不阻塞阅卷主循环。
推送格式（默认 JSON POST）：{"text": "..."}
可通过环境变量 WM_WECHAT_PAYLOAD_KEY 自定义 JSON 字段名（如 Server酱 用 "content"）。
"""
import json
import queue
import threading
import urllib.request
from typing import Optional

from ..config import config
from ..utils.logging import logger
from ..engine.state import state_manager


class WechatNotifier:
    """微信推送器（异步队列 + 事件去重）"""

    def __init__(self):
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=200)
        self._worker: Optional[threading.Thread] = None
        self._subscribed = False
        self._lock = threading.RLock()
        self._last_graded = 0
        self._last_status = ""
        self._last_error_count = 0

    # ---------- 发送 ----------
    def send(self, text: str) -> bool:
        """同步发送一条文本消息（主要给测试 / 手动调用）"""
        if not config.wechat_ready:
            return False
        payload_key = config.WECHAT_PAYLOAD_KEY or "text"
        body = json.dumps({payload_key: text}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            config.WECHAT_WEBHOOK_URL,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300
        except Exception as e:
            logger.warning("微信推送失败: %s", e)
            return False

    def _enqueue(self, text: str) -> None:
        if not config.wechat_ready:
            return
        if self._worker is not None and self._worker.is_alive():
            try:
                self._queue.put_nowait(text)
                return
            except queue.Full:
                logger.debug("微信推送队列已满，丢弃一条消息")
                return
        # 未启动 worker（例如脚本里直接调用）时同步发送
        self.send(text)

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:  # 停止信号
                    break
                self.send(item)
            finally:
                self._queue.task_done()

    # ---------- 状态订阅 ----------
    def _on_state(self, snapshot: dict) -> None:
        """订阅状态变化，按事件去重推送"""
        status = snapshot.get("status", "")
        graded = int(snapshot.get("graded", 0) or 0)
        total = int(snapshot.get("total", 0) or 0)
        errors = snapshot.get("errors", []) or []

        # 1) 状态变化
        if status != self._last_status:
            prev, self._last_status = self._last_status, status
            msg = {
                "running": "▶️ 开始阅卷" if prev in ("", "idle") else "▶️ 继续阅卷",
                "paused": "⏸️ 已暂停",
                "done": "✅ 阅卷完成",
                "stopped": "⏹️ 已停止",
                "error": "⚠️ 阅卷出错",
            }.get(status)
            if msg:
                detail = f"（已批 {graded} 份）" if total == 0 else f"（{graded}/{total} 份）"
                self._enqueue(f"{msg}{detail}")

        # 2) 进度：按「增量」判断，跳号也不会漏推
        interval = max(config.WECHAT_PROGRESS_INTERVAL, 1)
        if graded > 0 and graded - self._last_graded >= interval:
            self._last_graded = graded
            total_str = f"/{total}" if total > 0 else ""
            self._enqueue(f"📊 批改进度：{graded}{total_str} 份")

        # 3) 异常：新增的逐条立即推送
        if len(errors) > self._last_error_count:
            new_errors = errors[self._last_error_count:]
            self._last_error_count = len(errors)
            for e in new_errors[:5]:
                self._enqueue(f"🚨 异常：{e.get('message', '')}")

    def start(self) -> None:
        """订阅状态管理器并启动异步发送线程（幂等）"""
        if not config.wechat_ready:
            return
        with self._lock:
            if not self._subscribed:
                state_manager.subscribe(self._on_state)
                self._subscribed = True
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._worker_loop, name="wechat-notifier", daemon=True)
                self._worker.start()
            logger.info("微信推送已启用（webhook）")

    def stop(self) -> None:
        """停止推送（幂等）"""
        with self._lock:
            if self._subscribed:
                state_manager.unsubscribe(self._on_state)
                self._subscribed = False
            worker, self._worker = self._worker, None
        if worker is not None:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            try:
                worker.join(timeout=3)
            except Exception:
                pass

    def reset(self) -> None:
        """清空去重计数（新一轮阅卷开始前调用）"""
        self._last_graded = 0
        self._last_status = ""
        self._last_error_count = 0


# 全局实例
wechat_notifier = WechatNotifier()
