import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from devpanel import supervisor as S
from devpanel.config import Project
from devpanel.logs import LogManager
from devpanel.supervisor import ActionError, Supervisor, listening_ports

LISTEN = "import socket,time,sys;s=socket.socket();s.bind(('127.0.0.1',{port}));s.listen();print('up',flush=True);time.sleep(60)"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def project(tmp_path: Path, pid: str, code: str, port: int | None, restart="never") -> Project:
    argv = [sys.executable, "-c", code]
    return Project(id=pid, name=pid, cwd=tmp_path, cmd=subprocess.list2cmdline(argv), port=port,
                   restart=restart, argv=argv)


@pytest.fixture
def sup(tmp_path):
    s = Supervisor(tmp_path / "state", LogManager(tmp_path / "logs"))
    yield s
    for rt in s.runtimes.values():
        if rt.pid and rt.alive():
            S.kill_tree(rt.pid)


def wait_for(pred, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.1)
    return False


def status(sup, p):
    return sup.snapshot([p])[0]


def test_start_running_stop(sup, tmp_path):
    port = free_port()
    p = project(tmp_path, "a", LISTEN.format(port=port), port)
    sup.start(p)
    assert status(sup, p)["status"] == "starting"
    assert wait_for(lambda: status(sup, p)["status"] == "running")
    d = status(sup, p)
    assert d["pid"] and d["rss"] > 0 and d["uptime"] >= 0
    assert wait_for(lambda: sup.logs.get("a").tail(1) == ["up"])
    assert sup.runtimes["a"].proc.stdout is None   # stdout 是文件不是管道，面板死了它也不会 EPIPE
    with pytest.raises(ActionError) as ei:
        sup.start(p)
    assert ei.value.status == 409

    sup.stop(p)
    assert port not in listening_ports()
    assert wait_for(lambda: status(sup, p)["status"] == "stopped")
    assert status(sup, p)["exit_code"] is not None


def test_nonzero_exit_backoff_then_crashed(sup, tmp_path, monkeypatch):
    monkeypatch.setattr(S, "backoff", lambda n: 0.05)
    p = project(tmp_path, "b", "import sys; sys.exit(3)", None, restart="on-failure")
    sup.start(p)
    assert wait_for(lambda: status(sup, p)["status"] == "crashed", timeout=20)
    d = status(sup, p)
    assert d["failures"] == S.MAX_RESTARTS + 1
    assert d["restart_count"] == S.MAX_RESTARTS
    assert d["exit_code"] == 3
    # 手动启动清零计数
    sup.start(p)
    assert wait_for(lambda: status(sup, p)["status"] == "crashed", timeout=20)


def test_nonzero_exit_without_restart_policy(sup, tmp_path):
    p = project(tmp_path, "c", "import sys; sys.exit(1)", None, restart="never")
    sup.start(p)
    assert wait_for(lambda: status(sup, p)["status"] == "exited")
    assert status(sup, p)["exit_code"] == 1


