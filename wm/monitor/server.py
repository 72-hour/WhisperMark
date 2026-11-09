"""
监控服务器（WhisperMark 2.0）
==================================================
本地 HTTP 服务器（默认 127.0.0.1:8321）：
  GET /            → 监控 HTML（web/monitor.html，实时展示阅卷状态）
  GET /dashboard   → 展示页（web/dashboard.html）
  GET /events      → SSE 实时推送（状态变化即推）
  GET /api/state   → 当前状态 JSON（轮询兜底）
  GET /api/health  → 健康检查

生命周期：start() 同步完成端口绑定（端口被占用会抛异常 / 自动顺延），
stop() 停止服务并注销状态订阅，可重复 start/stop。

临时 HTML 机制：
  启动时从 web/monitor.html 复制到 runtime/monitor/monitor.html（临时文件），
  下次启动自动覆盖，避免残留脏数据；web/monitor.html 缺失时回退到内置页面。
"""
import json
import queue
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Set

from ..config import config, PROJECT_ROOT
from ..utils.logging import logger
from ..engine.state import state_manager

WEB_DIR = PROJECT_ROOT / "web"
MONITOR_HTML_SRC = WEB_DIR / "monitor.html"
DASHBOARD_HTML_SRC = WEB_DIR / "dashboard.html"
MONITOR_HTML_TMP = config.MONITOR_DIR / "monitor.html"

