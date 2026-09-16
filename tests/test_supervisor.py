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
    assert sup.logs.get("a").tail(1) == ["up"]
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
    assert sup.logs.get("g").tail(2)[0] == "yes from-yaml False"
