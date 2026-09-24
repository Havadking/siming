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
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True, encoding="utf-8").stdout


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


# ----- 云端分支：fetch / 待合并 / 一键合并（DESIGN.md 11）-----

def commit(d: Path, name: str, content: str, msg: str) -> None:
    (d / name).write_text(content, encoding="utf-8")
    git(d, "add", name)
    git(d, "commit", "-q", "-m", msg)


@pytest.fixture
def remote_pair(tmp_path, monkeypatch):
    """origin（裸仓库，当 GitHub）+ local（本机）+ cloud（云端会话的克隆）。"""
    for k in ("AUTHOR", "COMMITTER"):       # merge_refs 用的是进程环境，合并提交要有身份
        monkeypatch.setenv(f"GIT_{k}_NAME", "t")
        monkeypatch.setenv(f"GIT_{k}_EMAIL", "t@x")
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    local = tmp_path / "local"
    git(tmp_path, "clone", "-q", str(origin), str(local))
    commit(local, "a.txt", "1\n", "init")
    git(local, "push", "-q", "-u", "origin", "main")
    cloud = tmp_path / "cloud"
    git(tmp_path, "clone", "-q", str(origin), str(cloud))
    return origin, local, cloud


def cloud_push(cloud: Path, branch: str, files: dict[str, str], msg: str) -> None:
    git(cloud, "checkout", "-q", "-B", branch, "origin/main")
    for name, content in files.items():
        commit(cloud, name, content, msg)
    git(cloud, "push", "-q", "-f", "origin", branch)


def test_incoming_cloud_branch_and_merge(remote_pair):
    origin, local, cloud = remote_pair
    cloud_push(cloud, "claude/feat-x", {"b.txt": "cloud\n"}, "feat: 云端加了 b")
    commit(local, "c.txt", "local\n", "fix: 本地改了 c")          # 本地也往前走了一步：分叉

    assert read_git(local).incoming == []                         # 没 fetch 不知道
    assert G.fetch(local) is None
    info = read_git(local)
    [inc] = info.incoming
    assert inc["ref"] == "origin/claude/feat-x" and inc["kind"] == "cloud"
    assert inc["count"] == 1 and inc["subjects"] == ["feat: 云端加了 b"]
    assert inc["behind_head"] == 1                                  # 本地那个 c 它没有

    r = G.merge_refs(local, ["origin/claude/feat-x"], push=True)
    assert r["merged"] == ["origin/claude/feat-x"] and r["failed"] == []
    assert r["pushed"] and r["push_error"] is None
    assert (local / "b.txt").read_text() == "cloud\n" and (local / "c.txt").exists()
    msg = git(local, "log", "-1", "--format=%B")
    assert msg.startswith("合并云端分支 claude/feat-x") and "- feat: 云端加了 b" in msg
    assert read_git(local).incoming == []
    assert git(origin, "rev-parse", "main") == git(local, "rev-parse", "HEAD")


def test_upstream_fast_forward_counts_as_incoming(remote_pair):
    origin, local, cloud = remote_pair
    git(cloud, "checkout", "-q", "main")
    commit(cloud, "d.txt", "x", "别处推到 main")
    git(cloud, "push", "-q", "origin", "main")
    G.fetch(local)
    info = read_git(local)
    assert info.behind == 1
    assert [(i["ref"], i["kind"]) for i in info.incoming] == [("origin/main", "upstream")]
    r = G.merge_refs(local, ["origin/main"])
    assert r["merged"] == ["origin/main"]
    assert git(local, "log", "-1", "--format=%s").strip() == "别处推到 main"   # 快进，没有合并提交


def test_merge_conflict_aborts_and_continues(remote_pair):
    _, local, cloud = remote_pair
    cloud_push(cloud, "claude/clash", {"a.txt": "cloud\n"}, "clash")
    cloud_push(cloud, "claude/ok", {"e.txt": "e\n"}, "ok")
    commit(local, "a.txt", "local\n", "本地改 a")
    G.fetch(local)
    head_before = git(local, "rev-parse", "HEAD")
    r = G.merge_refs(local, ["origin/claude/clash", "origin/claude/ok"])   # 冲突的在前也不挡后面的
    assert r["merged"] == ["origin/claude/ok"]
    [f] = r["failed"]
    assert f["ref"] == "origin/claude/clash" and f["conflicts"] == ["a.txt"]
    assert (local / "a.txt").read_text() == "local\n"                   # 冲突那个撤掉了
    assert git(local, "status", "--porcelain") == ""
    assert git(local, "rev-parse", "HEAD") != head_before                 # ok 那个留着
    assert [i["ref"] for i in read_git(local).incoming] == ["origin/claude/clash"]


