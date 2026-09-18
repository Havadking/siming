"""git 信息：分支 / 未提交 / 最近提交；不是仓库返回 None；缓存按需刷新。"""

import subprocess
import time
from pathlib import Path

import pytest

from devpanel import gitinfo as G
from devpanel.gitinfo import GitCache, _parse_status, read_git

pytestmark = pytest.mark.skipif(G.git_exe() is None, reason="没装 git")


def git(cwd: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x",
           "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cwd), "PATH": __import__("os").environ["PATH"]}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


def repo(tmp_path: Path) -> Path:
    d = tmp_path / "r"
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    return d


def test_not_a_repo(tmp_path):
    assert read_git(tmp_path) is None
    assert read_git(tmp_path / "missing") is None


def test_empty_repo(tmp_path):
    d = repo(tmp_path)
    info = read_git(d)
    assert info is not None and info.error is None
    assert info.branch == "main"
    assert info.commit_at is None and info.dirty == 0


def test_branch_dirty_commit(tmp_path):
    d = repo(tmp_path)
    (d / "a.txt").write_text("1")
    git(d, "add", "a.txt")
    git(d, "commit", "-q", "-m", "first: 中文标题")
    info = read_git(d)
    assert info.branch == "main" and not info.detached
    assert info.dirty == 0
    assert info.commit_msg == "first: 中文标题"
    assert abs(info.commit_at - time.time()) < 120

    (d / "a.txt").write_text("2")          # 改动
    (d / "b.txt").write_text("new")        # 未跟踪
    info = read_git(d)
    assert info.dirty == 2

    git(d, "checkout", "-q", "--detach")
    info = read_git(d)
    assert info.detached and len(info.branch) == 7


def test_parse_ahead_behind():
    text = "# branch.oid abcdef1234567\n# branch.head main\n# branch.upstream origin/main\n# branch.ab +2 -1\n1 .M N... 100644 100644 100644 x x a.txt\n? b.txt\n"
    info = _parse_status(text)
    assert (info.ahead, info.behind, info.upstream, info.dirty) == (2, 1, "origin/main", 2)


def test_cache_refresh_and_prune(tmp_path):
    d = repo(tmp_path)
    c = GitCache()
    assert c.get(d) is None          # 还没刷
    c.refresh([d, tmp_path])
    assert c.get(d)["branch"] == "main"
    assert c.get(tmp_path) is None   # 不是仓库
    c.refresh([tmp_path])
    assert d not in c._data          # 不在清单里的丢掉


def test_cache_thread_idle_and_wake(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "IDLE_AFTER", 0.5)
    monkeypatch.setattr(G, "MIN_GAP", 0.0)
    d = repo(tmp_path)
    c = GitCache(interval=100)
    c.start(lambda: [d])
    try:
        time.sleep(0.3)
        assert c._data == {}          # 没人 get 过，不跑
        c.get(d)                      # 叫醒：新目录立即刷
        t0 = time.time()
        while c.get(d) is None and time.time() - t0 < 5:
            time.sleep(0.05)
        assert c.get(d)["branch"] == "main"
    finally:
        c.stop()