# 内置兜底 HTML（当 web/monitor.html 缺失时使用）
_FALLBACK_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>WhisperMark 监控</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#0b1020;color:#e6edf3;padding:32px;margin:0}
 h1{font-size:20px} .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#3fb950;margin-right:8px}
 pre{background:#111827;border:1px solid #263042;border-radius:12px;padding:16px;overflow:auto;font-size:12.5px}
</style></head><body>
<h1><span class="dot"></span>WhisperMark 2.0 · 阅卷监控</h1>
<p>未找到 web/monitor.html，以下为实时状态 JSON：</p>
<pre id="s">加载中…</pre>
<script>
(function(){function r(s){document.getElementById('s').textContent=JSON.stringify(s,null,2);}
 fetch('/api/state').then(function(x){return x.json()}).then(r).catch(function(){});
 try{var es=new EventSource('/events');es.onmessage=function(e){r(JSON.parse(e.data));};}catch(e){}
 setInterval(function(){fetch('/api/state').then(function(x){return x.json()}).then(r).catch(function(){});},3000);
})();
</script></body></html>"""


class _ClientPool:
    """SSE 客户端连接池（线程安全）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._clients: Set[queue.Queue] = set()

    def add(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.add(q)

    def remove(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def broadcast(self, data: dict) -> None:
        with self._lock:
            targets = list(self._clients)
        for q in targets:
            try:
                q.put_nowait(data)
            except queue.Full:
                # 单个客户端处理不过来就丢弃这一帧，不阻塞阅卷主流程
                pass

    def close_all(self) -> None:
        """通知所有 SSE handler 退出（关闭服务时用，避免线程卡 15 秒）"""
        with self._lock:
            targets = list(self._clients)
            self._clients.clear()
        for q in targets:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass


_client_pool = _ClientPool()


def _on_state_change(snapshot: dict) -> None:
    """订阅 StateManager，广播状态快照"""
    _client_pool.broadcast(snapshot)


def _read_text(path: Path) -> Optional[str]:
    try:
        if path and path.exists():
            return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.debug("读取 HTML 失败 %s: %s", path, e)
    return None


class MonitorHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # 静默默认日志
        pass

    # ---------- 通用响应 ----------
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_html(self, source: Path, fallback: str = _FALLBACK_HTML):
        body = _read_text(source)
        if body is None and source == MONITOR_HTML_TMP:
            body = _read_text(MONITOR_HTML_SRC)
        if body is None:
            body = fallback
        data = body.encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("X-Content-Type-Options", "nosniff")
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_no_content(self, code: int = 204):
        self.send_response(code)
        self._cors()
        self.end_headers()

    # ---------- 路由 ----------
    def do_OPTIONS(self):
        self._send_no_content(204)

    def do_HEAD(self):
        # 只回响应头，避免监控工具探测时报 501
        path = self.path.split("?")[0]
        self.send_response(200 if path in ("/", "/index.html", "/monitor",
                                           "/monitor.html", "/api/state",
                                           "/api/health") else 404)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        try:
            if path == "/events":
                self._handle_sse()
            elif path == "/api/state":
                self._send_json(state_manager.snapshot())
            elif path == "/api/health":
                self._send_json({"ok": True, "service": "whispermark-monitor",
                                 "url": self._server_url()})
            elif path in ("/", "/index.html", "/monitor", "/monitor.html"):
                self._send_html(MONITOR_HTML_TMP)
            elif path in ("/dashboard", "/dashboard.html"):
                if _read_text(DASHBOARD_HTML_SRC) is None:
                    self._send_html(MONITOR_HTML_TMP)
                else:
                    self._send_html(DASHBOARD_HTML_SRC)
            elif path == "/favicon.ico":
                self._send_no_content(204)
            else:
                self.send_error(404, "Not Found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # 任何 handler 异常都不能拖垮服务器线程
            logger.warning("监控请求处理异常 %s: %s", path, e)
            try:
                self.send_error(500, "Internal Server Error")
            except Exception:
                pass

    def _server_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    # ---------- SSE ----------
    def _handle_sse(self):
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self._cors()
            self.end_headers()

            q: queue.Queue = queue.Queue(maxsize=100)
            _client_pool.add(q)
            try:
                # 先推一次当前状态
                self._write_sse(state_manager.snapshot())
                while True:
                    try:
                        data = q.get(timeout=15)
                    except queue.Empty:
                        # 心跳，避免代理/浏览器判定连接已死
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    if data is None:  # 服务端主动关闭
                        break
                    self._write_sse(data)
            finally:
                _client_pool.remove(q)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.close_connection = True

    def _write_sse(self, data: dict):
        payload = f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
        self.wfile.write(payload)
        self.wfile.flush()


class _MonitorHTTPServer(ThreadingHTTPServer):
    """允许端口复用、daemon 线程（退出进程时不会卡住）"""

    allow_reuse_address = True
    daemon_threads = True


class MonitorServer:
    """监控服务器（可 start/stop 的包装类）"""

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        self._httpd: Optional[_MonitorHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._host = host or config.MONITOR_HOST
        self._port = int(port) if port is not None else config.MONITOR_PORT
        self._actual_port: Optional[int] = None
        self._subscribed = False
        self._lock = threading.RLock()

    # ---------- 属性 ----------
    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._actual_port or self._port

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self._host in ("0.0.0.0", "", "::") else self._host
        return f"http://{host}:{self.port}"

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    # ---------- 生命周期 ----------
    def _prepare_tmp_html(self) -> None:
        """启动时清理临时目录并复制监控 HTML（失败不影响服务）"""
        try:
            config.MONITOR_DIR.mkdir(parents=True, exist_ok=True)
            if MONITOR_HTML_SRC.exists():
                if MONITOR_HTML_TMP.exists():
                    try:
                        MONITOR_HTML_TMP.unlink()
                    except OSError:
                        pass
                shutil.copyfile(MONITOR_HTML_SRC, MONITOR_HTML_TMP)
                logger.info("监控 HTML 已部署到 %s", MONITOR_HTML_TMP)
        except Exception as e:
            logger.warning("部署监控 HTML 失败（将直接使用 web/monitor.html）: %s", e)

    def _bind(self, host: str, port: int) -> _MonitorHTTPServer:
        return _MonitorHTTPServer((host, port), MonitorHandler)

    def start(self, host: Optional[str] = None,
              port: Optional[int] = None,
              allow_port_fallback: Optional[bool] = None,
              max_fallback: int = 10) -> str:
        """
        同步启动监控服务器，返回实际访问 URL。
        端口被占用时：allow_port_fallback=True 则自动顺延，否则抛 RuntimeError。
        """
        with self._lock:
            if self._httpd is not None:
                return self.url
            use_host = host or self._host
            use_port = int(port if port is not None else self._port)
            fallback = (config.MONITOR_PORT_FALLBACK
                        if allow_port_fallback is None else bool(allow_port_fallback))

            self._prepare_tmp_html()

            candidates = [use_port] + [use_port + i for i in range(1, max_fallback)]
            if not fallback:
                candidates = [use_port]

            last_error: Optional[Exception] = None
            httpd = None
            for candidate in candidates:
                if candidate <= 0 or candidate > 65535:
                    continue
                try:
                    httpd = self._bind(use_host, candidate)
                    self._actual_port = httpd.server_address[1]
                    break
                except OSError as e:
                    last_error = e
                    logger.debug("监控端口 %s 不可用: %s", candidate, e)

            if httpd is None:
                raise RuntimeError(
                    f"监控服务器启动失败：{use_host}:{use_port} 不可用（{last_error}）")

            self._httpd = httpd
            self._host = use_host
            state_manager.subscribe(_on_state_change)
            self._subscribed = True
            self._thread = threading.Thread(
                target=httpd.serve_forever,
                kwargs={"poll_interval": 0.2},
                name="monitor-server",
                daemon=True,
            )
            self._thread.start()
            logger.info("监控服务器已启动: %s", self.url)
            return self.url

    def stop(self) -> None:
        """停止服务并注销订阅（可重复调用）"""
        with self._lock:
            httpd = self._httpd
            thread = self._thread
            self._httpd = None
            self._thread = None
            self._actual_port = None
            if self._subscribed:
                state_manager.unsubscribe(_on_state_change)
                self._subscribed = False

        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        _client_pool.close_all()
        if thread is not None:
            try:
                thread.join(timeout=3)
            except Exception:
                pass
        if httpd is not None:
            logger.info("监控服务器已停止")


# 全局实例
monitor_server = MonitorServer()