def test_unhealthy_when_port_never_opens(sup, tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STARTING_GRACE", 0.3)
    p = project(tmp_path, "d", "import time; time.sleep(30)", free_port())
    sup.start(p)
    assert wait_for(lambda: status(sup, p)["status"] == "unhealthy")


def test_external_detect_and_stop(sup, tmp_path):
    port = free_port()
    ext = subprocess.Popen([sys.executable, "-c", LISTEN.format(port=port)],
                           stdout=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        assert ext.stdout.readline().strip() == b"up"
        p = project(tmp_path, "e", "print()", port)
        d = status(sup, p)
        # venv 里的 python.exe 是 launcher，真正监听的可能是它的子进程
        import psutil
        tree = {ext.pid, *(c.pid for c in psutil.Process(ext.pid).children(recursive=True))}
        assert d["status"] == "external" and d["pid"] in tree and d["logs_available"] is False
        with pytest.raises(ActionError) as ei:
            sup.start(p)
        assert ei.value.status == 409
        sup.stop(p)
        assert ext.wait(timeout=5) is not None
        assert status(sup, p)["status"] == "stopped"
    finally:
        if ext.poll() is None:
            ext.kill()


def test_adopt_saved_after_panel_restart(sup, tmp_path):
    port = free_port()
    p = project(tmp_path, "f", LISTEN.format(port=port), port)
    sup.start(p)
    assert wait_for(lambda: status(sup, p)["status"] == "running")
    # 「面板重启」：新的 Supervisor 读同一个 state 目录
    sup2 = Supervisor(tmp_path / "state", LogManager(tmp_path / "logs"))
    assert sup2.adopt_saved([p]) == ["f"]
    d = status(sup2, p)
    assert d["status"] == "running" and d["logs_available"] is True and d["pid"] == sup.runtimes["f"].pid
    sup2.stop(p)
    assert wait_for(lambda: status(sup2, p)["status"] == "stopped")


def test_env_file_and_clean_path_reach_child(sup, tmp_path):
    (tmp_path / ".env").write_text("FROM_FILE=yes\n", encoding="utf-8")
    code = "import os;print(os.environ['FROM_FILE'], os.environ['OVERRIDE'], 'VIRTUAL_ENV' in os.environ, flush=True)"
    p = project(tmp_path, "g", code, None)
    p.env_file = tmp_path / ".env"
    p.env = {"OVERRIDE": "from-yaml"}
    sup.start(p)
    assert wait_for(lambda: status(sup, p)["status"] == "stopped")
    assert wait_for(lambda: "yes from-yaml False" in sup.logs.get("g").tail(3))


def test_missing_env_file_blocks_start_until_created(sup, tmp_path):
    p = project(tmp_path, "h", "print('hi', flush=True)", None)
    p.env_file = tmp_path / "later.env"
    assert status(sup, p)["status"] == "error"
    with pytest.raises(ActionError) as ei:
        sup.start(p)
    assert ei.value.status == 400
    (tmp_path / "later.env").write_text("X=1\n", encoding="utf-8")
    assert status(sup, p)["status"] == "stopped"   # 不用重载配置
    sup.start(p)
    assert wait_for(lambda: status(sup, p)["status"] == "stopped" and status(sup, p)["exit_code"] == 0)


def test_url_pattern_from_stdout(sup, tmp_path):
    import re
    code = "import time;print('ready at http://127.0.0.1:1234/?token=abc', flush=True);time.sleep(30)"
    p = project(tmp_path, "u", code, None)
    p.url = "http://127.0.0.1:1234"
    p.url_pattern = re.compile(r"ready at (http\S+)")
    sup.start(p)
    assert wait_for(lambda: status(sup, p)["url"] == "http://127.0.0.1:1234/?token=abc")
    # 面板重启后从 pids.json 恢复
    sup2 = Supervisor(tmp_path / "state", LogManager(tmp_path / "logs"))
    sup2.adopt_saved([p])
    assert status(sup2, p)["url"] == "http://127.0.0.1:1234/?token=abc"
    sup2.stop(p)
    assert wait_for(lambda: status(sup2, p)["url"] == "http://127.0.0.1:1234")


def test_manual_stop_is_remembered_across_panel_restart(sup, tmp_path):
    port = free_port()
    p = project(tmp_path, "m", LISTEN.format(port=port), port)
    sup.start(p)
    assert wait_for(lambda: status(sup, p)["status"] == "running")
    sup.stop(p)
    assert "m" in sup.manual_stopped
    # 「面板重启」：新 Supervisor 读同一个 state，知道这是用户停的
    sup2 = Supervisor(tmp_path / "state", LogManager(tmp_path / "logs"))
    assert sup2.manual_stopped == {"m"}
    # 用户再手动启动就清掉
    sup2.start(p)
    assert "m" not in sup2.manual_stopped
    sup3 = Supervisor(tmp_path / "state", LogManager(tmp_path / "logs"))
    assert sup3.manual_stopped == set()
    sup2.stop(p)


def test_health_url_overrides_port(sup, tmp_path):
    """配了 health：端口通了也不算在线，要健康检查通过；检查结果得是本次启动之后的。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        code = 503

        def do_GET(self):
            self.send_response(H.code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = free_port()
        p = project(tmp_path, "h", LISTEN.format(port=port), port)
        p.health = "/health"
        p.health_url = f"http://127.0.0.1:{srv.server_port}/health"
        # 启动前塞一个「通过」的旧结果：启动会把它清掉，不能靠它翻绿
        sup.health.check_now("h", p.health_url.replace("/health", "/"))
        H.code = 200
        sup.health.check_now("h", p.health_url)
        assert sup.health.result("h").ok
        sup.start(p)
        assert sup.health.result("h") is None
        assert wait_for(lambda: port in listening_ports())
        assert status(sup, p)["status"] == "starting"          # 端口通了，但还没检查过
        assert sup.health_targets([p]) == [("h", p.health_url)]
        H.code = 503
        sup.health.check_now("h", p.health_url)
        d = status(sup, p)
        assert d["status"] == "starting" and d["health_result"]["detail"] == "HTTP 503"
        H.code = 200
        sup.health.check_now("h", p.health_url)
        d = status(sup, p)
        assert d["status"] == "running" and d["health_result"]["ok"]
        sup.stop(p)
        assert sup.health_targets([p]) == []
    finally:
        srv.shutdown()
