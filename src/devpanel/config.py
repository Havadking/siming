"""读 projects.yaml、校验、按 mtime 热重载。

校验失败不整体拒绝：单个项目带着 error 进列表，界面上标「配置错误」，其他照常。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
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
    desc: str | None = None                         # 一句话用途，显示在卡片名字下面
    env_file: Path | None = None                    # KEY=VALUE 文件，spawn 时读；放密码用，不进 git
    url_pattern: re.Pattern[str] | None = None      # 在 stdout 里匹配，捕获组 1 当「打开」链接（带 token 的地址）
    health: str | None = None                       # 健康检查：相对端口的路径（/api/health）或完整 URL
    health_url: str | None = None                   # 解析好的完整地址；有它就用 HTTP 响应代替端口探测
    argv: list[str] = field(default_factory=list)   # 解析好的命令，argv[0] 已经是绝对路径
    error: str | None = None                        # 配置错误原因；非空则不允许启动

    def runtime_error(self) -> str | None:
        """静态校验错误 + 每次现查的错误（env_file 是否存在）。"""
        if self.error:
            return self.error
        if self.env_file and not self.env_file.is_file():
            return f"env_file 不存在：{self.env_file}"
        return None

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
            "desc": self.desc,
            "env_file": str(self.env_file) if self.env_file else None,
            "url_pattern": self.url_pattern.pattern if self.url_pattern else None,
            "health": self.health,
            "health_url": self.health_url,
            "error": self.error,
        }


@dataclass
class Tool:
    id: str
    name: str
    file: Path
    desc: str | None = None
    error: str | None = None

    def runtime_error(self) -> str | None:
        if self.error:
            return self.error
        if not self.file.is_file():
            return f"文件不存在：{self.file}"
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "file": str(self.file),
            "desc": self.desc,
            "error": self.runtime_error(),
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
    groups: list[str] = field(default_factory=list)   # 顶层 groups: 列表；决定顺序，允许空组
    tools: list[Tool] = field(default_factory=list)

    @property
    def base_dir(self) -> Path:
        return self.path.parent

    def group_order(self) -> list[str]:
        """分组的展示顺序：groups: 里列的在前，项目里出现但没列的按首次出现补在后面。"""
        seen = list(self.groups)
        for p in self.projects:
            if p.group and p.group not in seen:
                seen.append(p.group)
        return seen


def _split_cmd(cmd: str) -> list[str]:
    # Windows 下 posix=False 会把引号原样留在 token 上，Popen 再次转义就多了一层，这里剥掉
    argv = shlex.split(cmd, posix=False)
    return [a[1:-1] if len(a) >= 2 and a[0] == a[-1] and a[0] in "\"'" else a for a in argv]


def clean_path() -> str:
    """去掉面板自己 venv 的 Scripts 目录后的 PATH。

    面板由 `uv run` 起，PATH 第一项是面板的 .venv/Scripts；项目里写 `python` 本意是系统 Python，
    不洗掉就会被解析到面板的解释器。子进程的 PATH 也用这个。
    """
    own = {Path(sys.prefix).resolve(), (Path(sys.prefix) / "Scripts").resolve(), (Path(sys.prefix) / "bin").resolve()}
    kept = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            if Path(entry).resolve() in own:
                continue
        except OSError:
            pass
        kept.append(entry)
    return os.pathsep.join(kept)


def resolve_executable(name: str, cwd: Path) -> str | None:
    """像 shell 一样找可执行文件：相对路径先按 cwd 找，再走 PATH（Windows 上能找到 npm.cmd / uv.exe）。"""
    if os.sep in name or "/" in name:
        p = (cwd / name) if not Path(name).is_absolute() else Path(name)
        return shutil.which(str(p))
    return shutil.which(name, path=clean_path())


def read_env_file(path: Path) -> dict[str, str]:
    """最简 dotenv：KEY=VALUE 一行一个，# 开头是注释，两边引号剥掉。不做变量展开。"""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


# 硬错误：这几条不允许写进 projects.yaml；其余的（cwd 不存在、命令没装、端口冲突）允许保存，卡片上标「配置错误」
HARD_PREFIXES = ("id 只能是", "缺少 cwd", "缺少 cmd", "id 重复")


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
        desc=(str(raw["desc"]).strip() or None) if raw.get("desc") else None,
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
    if raw.get("url_pattern"):
        try:
            p.url_pattern = re.compile(str(raw["url_pattern"]))
            if p.url_pattern.groups < 1:
                problems.append(r"url_pattern 需要一个捕获组，例如 'listening on (http\S+)'")
        except re.error as e:
            problems.append(f"url_pattern 不是合法正则：{e}")
    if raw.get("env_file"):
        ef = Path(str(raw["env_file"])).expanduser()
        if not ef.is_absolute():
            ef = cwd / ef
        p.env_file = ef   # 存不存在不在这里查：建 .env 不会改 yaml 的 mtime，放到 snapshot/start 时现查
    if p.url is None and p.port is not None:
        p.url = f"http://127.0.0.1:{p.port}"
    if raw.get("health"):
        h = str(raw["health"]).strip()
        p.health = h
        if h.startswith(("http://", "https://")):
            p.health_url = h
        elif p.port is None:
            problems.append("health 是相对路径时需要 port")
        else:
            p.health_url = f"http://127.0.0.1:{p.port}{'' if h.startswith('/') else '/'}{h}"
    if problems:
        p.error = "；".join(problems)
    return p


