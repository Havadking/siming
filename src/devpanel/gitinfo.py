"""每个项目目录的 git 信息：分支、未提交条数、ahead/behind、最近一次提交。

只看不动。跑的是 `git status --porcelain=v2 --branch` 和 `git log -1`，
带 GIT_OPTIONAL_LOCKS=0——status 顺手刷新索引时会写 index.lock，用户正在终端里 commit 会撞上。
GitCache 一条后台线程 30s 刷一轮，没人看（90s 没读过）就不跑，见 DESIGN.md 10.1。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

GIT_TIMEOUT = 10.0
REFRESH_INTERVAL = 30.0
IDLE_AFTER = 90.0            # 超过这么久没人读缓存，后台线程空转
MIN_GAP = 2.0                # 新目录触发的立即刷新，两次之间至少隔这么久
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class GitInfo:
    branch: str | None = None       # detached 时是短 sha
    detached: bool = False
    dirty: int = 0                  # status 里非 # 开头的行数：改动 + 未跟踪 + 冲突
    ahead: int = 0
    behind: int = 0
    upstream: str | None = None
    commit_at: float | None = None  # 最近一次提交的 unix 时间；空仓库为 None
    commit_msg: str | None = None
    error: str | None = None        # git 跑失败（超时之类）；不是仓库时整个 GitInfo 是 None

    def to_dict(self) -> dict:
        return asdict(self)


def git_exe() -> str | None:
    return shutil.which("git")


def _run(git: str, cwd: Path, *args: str) -> tuple[int, str]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(
        [git, *args], cwd=str(cwd), env=env, capture_output=True, timeout=GIT_TIMEOUT,
        creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
    )
    return r.returncode, r.stdout.decode("utf-8", errors="replace")


def _parse_status(text: str) -> GitInfo:
    info = GitInfo()
    oid = None
    for line in text.splitlines():
        if not line.startswith("# "):
            if line.strip():
                info.dirty += 1
            continue
        key, _, val = line[2:].partition(" ")
        if key == "branch.oid":
            oid = val
        elif key == "branch.head":
            if val == "(detached)":
                info.detached = True
            else:
                info.branch = val
        elif key == "branch.upstream":
            info.upstream = val
        elif key == "branch.ab":
            for tok in val.split():
                if tok.startswith("+"):
                    info.ahead = int(tok[1:] or 0)
                elif tok.startswith("-"):
                    info.behind = int(tok[1:] or 0)
    if info.detached and oid and oid != "(initial)":
        info.branch = oid[:7]
    return info


def read_git(cwd: Path, git: str | None = None) -> GitInfo | None:
    """不是仓库 / git 没装 → None；跑失败 → GitInfo(error=...)。"""
    git = git or git_exe()
    if git is None or not cwd.is_dir():
        return None
    try:
        code, out = _run(git, cwd, "status", "--porcelain=v2", "--branch", "--untracked-files=normal")
    except subprocess.TimeoutExpired:
        return GitInfo(error=f"git status 超时（{GIT_TIMEOUT:.0f}s）")
    except OSError as e:
        return GitInfo(error=f"git 跑不起来：{e}")
    if code == 128:
        return None   # not a git repository（或 safe.directory 拦住了，同样当作不是仓库）
    if code != 0:
        return GitInfo(error=f"git status 退出码 {code}")
    info = _parse_status(out)
    try:
        code, out = _run(git, cwd, "log", "-1", "--format=%ct%x1f%s")
    except (subprocess.TimeoutExpired, OSError):
        code, out = 1, ""
    if code == 0 and out.strip():
        ts, _, msg = out.strip().partition("\x1f")
        try:
            info.commit_at = float(ts)
        except ValueError:
            pass
        info.commit_msg = msg.strip() or None
    return info


class GitCache:
    """{cwd: GitInfo | None}，后台线程定期刷新。get() 记一下「有人在看」。"""

    def __init__(self, interval: float = REFRESH_INTERVAL):
        self.interval = interval
        self._data: dict[Path, GitInfo | None] = {}
        self._lock = threading.Lock()
        self._last_get = 0.0
        self._last_refresh = 0.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._git = git_exe()

    def get(self, cwd: Path) -> dict | None:
        self._last_get = time.time()
        with self._lock:
            if cwd not in self._data:
                self._wake.set()     # 新目录：叫醒后台线程马上刷
                return None
            info = self._data[cwd]
        return info.to_dict() if info else None

    def refresh(self, cwds: Iterable[Path]) -> None:
        """同步刷一轮。丢掉不在清单里的目录。"""
        wanted = list(dict.fromkeys(cwds))
        fresh = {cwd: read_git(cwd, self._git) for cwd in wanted}
        with self._lock:
            self._data = fresh
        self._last_refresh = time.time()

    def start(self, get_cwds: Callable[[], list[Path]]) -> None:
        threading.Thread(target=self._loop, args=(get_cwds,), name="gitcache", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self, get_cwds: Callable[[], list[Path]]) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            if self._stop.is_set():
                return
            now = time.time()
            if now - self._last_get > IDLE_AFTER:
                continue   # 没人看
            cwds = get_cwds()
            with self._lock:
                has_new = any(c not in self._data for c in cwds)
            due = now - self._last_refresh >= self.interval or (has_new and now - self._last_refresh >= MIN_GAP)
            if not due:
                continue
            try:
                self.refresh(cwds)
            except Exception:   # noqa: BLE001  后台线程不能死
                self._last_refresh = now
