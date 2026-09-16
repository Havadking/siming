"""每个项目的日志：文件是唯一的真相，内存环形缓冲和 SSE 都是文件的 tail。

子进程的 stdout 不走管道，直接以追加模式写 logs/<id>.log——面板重启不会断掉它的 stdout
（管道一断，Node 往里写会 EPIPE 崩掉）。面板自己写的分隔行也追加到同一个文件。
一条 tail 线程盯着文件尾部，新内容解码、剥 ANSI 后进缓冲、推给 SSE 订阅者。

Windows 上所有写句柄都用 FILE_APPEND_DATA 打开：OS 保证每次写都落在文件尾，面板和子进程
各写各的不会互相覆盖；FILE_SHARE_DELETE 让滚动（rename）在子进程还握着句柄时也能做。
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r")
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3
BUFFER_LINES = 2000
REPLAY_LINES = 200
TAIL_INTERVAL = 0.15
PRELOAD_BYTES = 256 * 1024


def decode(raw: bytes) -> str:
    """UTF-8 → GBK → UTF-8 replace。Windows 上 npm 的报错偶尔是 GBK。"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def clean(line: str) -> str:
    return ANSI_RE.sub("", line).rstrip("\n")


def open_append(path: Path):
    """追加写的二进制文件对象。Windows 走 CreateFileW(FILE_APPEND_DATA, 共享读写删)。"""
    if os.name != "nt":
        return open(path, "ab")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    FILE_APPEND_DATA = 0x0004
    SYNCHRONIZE = 0x00100000
    SHARE_ALL = 0x1 | 0x2 | 0x4          # READ | WRITE | DELETE
    OPEN_ALWAYS = 4
    FILE_ATTRIBUTE_NORMAL = 0x80
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(path), FILE_APPEND_DATA | SYNCHRONIZE, SHARE_ALL, None,
                        OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)
    if h in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    fd = msvcrt.open_osfhandle(h, os.O_APPEND | os.O_BINARY)
    return os.fdopen(fd, "ab", buffering=0)


class ProjectLog:
    def __init__(self, path: Path):
        self.path = path
        self.buffer: deque[str] = deque(maxlen=BUFFER_LINES)
        self._lock = threading.Lock()
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[str | None]]] = set()
        self._watchers: list[Callable[[str], None]] = []
        self._fh = None
        self._pos = 0            # tail 读到文件的哪里
        self._carry = b""        # 上次读到的不完整行
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._preload()
        self._stop = threading.Event()
        threading.Thread(target=self._tail_loop, name=f"tail:{path.stem}", daemon=True).start()

    # ----- 文件 -----

    def _preload(self) -> None:
        """面板重启后内存缓冲是空的，先从文件尾部捞最近几百行回来；tail 从文件末尾开始。"""
        try:
            with self.path.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - PRELOAD_BYTES))
                raw = f.read()
        except OSError:
            self._pos = 0
            return
        self._pos = size
        lines = decode(raw).splitlines()
        if size > PRELOAD_BYTES and lines:
            lines = lines[1:]  # 第一行可能被截断
        self.buffer.extend(clean(l) for l in lines[-REPLAY_LINES:])

    def _writer(self):
        if self._fh is None:
            self._fh = open_append(self.path)
        return self._fh

    def open_for_child(self):
        """给子进程当 stdout 的句柄。调用方 spawn 完自己 close()，子进程拿的是复制品。"""
        return open_append(self.path)

    def rotate_if_needed(self) -> None:
        """单文件超过 5MB 就滚动，留 3 份。在项目启动前调，别在它跑的时候滚。"""
        with self._lock:
            try:
                if self.path.stat().st_size < MAX_BYTES:
                    return
            except OSError:
                return
            if self._fh:
                self._fh.close()
                self._fh = None
            try:
                for i in range(BACKUPS - 1, 0, -1):
                    src = self.path.with_suffix(f".log.{i}")
                    dst = self.path.with_suffix(f".log.{i + 1}")
                    if src.exists():
                        src.replace(dst)
                self.path.replace(self.path.with_suffix(".log.1"))
            except OSError:
                return  # 有人握着且不许改名，那就先不滚
            self._pos = 0
            self._carry = b""

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None

    # ----- 写入（面板自己的分隔行） -----

    def append(self, line: str) -> None:
        data = line.encode("utf-8", errors="replace") + b"\n"
        with self._lock:
            try:
                self._writer().write(data)
            except OSError:
                return
            self._poll_locked()  # 立刻进缓冲，别等 tail 线程那 150ms

    def mark(self, text: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.append(f"===== {stamp} {text} =====")

    # ----- tail -----

    def _tail_loop(self) -> None:
        while not self._stop.wait(TAIL_INTERVAL):
            with self._lock:
                self._poll_locked()

    def _poll_locked(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size == self._pos:
            return
        if size < self._pos:          # 被滚动 / 截断了，从头读
            self._pos, self._carry = 0, b""
        try:
            with self.path.open("rb") as f:
                f.seek(self._pos)
                chunk = f.read(size - self._pos)
        except OSError:
            return
        self._pos = size
        data = self._carry + chunk
        *lines, self._carry = data.split(b"\n")
        if not lines:
            return
        out = [clean(decode(raw)) for raw in lines]
        self.buffer.extend(out)
        subs = list(self._subs)
        watchers = list(self._watchers)
        for line in out:
            for loop, q in subs:
                try:
                    loop.call_soon_threadsafe(q.put_nowait, line)
                except RuntimeError:
                    pass  # 事件循环已关
            for fn in watchers:
                try:
                    fn(line)
                except Exception:
                    pass

    def poll(self) -> None:
        """同步拉一次 tail。测试和「刚 spawn 完想立刻看到输出」用。"""
        with self._lock:
            self._poll_locked()

    def watch(self, fn: Callable[[str], None]) -> Callable[[], None]:
        """每来一行调一次 fn（tail 线程里）。返回取消函数。"""
        with self._lock:
            self._watchers.append(fn)

        def unwatch() -> None:
            with self._lock:
                if fn in self._watchers:
                    self._watchers.remove(fn)

        return unwatch

    def tail(self, n: int) -> list[str]:
        with self._lock:
            if n <= 0:
                return []
            return list(self.buffer)[-n:]

    # ----- 订阅 -----

    def subscribe(self) -> tuple[list[str], asyncio.Queue[str | None]]:
        """返回（回放的最近几行, 后续的队列）。在事件循环里调。"""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[str | None] = asyncio.Queue()
        with self._lock:
            replay = list(self.buffer)[-REPLAY_LINES:]
            self._subs.add((loop, q))
        return replay, q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = {s for s in self._subs if s[1] is not q}


class LogManager:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self._logs: dict[str, ProjectLog] = {}
        self._lock = threading.Lock()

    def get(self, project_id: str) -> ProjectLog:
        with self._lock:
            log = self._logs.get(project_id)
            if log is None:
                log = ProjectLog(self.log_dir / f"{project_id}.log")
                self._logs[project_id] = log
            return log