def _parse_tool(raw: dict, base_dir: Path) -> Tool:
    raw_id = str(raw.get("id", "")).strip()
    name = str(raw.get("name") or raw_id).strip()
    raw_file = str(raw.get("file", "")).strip()
    desc = raw.get("desc")
    desc_str = str(desc).strip() if desc is not None else None

    file_path = Path(raw_file).expanduser() if raw_file else Path()
    if not file_path.is_absolute() and raw_file:
        file_path = (base_dir / file_path).resolve()

    problems: list[str] = []
    tid = raw_id
    if not tid:
        base = file_path.stem.lower() if raw_file else "tool"
        tid = re.sub(r"[^a-z0-9-]", "-", base).strip("-")
        if not tid or not ID_RE.match(tid):
            import hashlib
            tid = f"t-{hashlib.md5((name or 'tool').encode()).hexdigest()[:6]}"
    elif not ID_RE.match(tid):
        problems.append("id 只能是 [a-z0-9-]，且不能为空")

    if not raw_file:
        problems.append("缺少 file")

    tool = Tool(id=tid, name=name or tid, file=file_path, desc=desc_str)
    if problems:
        tool.error = "；".join(problems)
    return tool


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

    groups: list[str] = []
    raw_groups = data.get("groups") or []
    if not isinstance(raw_groups, list):
        errors.append("groups 必须是列表")
    else:
        for g in raw_groups:
            name = str(g).strip() if g is not None else ""
            if name and name not in groups:
                groups.append(name)

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

    # 解析 tools
    tools: list[Tool] = []
    raw_tools = data.get("tools") or []
    if not isinstance(raw_tools, list):
        errors.append("tools 必须是列表")
    else:
        seen_tool_ids: set[str] = set()
        for i, raw in enumerate(raw_tools):
            if not isinstance(raw, dict):
                errors.append(f"tools[{i}] 不是映射，已忽略")
                continue
            t = _parse_tool(raw, path.parent)
            if t.id in seen_tool_ids:
                msg = f"tool id 重复：{t.id}"
                t.error = f"{t.error}；{msg}" if t.error else msg
                errors.append(msg)
            else:
                seen_tool_ids.add(t.id)
            tools.append(t)

    return Config(panel, projects, errors, path, mtime, groups, tools)


def append_tool_to_yaml(config_path: Path, name: str, file_str: str, desc: str | None = None) -> Tool:
    config_path = config_path.resolve()
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

    p = Path(file_str).expanduser()
    base = p.stem.lower() if file_str else "tool"
    tid = re.sub(r"[^a-z0-9-]", "-", base).strip("-")
    if not tid or not ID_RE.match(tid):
        import hashlib
        tid = f"t-{hashlib.md5(name.encode()).hexdigest()[:6]}"

    existing_cfg = load(config_path)
    existing_ids = {t.id for t in existing_cfg.tools}
    final_id = tid
    counter = 1
    while final_id in existing_ids:
        final_id = f"{tid}-{counter}"
        counter += 1

    entry_lines = [
        f"  - id: {final_id}",
        f"    name: {name}",
        f"    file: {file_str}",
    ]
    if desc:
        entry_lines.append(f"    desc: {desc}")
    entry_str = "\n".join(entry_lines) + "\n"

    match = re.search(r"^tools:\s*$", text, re.MULTILINE)
    if match:
        if text.endswith("\n"):
            new_text = text + entry_str
        else:
            new_text = text + "\n" + entry_str
    else:
        new_text = text.rstrip() + f"\n\ntools:\n{entry_str}"

    config_path.write_text(new_text, encoding="utf-8")
    return _parse_tool({"id": final_id, "name": name, "file": file_str, "desc": desc}, config_path.parent)


KNOWN_KEYS = ("id", "name", "desc", "cwd", "cmd", "port", "group", "autostart", "restart", "url", "url_pattern", "health", "env_file", "env")


def validate_raw(raw: dict, cfg: Config, *, editing: str | None = None) -> tuple[list[str], list[str]]:
    """界面表单的干跑校验。返回 (硬错误, 软错误)。

    硬错误拒绝保存；软错误允许保存但卡片会标「配置错误」。`editing` 是正在编辑的项目 id，
    查 id / port 冲突时把它自己排除掉。
    """
    p = _parse_project(raw, cfg.panel.port)
    problems = p.error.split("；") if p.error else []
    others = [x for x in cfg.projects if x.id != editing]
    if p.id and any(x.id == p.id for x in others):
        problems.append(f"id 重复：{p.id}")
    if p.port is not None:
        clash = [x.id for x in others if x.port == p.port]
        if clash:
            problems.append(f"port {p.port} 和 {'、'.join(clash)} 冲突")
    hard = [m for m in problems if m.startswith(HARD_PREFIXES)]
    soft = [m for m in problems if not m.startswith(HARD_PREFIXES)]
    return hard, soft


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
