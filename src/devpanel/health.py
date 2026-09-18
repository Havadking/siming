"""健康检查：配了 `health:` 的项目，「在线」看 HTTP 响应而不是端口有没有人听。

一条后台线程 2s 一轮，只查进程活着（或端口有人听）的项目；上次通过的 5s 查一次，没过的 2s 查一次。
urllib 显式关代理——Windows 系统代理开着时 127.0.0.1 也会被送去代理。见 DESIGN.md 10.3。
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

TIMEOUT = 3.0          # Windows 上 loopback 的「连接被拒绝」要 SYN 重试 ~2s 才报出来，短于这个会先超时
LOOP_INTERVAL = 2.0
OK_EVERY = 5.0          # 上次通过：隔多久再查
FAIL_EVERY = 2.0        # 上次没过：隔多久再查
WORKERS = 4

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class HealthResult:
    ok: bool
    detail: str                 # "200" / "HTTP 503" / "连接被拒绝" / "超时 2s"
    checked_at: float
    latency_ms: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def check_url(url: str, timeout: float = TIMEOUT) -> HealthResult:
    t0 = time.time()
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "devpanel-health"})
    try:
        with _opener.open(req, timeout=timeout) as resp:
            code = resp.status
            resp.read(1)   # 有响应头就够了，读一个字节让连接正常收尾
    except urllib.error.HTTPError as e:
        code = e.code
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            return HealthResult(False, f"超时 {timeout:g}s", time.time())
        if isinstance(reason, ConnectionRefusedError) or "10061" in str(reason) or "refused" in str(reason).lower():
            return HealthResult(False, "连接被拒绝", time.time())
        return HealthResult(False, f"连不上：{reason}", time.time())
    except (TimeoutError, ConnectionError) as e:
        return HealthResult(False, "超时" if isinstance(e, TimeoutError) else f"连不上：{e}", time.time())
    except Exception as e:   # noqa: BLE001  http.client 的各种 BadStatusLine / RemoteDisconnected
        return HealthResult(False, f"响应异常：{type(e).__name__}", time.time())
    ms = int((time.time() - t0) * 1000)
    # 3xx 也算通过：urllib 默认跟随重定向，走到这里的 3xx 只有它不跟的那几种（如 304）
    ok = 200 <= code < 400
    return HealthResult(ok, str(code) if ok else f"HTTP {code}", time.time(), ms)


class HealthChecker:
    """{project_id: HealthResult}。start() 起后台线程，get_targets() 每轮给出要查的 (id, url)。"""

    def __init__(self):
        self.results: dict[str, HealthResult] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pool: ThreadPoolExecutor | None = None

    def result(self, project_id: str) -> HealthResult | None:
        with self._lock:
            return self.results.get(project_id)

    def forget(self, project_id: str) -> None:
        with self._lock:
            self.results.pop(project_id, None)

    def check_now(self, project_id: str, url: str) -> HealthResult:
        r = check_url(url)
        with self._lock:
            self.results[project_id] = r
        return r

    def due(self, project_id: str, now: float) -> bool:
        r = self.result(project_id)
        if r is None:
            return True
        return now - r.checked_at >= (OK_EVERY if r.ok else FAIL_EVERY)

    def run_once(self, targets: list[tuple[str, str]]) -> None:
        now = time.time()
        todo = [(pid, url) for pid, url in targets if self.due(pid, now)]
        # 不再是目标的（停掉了）结果丢掉，下次启动从头查
        with self._lock:
            keep = {pid for pid, _ in targets}
            for pid in list(self.results):
                if pid not in keep:
                    del self.results[pid]
        if not todo:
            return
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="health")
        list(self._pool.map(lambda t: self.check_now(*t), todo))

    def start(self, get_targets: Callable[[], list[tuple[str, str]]]) -> None:
        threading.Thread(target=self._loop, args=(get_targets,), name="health", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self, get_targets: Callable[[], list[tuple[str, str]]]) -> None:
        while not self._stop.wait(LOOP_INTERVAL):
            try:
                self.run_once(get_targets())
            except Exception:   # noqa: BLE001  后台线程不能死
                pass
