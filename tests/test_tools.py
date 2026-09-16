from pathlib import Path
import textwrap
import pytest
from fastapi.testclient import TestClient

from devpanel.config import load, append_tool_to_yaml, _parse_tool
from devpanel.api import create_app


def write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "projects.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_parse_tool(tmp_path):
    f = tmp_path / "hello.html"
    f.write_text("<h1>Hi</h1>", encoding="utf-8")

    # 完整配置
    t1 = _parse_tool({"id": "my-tool", "name": "我的工具", "file": str(f), "desc": "测试"}, tmp_path)
    assert t1.id == "my-tool"
    assert t1.name == "我的工具"
    assert t1.file == f
    assert t1.desc == "测试"
    assert t1.runtime_error() is None

    # 自动派生 id
    t2 = _parse_tool({"name": "正则", "file": str(f)}, tmp_path)
    assert t2.id == "hello"
    assert t2.name == "正则"
    assert t2.runtime_error() is None

    # 相对路径解析
    t3 = _parse_tool({"file": "hello.html"}, tmp_path)
    assert t3.file == f
    assert t3.runtime_error() is None

    # 不存在的文件
    t4 = _parse_tool({"name": "空", "file": "nope.html"}, tmp_path)
    assert "文件不存在" in t4.runtime_error()


def test_append_tool_to_yaml(tmp_path):
    p = write_yaml(tmp_path, """
    panel: {port: 9000}
    projects: []
    """)

    f1 = tmp_path / "tool1.html"
    f1.write_text("ok", encoding="utf-8")

    tool1 = append_tool_to_yaml(p, "工具1", str(f1), "说明1")
    assert tool1.name == "工具1"
    assert tool1.desc == "说明1"

    cfg = load(p)
    assert len(cfg.tools) == 1
    assert cfg.tools[0].name == "工具1"
    assert cfg.tools[0].file == f1

    # 追加第二个同名文件派生 id 不重复
    f2 = tmp_path / "tool1.html"
    tool2 = append_tool_to_yaml(p, "工具2", str(f2))
    assert tool2.id != tool1.id

    cfg2 = load(p)
    assert len(cfg2.tools) == 2


def test_tools_api(tmp_path, monkeypatch):
    html_file = tmp_path / "demo.html"
    html_file.write_text("<p>demo html</p>", encoding="utf-8")

    cfg_path = write_yaml(tmp_path, f"""
    panel: {{port: 9000}}
    projects: []
    tools:
      - id: demo
        name: 演示工具
        file: {html_file.as_posix()}
        desc: 测试演示
    """)

    app = create_app(cfg_path, autostart=False)
    client = TestClient(app)

    # 1. 列表
    r = client.get("/api/tools")
    assert r.status_code == 200
    tools = r.json()["tools"]
    assert len(tools) == 1
    assert tools[0]["id"] == "demo"
    assert tools[0]["name"] == "演示工具"

    # 2. 查看页面
    r_view = client.get("/view-tool/demo")
    assert r_view.status_code == 200
    assert "demo html" in r_view.text

    # 查看不存在的页面
    assert client.get("/view-tool/nope").status_code == 404

    # 3. 添加新工具
    f_new = tmp_path / "new_tool.html"
    f_new.write_text("<div>new</div>", encoding="utf-8")
    r_add = client.post("/api/tools", json={
        "name": "新工具",
        "file": str(f_new),
        "desc": "新工具说明"
    })
    assert r_add.status_code == 201
    assert r_add.json()["name"] == "新工具"

    # 确认列表更新
    r2 = client.get("/api/tools")
    assert len(r2.json()["tools"]) == 2

    # 4. 模拟 pick-file
    monkeypatch.setattr("devpanel.api.pick_html_file", lambda initial: "E:/fake/file.html")
    r_pick = client.post("/api/tools/pick-file")
    assert r_pick.status_code == 200
    assert r_pick.json()["path"] == "E:/fake/file.html"
