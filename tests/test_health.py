"""健康检查：HTTP 2xx/3xx 通过，其他码 / 连不上 / 超时不通过；供 supervisor 判状态。"""

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from devpanel.health import HealthChecker, check_url


class Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def do_GET(self):
        if Handler.mode == "slow":
            time.sleep(3)
        code = {"ok": 200, "bad": 503, "missing": 404}.get(Handler.mode, 200)
        self.send_response(code)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):  # 安静
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Handler.mode = "ok"
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_ok_and_codes(server):
    r = check_url(server + "/health")
    assert r.ok and r.detail == "200" and r.latency_ms is not None
    Handler.mode = "bad"
    r = check_url(server + "/health")
    assert not r.ok and r.detail == "HTTP 503"
    Handler.mode = "missing"
    assert check_url(server + "/x").detail == "HTTP 404"


def test_refused_and_timeout(server):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    r = check_url(f"http://127.0.0.1:{port}/", timeout=5)   # Windows 报 refused 前要 ~2s
    assert not r.ok and r.detail == "连接被拒绝"
    Handler.mode = "slow"
    r = check_url(server + "/", timeout=0.5)
    assert not r.ok and r.detail.startswith("超时")


def test_checker_run_once_and_prune(server):
    hc = HealthChecker()
    hc.run_once([("a", server + "/"), ("b", server + "/")])
    assert hc.result("a").ok and hc.result("b").ok
    # 刚查过、通过了：5s 内不再查
    t = hc.result("a").checked_at
    hc.run_once([("a", server + "/")])
    assert hc.result("a").checked_at == t
    assert hc.result("b") is None       # 不在目标里了，结果丢掉
    hc.forget("a")
    assert hc.result("a") is None
