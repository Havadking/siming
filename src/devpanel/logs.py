"""每个项目的日志：滚动文件 + 内存环形缓冲 + SSE 订阅者。

读管道的线程调 append()；SSE 端在事件循环里 subscribe()。跨线程投递用 call_soon_threadsafe。
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections import deque
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r")
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3
BUFFER_LINES = 2000
REPLAY_LINES = 200


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


class ProjectLog:
    def __init__(self, path: Path):
        self.path = path
        self.buffer: deque[str] = deque(maxlen=BUFFER_LINES)
        self._lock = threading.Lock()
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[str | None]]] = set()
        self._fh = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._preload()

    # ----- 文件 -----

    def _preload(self) -> None:
        """面板重启后内存缓冲是空的，先从文件尾部捞最近几百行回来，抽屉里不至于一片空白。"""
        try:
            with self.path.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 256 * 1024))
                tail = f.read()
        except OSError:
            return
        lines = decode(tail).splitlines()
        if len(tail) >= 256 * 1024 and lines:
            lines = lines[1:]  # 第一行可能被截断
        self.buffer.extend(lines[-REPLAY_LINES:])

    def _open(self):
        if self._fh is None:
            self._fh = self.path.open("ab")
        return self._fh

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.stat().st_size < MAX_BYTES:
                return
        except OSError:
            return
        if self._fh:
            self._fh.close()
            self._fh = None
        for i in range(BACKUPS - 1, 0, -1):
            src = self.path.with_suffix(f".log.{i}")
            dst = self.path.with_suffix(f".log.{i + 1}")
            if src.exists():
                src.replace(dst)
        self.path.replace(self.path.with_suffix(".log.1"))

    def close(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None

    # ----- 写入 -----

    def append(self, line: str) -> None:
        line = clean(line)
        with self._lock:
            self.buffer.append(line)
            try:
                self._rotate_if_needed()
                fh = self._open()
                fh.write(line.encode("utf-8", errors="replace") + b"\n")
                fh.flush()
            except OSError:
                pass
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, line)
            except RuntimeError:
                pass  # 事件循环已关

    def mark(self, text: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.append(f"===== {stamp} {text} =====")

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
