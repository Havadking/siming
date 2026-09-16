import sys
import textwrap
from pathlib import Path

from devpanel.config import ConfigWatcher, load


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "projects.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_load_ok(tmp_path):
    p = write(tmp_path, f"""
    panel: {{port: 9100}}
    projects:
      - id: a
        name: A
        cwd: {tmp_path.as_posix()}
        cmd: python -c "print(1)"
        port: 5000
        group: g
    """)
    cfg = load(p)
    assert cfg.panel.port == 9100
    assert cfg.errors == []
    a = cfg.projects[0]
    assert a.error is None
    assert a.url == "http://127.0.0.1:5000"
    assert Path(a.argv[0]).name.lower().startswith("python")
    assert a.argv[1:] == ["-c", "print(1)"]  # 引号被剥掉


def test_validation_errors(tmp_path):
    p = write(tmp_path, f"""
    projects:
      - id: Bad_ID
        cwd: {tmp_path.as_posix()}/nope
        cmd: definitely-not-a-command-xyz
        port: 9000
      - id: dup
        cwd: {tmp_path.as_posix()}
        cmd: python -V
        port: 5001
      - id: dup
        cwd: {tmp_path.as_posix()}
        cmd: python -V
        port: 5001
    """)
    cfg = load(p)
    bad, d1, d2 = cfg.projects
    assert "id 只能是" in bad.error
    assert "cwd 不存在" in bad.error
    assert "命令不存在" in bad.error
    assert "和面板自己冲突" in bad.error
    assert "id 重复" in d2.error
    assert "port 5001" in d1.error and "port 5001" in d2.error
    assert any("重复" in e for e in cfg.errors)


def test_missing_file(tmp_path):
    cfg = load(tmp_path / "nope.yaml")
    assert cfg.projects == []
    assert cfg.errors


def test_watcher_reloads_on_mtime(tmp_path):
    p = write(tmp_path, "projects: []\n")
    w = ConfigWatcher(p)
    assert w.current().projects == []
    import os, time
    write(tmp_path, f"projects:\n  - {{id: x, cwd: {tmp_path.as_posix()}, cmd: python -V}}\n")
    # 同一秒内写两次 mtime 可能不变，手动往后拨
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert [x.id for x in w.current().projects] == ["x"]


def test_relative_executable(tmp_path):
    exe = Path(sys.executable)
    p = write(tmp_path, f"""
    projects:
      - id: r
        cwd: {exe.parent.as_posix()}
        cmd: ./{exe.name} -V
    """)
    cfg = load(p)
    assert cfg.projects[0].error is None
