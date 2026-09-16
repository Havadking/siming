"""界面增删改接口：写回 yaml、校验分硬软、删除在跑的拒绝。"""

import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from devpanel import supervisor as S
from devpanel.api import create_app


@pytest.fixture
def env(tmp_path: Path):
    yml = tmp_path / "projects.yaml"
    yml.write_text(textwrap.dedent(f"""
        panel: {{port: 9100}}
        projects:
          - id: a
            cwd: {tmp_path.as_posix()}
            cmd: python -V     # 注释
            port: 5001
        """), encoding="utf-8")
    app = create_app(yml, autostart=False)
    client = TestClient(app)
    yield client, yml, app.state.panel
    for rt in app.state.panel.supervisor.runtimes.values():
        if rt.pid and rt.alive():
            S.kill_tree(rt.pid)


def test_validate_hard_and_soft(env):
    client, yml, _ = env
    r = client.post("/api/projects/validate", json={"project": {"id": "Bad", "cwd": "", "cmd": "nope-cmd", "port": 5001}})
    body = r.json()
    assert any("id 只能是" in m for m in body["hard"])
    assert any("缺少 cwd" in m for m in body["hard"])
    assert any("命令不存在" in m for m in body["soft"])
    assert any("port 5001 和 a 冲突" in m for m in body["soft"])
    # 编辑自己时不算和自己冲突
    r = client.post("/api/projects/validate", json={"project": {"id": "a", "cwd": str(yml.parent), "cmd": "python -V", "port": 5001}, "editing": "a"})
    assert r.json() == {"hard": [], "soft": []}


def test_create_update_delete(env):
    client, yml, _ = env
    r = client.post("/api/projects", json={"id": "b", "name": "乙", "cwd": str(yml.parent), "cmd": "python -V", "port": "5002", "env": {"X": "1"}})
    assert r.status_code == 201, r.text
    ids = [p["id"] for p in client.get("/api/projects").json()["projects"]]
    assert ids == ["a", "b"]
    assert "# 注释" in yml.read_text(encoding="utf-8")

    r = client.put("/api/projects/b", json={"id": "b", "cwd": str(yml.parent), "cmd": "python -c pass", "port": 5003})
    assert r.status_code == 200
    b = next(p for p in client.get("/api/projects").json()["projects"] if p["id"] == "b")
    assert b["port"] == 5003 and b["env"] == {} and b["name"] == "b"

    r = client.delete("/api/projects/b")
    assert r.status_code == 200
    assert [p["id"] for p in yaml.safe_load(yml.read_text(encoding="utf-8"))["projects"]] == ["a"]
    assert client.delete("/api/projects/b").status_code == 404


def test_create_rejects_hard_errors_but_allows_soft(env):
    client, yml, _ = env
    r = client.post("/api/projects", json={"id": "a", "cwd": str(yml.parent), "cmd": "python -V"})
    assert r.status_code == 422 and "id 重复" in r.json()["detail"]
    r = client.post("/api/projects", json={"id": "c", "cwd": str(yml.parent), "cmd": "no-such-cmd-xyz", "port": 5001})
    assert r.status_code == 201
    assert any("命令不存在" in m for m in r.json()["soft"])
    c = next(p for p in client.get("/api/projects").json()["projects"] if p["id"] == "c")
    assert c["status"] == "error"


def test_delete_running_refused(env):
    client, yml, _ = env
    code = "import time; time.sleep(30)"
    r = client.post("/api/projects", json={"id": "run", "cwd": str(yml.parent), "cmd": f'"{sys.executable}" -c "{code}"'})
    assert r.status_code == 201, r.text
    assert client.post("/api/projects/run/start").status_code == 200
    r = client.delete("/api/projects/run")
    assert r.status_code == 409
    assert client.post("/api/projects/run/stop").status_code == 200
    assert client.delete("/api/projects/run").status_code == 200


def test_order(env):
    client, yml, _ = env
    client.post("/api/projects", json={"id": "b", "cwd": str(yml.parent), "cmd": "python -V"})
    r = client.post("/api/projects/order", json=[{"id": "b", "group": "G"}, {"id": "a", "group": None}])
    assert r.status_code == 200
    ps = client.get("/api/projects").json()["projects"]
    assert [(p["id"], p["group"]) for p in ps] == [("b", "G"), ("a", None)]


def test_detect_endpoint(env, tmp_path):
    client, _, _ = env
    (tmp_path / "package.json").write_text('{"name":"x","scripts":{"dev":"vite --port 5199"}}')
    d = client.get("/api/detect", params={"cwd": str(tmp_path)}).json()
    assert d["cmd"] == "npm run dev" and d["port"] == 5199
