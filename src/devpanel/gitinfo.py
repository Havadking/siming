"""每个项目目录的 git 信息：分支、未提交条数、ahead/behind、最近一次提交、云端还有什么没合进来。

读的部分只看不动：`git status --porcelain=v2 --branch`、`git log -1`、`git for-each-ref --no-merged`，
带 GIT_OPTIONAL_LOCKS=0——status 顺手刷新索引时会写 index.lock，用户正在终端里 commit 会撞上。
GitCache 一条后台线程 30s 刷一轮，没人看（90s 没读过）就不跑，见 DESIGN.md 10.1。

动的只有两件（DESIGN.md 11）：隔 10 分钟 `git fetch --prune origin`，和用户点了才跑的 merge_refs——
Claude 云端会话把代码推到 origin/claude/*，本地分支不知道，一键合进来。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

GIT_TIMEOUT = 10.0
REFRESH_INTERVAL = 30.0
IDLE_AFTER = 90.0            # 超过这么久没人读缓存，后台线程空转
MIN_GAP = 2.0                # 新目录触发的立即刷新，两次之间至少隔这么久
FETCH_INTERVAL = 600.0       # 后台 fetch 的间隔；同样没人看就不跑
NET_TIMEOUT = 60.0           # fetch / push
MERGE_TIMEOUT = 120.0
CLOUD_REFS = "refs/remotes/origin/claude"   # Claude 云端会话推的分支
MAX_SUBJECTS = 8
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
    # 远端有、HEAD 里还没有的：上游落后的部分 + origin/claude/* 里没合进来的分支。见 read_incoming
    incoming: list[dict] = field(default_factory=list)
    fetched_at: float | None = None   # 面板上次成功 fetch 的时间；没 fetch 过为 None
    fetch_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def git_exe() -> str | None:
    return shutil.which("git")


def _run(git: str, cwd: Path, *args: str) -> tuple[int, str]:
    code, out, _ = _run_full(git, cwd, *args)
    return code, out


def _run_full(git: str, cwd: Path, *args: str, timeout: float = GIT_TIMEOUT) -> tuple[int, str, str]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_MERGE_AUTOEDIT": "no"}
    r = subprocess.run(
        [git, *args], cwd=str(cwd), env=env, capture_output=True, timeout=timeout,
        creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
    )
    return (r.returncode, r.stdout.decode("utf-8", errors="replace"),
            r.stderr.decode("utf-8", errors="replace"))


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


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


def _pending_commits(git: str, cwd: Path, ref: str) -> list[tuple[str, str]] | None:
    """ref 上有、HEAD 里没有的非合并提交 [(unix 时间, 标题)]，新的在前。

    --cherry-pick 去掉内容已经在 HEAD 里的（cherry-pick / rebase 过来的）。git 跑失败返回 None。
    """
    code, out = _run(git, cwd, "log", "--right-only", "--cherry-pick", "--no-merges",
                     "--format=%ct%x1f%s", f"HEAD...{ref}")
    if code != 0:
        return None
    return [(ts, msg.strip()) for ts, _, msg in (ln.partition("\x1f") for ln in out.splitlines() if ln.strip())]


def read_incoming(git: str, cwd: Path, upstream: str | None, behind: int,
                  ignored: dict[str, str] | None = None) -> list[dict]:
    """HEAD 还没包含的远端提交，按分支列出：[{ref, sha, kind, count, at, subjects}]。

    - 上游（通常 origin/main）落后时算一条，kind="upstream"。
    - origin/claude/* 里 `--no-merged HEAD` 的分支，kind="cloud"；提交全都已在 HEAD 里（按内容比）的不列。
    - ignored {ref: sha}：用户点过「忽略」的分支，tip 还是那个 sha 就不列；有了新提交会重新出现。
    只读本地 refs，不联网；新不新取决于上次 fetch。
    """
    ignored = ignored or {}
    cands: list[tuple[str, str]] = []
    if upstream and behind > 0:
        cands.append((upstream, "upstream"))
    code, out = _run(git, cwd, "for-each-ref", "--no-merged=HEAD", "--format=%(refname:short)", CLOUD_REFS)
    if code == 0:
        cands += [(r.strip(), "cloud") for r in out.splitlines() if r.strip() and r.strip() != upstream]
    result = []
    for ref, kind in cands:
        code, sha = _run(git, cwd, "rev-parse", "--verify", "-q", f"refs/remotes/{ref}^{{commit}}")
        sha = sha.strip()
        if code != 0 or not sha or ignored.get(ref) == sha:
            continue
        commits = _pending_commits(git, cwd, sha)
        if not commits:
            continue
        try:
            at = float(commits[0][0])
        except ValueError:
            at = None
        # 反过来：HEAD 有、它没有的提交数。分支越旧这个越大，合起来越可能冲突
        code, n = _run(git, cwd, "rev-list", "--count", f"{sha}..HEAD")
        result.append({"ref": ref, "sha": sha, "kind": kind, "count": len(commits), "at": at,
                       "behind_head": int(n.strip()) if code == 0 and n.strip().isdigit() else 0,
                       "subjects": [msg for _, msg in commits[:MAX_SUBJECTS]]})
    # 上游在前，云端分支按最近提交从旧到新——合并也照这个顺序
    result.sort(key=lambda d: (d["kind"] != "upstream", d["at"] or 0))
    return result


def read_git(cwd: Path, git: str | None = None, ignored: dict[str, str] | None = None) -> GitInfo | None:
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
    if not info.detached and info.commit_at is not None:
        try:
            info.incoming = read_incoming(git, cwd, info.upstream, info.behind, ignored)
        except (subprocess.TimeoutExpired, OSError):
            pass
    return info


def fetch(cwd: Path, git: str | None = None) -> str | None:
    """git fetch --prune origin。成功返回 None，失败返回一句原因。没有 origin 也算成功（没东西可拉）。

    --prune：云端分支在 GitHub 上删了，本地的 origin/claude/* 跟着消失，不然会一直挂在「待合并」里。
    """
    git = git or git_exe()
    if git is None:
        return "git 没装"
    try:
        code, out = _run(git, cwd, "remote")
        if code != 0 or "origin" not in out.split():
            return None
        code, _, err = _run_full(git, cwd, "fetch", "--prune", "--quiet", "origin", timeout=NET_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"fetch 超时（{NET_TIMEOUT:.0f}s）"
    except OSError as e:
        return f"git 跑不起来：{e}"
    if code != 0:
        return _last_line(err) or f"fetch 退出码 {code}"
    return None


class MergeError(Exception):
    """合并前的检查没过，一个 ref 都没动。"""


def merge_refs(cwd: Path, refs: list[str], *, push: bool = False, git: str | None = None) -> dict:
    """把远端分支 refs 依次合进当前分支。能快进就快进，否则生成一个合并提交。

    前置：不是 detached、没有进行中的 merge/rebase/cherry-pick、已跟踪的文件没有未提交改动
    （未跟踪文件不管——真会被覆盖时 git 自己会拒绝，什么都不动）。不满足抛 MergeError。
    某个 ref 冲突：`merge --abort` 回到合它之前，记下来，接着合后面的——各分支互不依赖，
    一个旧分支冲突不该挡住别的。
    push=True 且至少合进一个：推当前分支（有上游就 `git push`，没有就 `git push -u origin <branch>`）。

    返回 {merged: [ref], failed: [{ref, message, conflicts}], pushed, push_error}。
    """
    git = git or git_exe()
    if git is None:
        raise MergeError("git 没装")
    code, head = _run(git, cwd, "symbolic-ref", "-q", "--short", "HEAD")
    branch = head.strip()
    if code != 0 or not branch:
        raise MergeError("当前是 detached HEAD，先切到一个分支")
    for marker, what in (("MERGE_HEAD", "合并"), ("REBASE_HEAD", "rebase"), ("CHERRY_PICK_HEAD", "cherry-pick")):
        if _run(git, cwd, "rev-parse", "-q", "--verify", marker)[0] == 0:
            raise MergeError(f"有一个没做完的 {what}，先在终端里处理掉")
    code, out = _run(git, cwd, "status", "--porcelain=v1", "--untracked-files=no")
    if code != 0:
        raise MergeError(f"git status 退出码 {code}")
    changed = [ln for ln in out.splitlines() if ln.strip()]
    if changed:
        raise MergeError(f"有 {len(changed)} 个文件改了没提交，先提交或 stash 再合并")
    for ref in refs:
        if _run(git, cwd, "rev-parse", "-q", "--verify", f"refs/remotes/{ref}^{{commit}}")[0] != 0:
            raise MergeError(f"不认识的远端分支：{ref}")

    merged: list[str] = []
    failed: list[dict] = []
    for ref in refs:
        commits = _pending_commits(git, cwd, f"refs/remotes/{ref}")
        if not commits:
            continue   # 已经都在了
        title = f"合并云端分支 {ref.removeprefix('origin/')}" if ref.startswith("origin/claude/") else f"合并 {ref}"
        body = "\n".join(f"- {msg}" for _, msg in reversed(commits))
        try:
            code, out, err = _run_full(git, cwd, "merge", "--no-edit", "--no-stat", "-m", f"{title}\n\n{body}",
                                       f"refs/remotes/{ref}", timeout=MERGE_TIMEOUT)
        except subprocess.TimeoutExpired:
            code, out, err = 1, "", f"merge 超时（{MERGE_TIMEOUT:.0f}s）"
        if code == 0:
            merged.append(ref)
            continue
        _, unmerged = _run(git, cwd, "diff", "--name-only", "--diff-filter=U")
        conflicts = [ln.strip() for ln in unmerged.splitlines() if ln.strip()]
        if _run(git, cwd, "rev-parse", "-q", "--verify", "MERGE_HEAD")[0] == 0:
            _run(git, cwd, "merge", "--abort")
        message = "有冲突，已撤销这次合并" if conflicts else (_last_line(err) or _last_line(out) or f"merge 退出码 {code}")
        failed.append({"ref": ref, "message": message, "conflicts": conflicts})

    pushed, push_error = False, None
    if push and merged:
        code, _ = _run(git, cwd, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        args = ["push", "--quiet"] if code == 0 else ["push", "--quiet", "-u", "origin", branch]
        try:
            code, _, err = _run_full(git, cwd, *args, timeout=NET_TIMEOUT)
            pushed = code == 0
            push_error = None if pushed else (_last_line(err) or f"push 退出码 {code}")
        except subprocess.TimeoutExpired:
            push_error = f"push 超时（{NET_TIMEOUT:.0f}s）"
    return {"merged": merged, "failed": failed, "pushed": pushed, "push_error": push_error}


class GitCache:
    """{cwd: GitInfo | None}，后台线程定期刷新。get() 记一下「有人在看」。

    同一个 cwd 的写操作（fetch / merge）用一把锁串起来，后台 fetch 和手点的合并不会撞上。
    「忽略」过的云端分支 {cwd: {ref: sha}} 存在 state_dir/git_ignored.json，面板重启还记得。
    """

    def __init__(self, interval: float = REFRESH_INTERVAL, state_dir: Path | None = None,
                 fetch_interval: float = FETCH_INTERVAL):
        self.interval = interval
        self.fetch_interval = fetch_interval
        self._data: dict[Path, GitInfo | None] = {}
        self._fetched: dict[Path, tuple[float | None, str | None]] = {}   # cwd → (上次成功时间, 最近一次的错误)
        self._lock = threading.Lock()
        self._op_locks: dict[Path, threading.Lock] = {}
        self._last_get = 0.0
        self._last_refresh = 0.0
        self._last_fetch = 0.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._git = git_exe()
        self._ignored_path = state_dir / "git_ignored.json" if state_dir else None
        self._ignored: dict[str, dict[str, str]] = self._load_ignored()

    # ----- 读 -----

    def get(self, cwd: Path) -> dict | None:
        self._last_get = time.time()
        with self._lock:
            if cwd not in self._data:
                self._wake.set()     # 新目录：叫醒后台线程马上刷
                return None
            info = self._data[cwd]
        return info.to_dict() if info else None

    def _read(self, cwd: Path) -> GitInfo | None:
        info = read_git(cwd, self._git, self._ignored.get(str(cwd)))
        if info is not None:
            info.fetched_at, info.fetch_error = self._fetched.get(cwd, (None, None))
        return info

    def refresh(self, cwds: Iterable[Path]) -> None:
        """同步刷一轮。丢掉不在清单里的目录。"""
        wanted = list(dict.fromkeys(cwds))
        fresh = {cwd: self._read(cwd) for cwd in wanted}
        with self._lock:
            self._data = fresh
        self._last_refresh = time.time()

    def refresh_one(self, cwd: Path) -> dict | None:
        info = self._read(cwd)
        with self._lock:
            self._data[cwd] = info
        return info.to_dict() if info else None

    # ----- 写 -----

    def _op_lock(self, cwd: Path) -> threading.Lock:
        with self._lock:
            return self._op_locks.setdefault(cwd, threading.Lock())

    def fetch(self, cwd: Path) -> str | None:
        with self._op_lock(cwd):
            err = fetch(cwd, self._git)
        prev_at = self._fetched.get(cwd, (None, None))[0]
        self._fetched[cwd] = (prev_at, err) if err else (time.time(), None)
        return err

    def fetch_all(self, cwds: Iterable[Path]) -> None:
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="gitfetch") as pool:
            list(pool.map(self.fetch, list(dict.fromkeys(cwds))))
        self._last_fetch = time.time()

    def merge(self, cwd: Path, refs: list[str], *, push: bool = False) -> dict:
        """MergeError 原样往外抛。"""
        with self._op_lock(cwd):
            return merge_refs(cwd, refs, push=push, git=self._git)

    def ignore(self, cwd: Path, ref: str, sha: str) -> None:
        with self._lock:
            self._ignored.setdefault(str(cwd), {})[ref] = sha
            self._save_ignored()

    def _load_ignored(self) -> dict[str, dict[str, str]]:
        if not self._ignored_path or not self._ignored_path.is_file():
            return {}
        try:
            data = json.loads(self._ignored_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_ignored(self) -> None:
        if not self._ignored_path:
            return
        self._ignored_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._ignored_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._ignored, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._ignored_path)

    # ----- 后台线程 -----

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
            fetch_due = self.fetch_interval > 0 and now - self._last_fetch >= self.fetch_interval
            due = fetch_due or now - self._last_refresh >= self.interval or (has_new and now - self._last_refresh >= MIN_GAP)
            if not due:
                continue
            try:
                if has_new and fetch_due:
                    self.refresh(cwds)     # 先把本地能看的亮出来，fetch 可能要好几秒
                if fetch_due:
                    self.fetch_all(cwds)
                self.refresh(cwds)
            except Exception:   # noqa: BLE001  后台线程不能死
                self._last_refresh = now
                if fetch_due:
                    self._last_fetch = now
