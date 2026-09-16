"""读 projects.yaml、校验、按 mtime 热重载。

校验失败不整体拒绝：单个项目带着 error 进列表，界面上标「配置错误」，其他照常。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DEFAULT_PANEL_PORT = 9000


@dataclass
class Project:
    id: str
    name: str
    cwd: Path
    cmd: str
    port: int | None
    url: str | None = None
    autostart: bool = False
    restart: str = "never"          # on-failure | never
    env: dict[str, str] = field(default_factory=dict)
    group: str | None = None
    argv: list[str] = field(default_factory=list)   # 解析好的命令，argv[0] 已经是绝对路径
    error: str | None = None                        # 配置错误原因；非空则不允许启动

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "cwd": str(self.cwd),
            "cmd": self.cmd,
            "port": self.port,
            "url": self.url,
            "autostart": self.autostart,
            "restart": self.restart,
            "env": self.env,
            "group": self.group,
            "error": self.error,
        }


@dataclass
class PanelConfig:
    port: int = DEFAULT_PANEL_PORT
    open_browser: bool = True


@dataclass
class Config:
    panel: PanelConfig
    projects: list[Project]
    errors: list[str]          # 全局错误（文件本身坏了、重复 id 之类）
    path: Path
    mtime: float

    @property
    def base_dir(self) -> Path:
        return self.path.parent


def _split_cmd(cmd: str) -> list[str]:
    # Windows 下 posix=False 会把引号原样留在 token 上，Popen 再次转义就多了一层，这里剥掉
    argv = shlex.split(cmd, posix=False)
    return [a[1:-1] if len(a) >= 2 and a[0] == a[-1] and a[0] in "\"'" else a for a in argv]


def resolve_executable(name: str, cwd: Path) -> str | None:
    """像 shell 一样找可执行文件：相对路径先按 cwd 找，再走 PATH（Windows 上能找到 npm.cmd / uv.exe）。"""
    if os.sep in name or "/" in name:
        p = (cwd / name) if not Path(name).is_absolute() else Path(name)
        found = shutil.which(str(p))
        return found
    return shutil.which(name)


def _parse_project(raw: dict, panel_port: int) -> Project:
    pid = str(raw.get("id", "")).strip()
    name = str(raw.get("name") or pid)
    cwd = Path(str(raw.get("cwd", ""))).expanduser()
    cmd = str(raw.get("cmd", "")).strip()
    port = raw.get("port")
    env = {str(k): str(v) for k, v in (raw.get("env") or {}).items()}
    p = Project(
        id=pid, name=name, cwd=cwd, cmd=cmd, port=int(port) if port is not None else None,
        url=raw.get("url"), autostart=bool(raw.get("autostart", False)),
        restart=str(raw.get("restart", "never")), env=env, group=raw.get("group"),
    )
    problems: list[str] = []
    if not pid or not ID_RE.match(pid):
        problems.append("id 只能是 [a-z0-9-]，且不能为空")
    if not raw.get("cwd"):
        problems.append("缺少 cwd")
    elif not cwd.is_dir():
        problems.append(f"cwd 不存在：{cwd}")
    if not cmd:
        problems.append("缺少 cmd")
    else:
        try:
            argv = _split_cmd(cmd)
        except ValueError as e:
            argv = []
            problems.append(f"cmd 解析失败：{e}")
        if argv:
            exe = resolve_executable(argv[0], cwd if cwd.is_dir() else Path.cwd())
            if exe is None:
                problems.append(f"命令不存在：{argv[0]}")
            else:
                p.argv = [exe, *argv[1:]]
    if p.port is not None:
        if not (1 <= p.port <= 65535):
            problems.append(f"port 不合法：{p.port}")
        elif p.port == panel_port:
            problems.append(f"port {p.port} 和面板自己冲突")
    if p.restart not in ("on-failure", "never"):
        problems.append(f"restart 只能是 on-failure / never，不是 {p.restart}")
    if p.url is None and p.port is not None:
        p.url = f"http://127.0.0.1:{p.port}"
    if problems:
        p.error = "；".join(problems)
    return p


def load(path: Path) -> Config:
    path = path.resolve()
    errors: list[str] = []
    try:
        mtime = path.stat().st_mtime
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return Config(PanelConfig(), [], [f"找不到配置文件 {path}"], path, 0.0)
    except (OSError, yaml.YAMLError) as e:
        mtime = path.stat().st_mtime if path.exists() else 0.0
        return Config(PanelConfig(), [], [f"配置文件读取失败：{e}"], path, mtime)

    if not isinstance(data, dict):
        return Config(PanelConfig(), [], ["配置文件顶层必须是映射"], path, mtime)

    panel_raw = data.get("panel") or {}
    panel = PanelConfig(
        port=int(panel_raw.get("port", DEFAULT_PANEL_PORT)),
        open_browser=bool(panel_raw.get("open_browser", True)),
    )

    projects: list[Project] = []
    raw_list = data.get("projects") or []
    if not isinstance(raw_list, list):
        errors.append("projects 必须是列表")
        raw_list = []
    for i, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            errors.append(f"projects[{i}] 不是映射，已忽略")
            continue
        projects.append(_parse_project(raw, panel.port))

    # 跨项目校验：id 唯一、port 不冲突
    seen_ids: dict[str, Project] = {}
    for p in projects:
        if p.id in seen_ids:
            msg = f"id 重复：{p.id}"
            p.error = f"{p.error}；{msg}" if p.error else msg
            errors.append(msg)
        else:
            seen_ids[p.id] = p
    by_port: dict[int, list[Project]] = {}
    for p in projects:
        if p.port is not None:
            by_port.setdefault(p.port, []).append(p)
    for port, ps in by_port.items():
        if len(ps) > 1:
            names = "、".join(x.id for x in ps)
            for x in ps:
                msg = f"port {port} 和 {names} 冲突"
                x.error = f"{x.error}；{msg}" if x.error else msg
            errors.append(f"port {port} 被多个项目使用：{names}")

    return Config(panel, projects, errors, path, mtime)


class ConfigWatcher:
    """持有当前生效的 Config，每次 current() 时 stat 一下文件，mtime 变了就重载。"""

    def __init__(self, path: Path):
        self.path = path.resolve()
        self._cfg = load(self.path)

    def current(self) -> Config:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != self._cfg.mtime:
            self._cfg = load(self.path)
        return self._cfg

    def reload(self) -> Config:
        self._cfg = load(self.path)
        return self._cfg
