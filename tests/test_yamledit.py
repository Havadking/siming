import textwrap
from pathlib import Path

import pytest
import yaml

from devpanel import yamledit as Y

SAMPLE = """\
# 顶部注释
panel:
  port: 9000            # 面板端口

projects:
  - id: a
    name: 甲
    cwd: E:/x/a
    cmd: npm run dev      # 行尾注释
    port: 5001
    group: 常用

  - id: b
    cwd: E:/x/b
    cmd: uv run --no-sync b
    port: 5002              # b 的端口
    autostart: true
    env:
      FOO: "1"
    group: 监控

  - id: c
    cwd: E:/x/c
    cmd: python c.py
"""


@pytest.fixture
def yml(tmp_path: Path) -> Path:
    p = tmp_path / "projects.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def load(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def test_clean_drops_empty_and_defaults():
    raw = {"id": " x ", "name": "x", "cwd": "E:/x", "cmd": "npm start", "port": "", "group": "",
           "autostart": False, "restart": "never", "url": None, "env": {"": "1", "A": "b"}}
    assert Y.clean(raw) == {"id": "x", "cwd": "E:/x", "cmd": "npm start", "env": {"A": "b"}}
    assert Y.clean({"id": "x", "port": "80", "autostart": True, "restart": "on-failure"}) == \
        {"id": "x", "port": 80, "autostart": True, "restart": "on-failure"}


def test_add_keeps_comments(yml):
    Y.add_project(yml, {"id": "d", "name": "丁", "cwd": "E:/x/d", "cmd": "npm start", "port": 5004, "env": {"K": "v"}})
    text = yml.read_text(encoding="utf-8")
    assert "# 顶部注释" in text and "# 面板端口" in text and "# 行尾注释" in text and "# b 的端口" in text
    data = load(yml)
    assert [p["id"] for p in data["projects"]] == ["a", "b", "c", "d"]
    assert data["projects"][3] == {"id": "d", "name": "丁", "cwd": "E:/x/d", "cmd": "npm start", "port": 5004, "env": {"K": "v"}}
    assert data["panel"]["port"] == 9000
    assert "\n\n  - id: d\n" in text and not text.endswith("\n\n")   # 项目之间空一行，末尾不留空行


def test_add_duplicate_id(yml):
    with pytest.raises(Y.EditError) as ei:
        Y.add_project(yml, {"id": "a", "cwd": "E:/x", "cmd": "x"})
    assert ei.value.status == 409


def test_update_in_place_and_removes_cleared_keys(yml):
    Y.update_project(yml, "b", {"id": "b", "cwd": "E:/x/b", "cmd": "uv run --no-sync b2", "port": 5002,
                                "autostart": False, "env": {"FOO": "2", "BAR": "3"}, "group": "监控"})
    text = yml.read_text(encoding="utf-8")
    assert "# b 的端口" in text          # 没改的键，注释还在
    data = load(yml)
    b = data["projects"][1]
    assert b["cmd"] == "uv run --no-sync b2"
    assert "autostart" not in b          # 清掉了
    assert b["env"] == {"FOO": "2", "BAR": "3"}
    assert list(b.keys()).index("cmd") < list(b.keys()).index("port")   # 顺序没乱


def test_update_id_is_immutable_and_new_keys_in_order(yml):
    Y.update_project(yml, "c", {"id": "zzz", "cwd": r"E:\x\c", "cmd": "python c.py", "name": "丙", "port": 5003})
    c = load(yml)["projects"][2]
    assert c["id"] == "c" and c["name"] == "丙"
    assert c["cwd"] == "E:/x/c"                                  # 反斜杠归一成正斜杠
    assert list(c.keys()) == ["id", "name", "cwd", "cmd", "port"]   # 新键插在该在的位置


def test_update_missing(yml):
    with pytest.raises(Y.EditError) as ei:
        Y.update_project(yml, "nope", {"cwd": "x", "cmd": "y"})
    assert ei.value.status == 404


def test_delete(yml):
    Y.delete_project(yml, "b")
    data = load(yml)
    assert [p["id"] for p in data["projects"]] == ["a", "c"]
    text = yml.read_text(encoding="utf-8")
    assert "# 行尾注释" in text and "# 顶部注释" in text


def test_reorder_and_regroup(yml):
    Y.reorder(yml, [{"id": "c", "group": "常用"}, {"id": "b", "group": ""}])
    data = load(yml)
    ps = data["projects"]
    assert [p["id"] for p in ps] == ["c", "b", "a"]
    assert ps[0]["group"] == "常用"
    assert "group" not in ps[1]
    assert ps[2]["group"] == "常用"
    text = yml.read_text(encoding="utf-8")
    assert "# b 的端口" in text and "# 行尾注释" in text
    assert text.count("\n\n  - id:") == 2 and "\n\n\n" not in text   # 空行分隔仍然整齐


def test_reorder_only_group_change_keeps_order(yml):
    Y.reorder(yml, [{"id": "a", "group": "新组"}, {"id": "b", "group": "监控"}, {"id": "c", "group": None}])
    ps = load(yml)["projects"]
    assert [p["id"] for p in ps] == ["a", "b", "c"]
    assert ps[0]["group"] == "新组"


def test_add_to_empty_file(tmp_path):
    p = tmp_path / "projects.yaml"
    Y.add_project(p, {"id": "x", "cwd": "E:/x", "cmd": "npm start"})
    assert load(p) == {"projects": [{"id": "x", "cwd": "E:/x", "cmd": "npm start"}]}
    p.write_text("panel:\n  port: 9100\nprojects:\n", encoding="utf-8")
    Y.add_project(p, {"id": "y", "cwd": "E:/y", "cmd": "npm start"})
    assert load(p) == {"panel": {"port": 9100}, "projects": [{"id": "y", "cwd": "E:/y", "cmd": "npm start"}]}


def test_refuses_broken_top_level(tmp_path):
    p = tmp_path / "projects.yaml"
    p.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(Y.EditError):
        Y.add_project(p, {"id": "x", "cwd": "E:/x", "cmd": "npm start"})