def test_merge_refuses_dirty_and_unknown(remote_pair):
    _, local, _ = remote_pair
    (local / "a.txt").write_text("改了没提交")
    with pytest.raises(G.MergeError, match="没提交"):
        G.merge_refs(local, ["origin/main"])
    git(local, "checkout", "-q", "--", "a.txt")
    (local / "untracked.txt").write_text("未跟踪的不拦")
    with pytest.raises(G.MergeError, match="不认识"):
        G.merge_refs(local, ["origin/nope"])


def test_ignore_until_new_commit(remote_pair, tmp_path):
    _, local, cloud = remote_pair
    cloud_push(cloud, "claude/stale", {"s.txt": "1"}, "旧的")
    c = GitCache(state_dir=tmp_path / "state")
    assert c.fetch(local) is None
    [inc] = c.refresh_one(local)["incoming"]
    assert c.refresh_one(local)["fetched_at"] is not None
    c.ignore(local, inc["ref"], inc["sha"])
    assert c.refresh_one(local)["incoming"] == []
    assert GitCache(state_dir=tmp_path / "state").refresh_one(local)["incoming"] == []   # 落盘了

    git(cloud, "checkout", "-q", "claude/stale")
    commit(cloud, "s.txt", "2", "又推了一个")
    git(cloud, "push", "-q", "origin", "claude/stale")
    c.fetch(local)
    [inc] = c.refresh_one(local)["incoming"]
    assert inc["count"] == 2


def test_fetch_prunes_deleted_cloud_branch(remote_pair):
    _, local, cloud = remote_pair
    cloud_push(cloud, "claude/gone", {"g.txt": "1"}, "g")
    G.fetch(local)
    assert read_git(local).incoming
    git(cloud, "push", "-q", "origin", "--delete", "claude/gone")
    G.fetch(local)
    assert read_git(local).incoming == []


# ----- 一键推送（DESIGN.md 12）-----

def test_outgoing_and_push(remote_pair):
    origin, local, _ = remote_pair
    commit(local, "p.txt", "1", "feat: 要推的第一个")
    commit(local, "q.txt", "2", "fix: 要推的第二个")
    info = read_git(local)
    assert info.ahead == 2 and info.outgoing == ["fix: 要推的第二个", "feat: 要推的第一个"]
    assert G.push_branch(local) is None
    info = read_git(local)
    assert info.ahead == 0 and info.outgoing == []
    assert git(origin, "rev-parse", "main") == git(local, "rev-parse", "HEAD")


def test_push_new_branch_sets_upstream(remote_pair):
    origin, local, _ = remote_pair
    git(local, "checkout", "-q", "-b", "topic")
    commit(local, "t.txt", "t", "topic")
    assert read_git(local).upstream is None
    assert G.push_branch(local) is None
    assert read_git(local).upstream == "origin/topic"
    assert git(origin, "rev-parse", "topic") == git(local, "rev-parse", "HEAD")


def test_push_rejected_when_remote_ahead_never_forces(remote_pair):
    origin, local, cloud = remote_pair
    git(cloud, "checkout", "-q", "main")
    commit(cloud, "r.txt", "remote", "别处推的")
    git(cloud, "push", "-q", "origin", "main")
    commit(local, "l.txt", "local", "本地的")
    remote_head = git(origin, "rev-parse", "main")
    err = G.push_branch(local)
    assert err and "先把云端改动合进来" in err
    assert git(origin, "rev-parse", "main") == remote_head          # 远端没被动


def test_push_errors(tmp_path):
    d = repo(tmp_path)
    commit(d, "a.txt", "1", "x")
    assert "没有 origin" in G.push_branch(d)
    git(d, "checkout", "-q", "--detach")
    assert "detached" in G.push_branch(d)
