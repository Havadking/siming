"""进程管理：spawn / 杀树 / 状态 / 重启退避 / 认领外部实例。

每个项目一个 Runtime，只记事实（pid、create_time、退出码、失败时间点）；
状态不缓存，每次 snapshot() 现算，见 DESIGN.md 4.2。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from .config import Project, clean_path, read_env_file
from .logs import LogManager, decode

STARTING_GRACE = 60          # 进程活着但端口不通，超过这个秒数标 unhealthy
FAIL_WINDOW = 600            # 10 分钟内
MAX_RESTARTS = 5             # 最多自动重启 5 次，第 6 次失败 → crashed
TERMINATE_WAIT = 3.0
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
DEFAULT_ENV = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "FORCE_COLOR": "0", "NO_COLOR": "1"}
# 面板自己是 `uv run` 起的，这些变量指向面板的 .venv，传给子项目会让它的 uv / python 认错环境
STRIP_ENV = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONHOME", "PYTHONPATH")


def backoff(n: int) -> float:
    """第 n 次自动重启前等多久：1, 2, 4, 8, 16, 30, 30…"""
    return float(min(2 ** (n - 1), 30))


class ActionError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


@dataclass
class Runtime:
    pid: int | None = None
    create_time: float | None = None
    started_at: float | None = None
    proc: subprocess.Popen | None = None      # 面板自己 spawn 的才有；认领回来的没有
    owned: bool = False                       # 是否面板起的（含重启后认领回来的）
    exit_code: int | None = None
    exited_at: float | None = None
    manual_stop: bool = False                 # 这次退出是用户点的停止
    failures: list[float] = field(default_factory=list)  # 非零退出的时间点
    restart_count: int = 0
    restart_timer: threading.Timer | None = None
    restart_due: float | None = None          # 正在退避，什么时候重启
    crashed: bool = False
    url: str | None = None                    # 从日志里按 url_pattern 捞到的地址，比配置里的 url 优先
    lock: threading.Lock = field(default_factory=threading.Lock)

    def alive(self) -> bool:
        return self.pid is not None and _pid_matches(self.pid, self.create_time)


def _pid_matches(pid: int, create_time: float | None) -> bool:
    try:
        p = psutil.Process(pid)
        if p.status() == psutil.STATUS_ZOMBIE:
            return False
        if create_time is not None and abs(p.create_time() - create_time) > 1.0:
            return False
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def listening_ports() -> dict[int, int]:
    """{端口: pid}，只看 TCP LISTEN。psutil 拿不到 pid 的填 0。"""
    out: dict[int, int] = {}
    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, OSError):
        return out
    for c in conns:
        if c.status == psutil.CONN_LISTEN and c.laddr:
            out.setdefault(c.laddr.port, c.pid or 0)
    return out


def kill_tree(pid: int) -> None:
    """子进程先、父进程后；terminate 等 3 秒，没退的 kill。"""
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        children = root.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    procs = [*reversed(children), root]
    for p in procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(procs, timeout=TERMINATE_WAIT)
    for p in alive:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(alive, timeout=TERMINATE_WAIT)


class Supervisor:
    def __init__(self, state_dir: Path, logs: LogManager):
        self.state_dir = state_dir
        self.logs = logs
        self.runtimes: dict[str, Runtime] = {}
        self._procs: dict[int, psutil.Process] = {}   # cpu_percent 要两次采样，Process 对象得留着
        self._lock = threading.Lock()
        self._ncpu = psutil.cpu_count() or 1
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ----- pids.json -----

    @property
    def _pids_path(self) -> Path:
        return self.state_dir / "pids.json"

    def _save_pids(self) -> None:
        data = {
            pid_id: {"pid": rt.pid, "create_time": rt.create_time, "started_at": rt.started_at, "url": rt.url}
            for pid_id, rt in self.runtimes.items()
            if rt.owned and rt.pid is not None and rt.exit_code is None and rt.alive()
        }
        tmp = self._pids_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._pids_path)

    def adopt_saved(self, projects: list[Project]) -> list[str]:
        """面板重启后，把 pids.json 里还活着的进程认领回来。返回认领成功的 id。"""
        try:
            data = json.loads(self._pids_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        ids = {p.id for p in projects}
        adopted: list[str] = []
        for pid_id, rec in data.items():
            if pid_id not in ids:
                continue
            pid, ct = rec.get("pid"), rec.get("create_time")
            if pid and _pid_matches(pid, ct):
                rt = self._rt(pid_id)
                rt.pid, rt.create_time, rt.started_at = pid, ct, rec.get("started_at") or time.time()
                rt.owned, rt.exit_code, rt.proc = True, None, None
                rt.url = rec.get("url")
                threading.Thread(target=self._watch_adopted, args=(pid_id, rt), daemon=True).start()
                adopted.append(pid_id)
                self.logs.get(pid_id).mark("panel restarted, adopted")
        self._save_pids()
        return adopted

    # ----- 启停 -----

    def _rt(self, project_id: str) -> Runtime:
        with self._lock:
            rt = self.runtimes.get(project_id)
            if rt is None:
                rt = Runtime()
                self.runtimes[project_id] = rt
            return rt

    def start(self, project: Project, *, auto: bool = False) -> None:
        err = project.runtime_error()
        if err:
            raise ActionError(400, f"配置错误：{err}")
        rt = self._rt(project.id)
        with rt.lock:
            if rt.alive():
                raise ActionError(409, "已经在运行")
            ports = listening_ports()
            if project.port and project.port in ports:
                raise ActionError(409, f"端口 {project.port} 已被 pid {ports[project.port]} 占用（外部实例？先停掉它）")
            self._cancel_restart(rt)
            if not auto:
                rt.failures.clear()
                rt.restart_count = 0
                rt.crashed = False
            env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
            env["PATH"] = clean_path()
            env.update(DEFAULT_ENV)
            log = self.logs.get(project.id)
            if project.env_file:
                try:
                    env.update(read_env_file(project.env_file))
                except OSError as e:
                    log.append(f"[devpanel] 读 env_file 失败：{e}")
                    raise ActionError(500, f"读 env_file 失败：{e}") from e
            env.update(project.env)
            log.mark("start" if not auto else f"auto restart #{rt.restart_count}")
            log.append(f"$ {project.cmd}")
            try:
                proc = subprocess.Popen(
                    project.argv, cwd=str(project.cwd), env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                )
            except OSError as e:
                log.append(f"[devpanel] 启动失败：{e}")
                raise ActionError(500, f"启动失败：{e}") from e
            rt.proc = proc
            rt.pid = proc.pid
            try:
                rt.create_time = psutil.Process(proc.pid).create_time()
            except psutil.Error:
                rt.create_time = None
            rt.started_at = time.time()
            rt.owned = True
            rt.exit_code = None
            rt.exited_at = None
            rt.manual_stop = False
            rt.url = None
            self._save_pids()
        threading.Thread(target=self._pump, args=(project, rt, proc), daemon=True).start()
        threading.Thread(target=self._wait_owned, args=(project, rt, proc), daemon=True).start()

    def stop(self, project: Project) -> None:
        rt = self._rt(project.id)
        with rt.lock:
            self._cancel_restart(rt)
            rt.crashed = False
            if rt.alive():
                rt.manual_stop = True
                self.logs.get(project.id).mark("stop")
                kill_tree(rt.pid)  # type: ignore[arg-type]
                # 树已死；有 Popen 的话把退出码当场记下，不等 _wait_owned 那条线程抢到锁
                if rt.proc is not None:
                    try:
                        rt.exit_code = rt.proc.wait(timeout=TERMINATE_WAIT)
                        rt.exited_at = time.time()
                    except subprocess.TimeoutExpired:
                        pass
                return
            # 不是我们起的，但端口有人听：杀那棵树
            ports = listening_ports()
            pid = ports.get(project.port or -1)
            if pid:
                self.logs.get(project.id).mark(f"stop external pid {pid}")
                kill_tree(pid)
                return
            raise ActionError(409, "没有在运行")

    def restart(self, project: Project) -> None:
        try:
            self.stop(project)
        except ActionError as e:
            if e.status != 409:
                raise
        # 端口释放需要一点时间；等到 LISTEN 消失再起，最多 5 秒
        if project.port:
            for _ in range(50):
                if project.port not in listening_ports():
                    break
                time.sleep(0.1)
        self.start(project)

    def reset_crash(self, project_id: str) -> None:
        rt = self._rt(project_id)
        rt.failures.clear()
        rt.restart_count = 0
        rt.crashed = False

    # ----- 后台线程 -----

    def _pump(self, project: Project, rt: Runtime, proc: subprocess.Popen) -> None:
        log = self.logs.get(project.id)
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = decode(raw)
            log.append(line)
            if project.url_pattern and rt.proc is proc:
                m = project.url_pattern.search(line)
                if m:
                    rt.url = m.group(1)
                    self._save_pids()
        proc.stdout.close()

    def _wait_owned(self, project: Project, rt: Runtime, proc: subprocess.Popen) -> None:
        code = proc.wait()
        with rt.lock:
            if rt.proc is not proc:
                return  # 已经被新的 start 覆盖
            rt.exit_code = code
            rt.exited_at = time.time()
            rt.proc = None
            self._procs.pop(rt.pid or -1, None)
            self._save_pids()
            log = self.logs.get(project.id)
            log.mark(f"exit code {code}")
            if rt.manual_stop or code == 0 or project.restart != "on-failure":
                return
            now = time.time()
            rt.failures = [t for t in rt.failures if now - t < FAIL_WINDOW] + [now]
            if len(rt.failures) > MAX_RESTARTS:
                rt.crashed = True
                log.append(f"[devpanel] {FAIL_WINDOW // 60} 分钟内失败 {len(rt.failures)} 次，停止自动重启")
                return
            rt.restart_count += 1
            delay = backoff(rt.restart_count)
            rt.restart_due = now + delay
            log.append(f"[devpanel] {delay}s 后自动重启（第 {rt.restart_count} 次）")
            rt.restart_timer = threading.Timer(delay, self._do_restart, args=(project, rt))
            rt.restart_timer.daemon = True
            rt.restart_timer.start()

    def _do_restart(self, project: Project, rt: Runtime) -> None:
        rt.restart_due = None
        rt.restart_timer = None
        try:
            self.start(project, auto=True)
        except ActionError as e:
            self.logs.get(project.id).append(f"[devpanel] 自动重启失败：{e}")

    def _cancel_restart(self, rt: Runtime) -> None:
        if rt.restart_timer:
            rt.restart_timer.cancel()
        rt.restart_timer = None
        rt.restart_due = None

    def _watch_adopted(self, project_id: str, rt: Runtime) -> None:
        """认领来的进程没有 Popen 可 wait，只能轮询它还在不在。退出码拿不到。"""
        pid, ct = rt.pid, rt.create_time
        while _pid_matches(pid, ct):  # type: ignore[arg-type]
            time.sleep(1.0)
        with rt.lock:
            if rt.pid != pid:
                return
            rt.exit_code = 0 if rt.manual_stop else -1
            rt.exited_at = time.time()
            self._procs.pop(pid or -1, None)
            self._save_pids()
            self.logs.get(project_id).mark("process gone")

    # ----- 状态 -----

    def _tree_metrics(self, pid: int) -> tuple[int, float]:
        """整棵树的 RSS 之和、CPU%（按机器总核归一）。"""
        rss, cpu = 0, 0.0
        try:
            root = self._procs.get(pid)
            if root is None:
                root = psutil.Process(pid)
                self._procs[pid] = root
            procs = [root, *root.children(recursive=True)]
        except psutil.Error:
            return 0, 0.0
        for p in procs:
            try:
                cached = self._procs.get(p.pid)
                if cached is None or cached.create_time() != p.create_time():
                    self._procs[p.pid] = p
                    cached = p
                rss += cached.memory_info().rss
                cpu += cached.cpu_percent(interval=None)
            except psutil.Error:
                continue
        return rss, round(cpu / self._ncpu, 1)

    def snapshot(self, projects: list[Project]) -> list[dict]:
        ports = listening_ports()
        now = time.time()
        out: list[dict] = []
        for p in projects:
            rt = self.runtimes.get(p.id)
            port_pid = ports.get(p.port) if p.port else None
            port_open = port_pid is not None
            err = p.runtime_error()
            d: dict = {
                **p.to_dict(),
                "error": err,
                "url": (rt.url if rt and rt.url and rt.alive() else None) or p.url,
                "status": "stopped", "pid": None, "uptime": None, "rss": None, "cpu": None,
                "restart_count": rt.restart_count if rt else 0,
                "exit_code": rt.exit_code if rt else None,
                "exited_at": rt.exited_at if rt else None,
                "restart_due": None, "failures": len(rt.failures) if rt else 0,
                "logs_available": bool(rt and rt.owned),
            }
            if err:
                d["status"] = "error"
            elif rt and rt.alive():
                d["pid"] = rt.pid
                d["uptime"] = now - (rt.started_at or now)
                rss, cpu = self._tree_metrics(rt.pid)  # type: ignore[arg-type]
                d["rss"], d["cpu"] = rss, cpu
                if p.port is None or port_open:
                    d["status"] = "running"
                elif d["uptime"] < STARTING_GRACE:
                    d["status"] = "starting"
                else:
                    d["status"] = "unhealthy"
            elif rt and rt.restart_due is not None:
                d["status"] = "restarting"
                d["restart_due"] = rt.restart_due
            elif port_open:
                d["status"] = "external"
                d["pid"] = port_pid or None
                d["logs_available"] = False
                if port_pid:
                    rss, cpu = self._tree_metrics(port_pid)
                    d["rss"], d["cpu"] = rss, cpu
                    try:
                        d["uptime"] = now - psutil.Process(port_pid).create_time()
                    except psutil.Error:
                        pass
            elif rt and rt.crashed:
                d["status"] = "crashed"
            elif rt and rt.exit_code not in (None, 0) and not rt.manual_stop:
                d["status"] = "exited"
            out.append(d)
        return out
