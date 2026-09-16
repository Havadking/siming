"""从目录识别项目：看到 package.json / pyproject.toml 猜名字、id、启动命令、端口。

只是给表单预填，猜错了改一下就是。返回所有候选命令，表单里一点就换。
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ENTRY_FILES = ("main.py", "app.py", "server.py", "run.py", "manage.py")
PORT_IN_ARGS = re.compile(r"(?:--port|-p)[ =](\d{2,5})\b")
PORT_IN_ENV = re.compile(r"^\s*(?:export\s+)?PORT\s*=\s*[\"']?(\d{2,5})", re.M)
# `port: 5174` / `port = 8080` / `"port": 8080` / `DEFAULT_PORT = 17777`，配置文件和入口文件里通用
PORT_IN_SRC = re.compile(r"\b\w*port[\"']?\s*[:=]\s*[\"']?(\d{4,5})\b", re.I)
# 按这个顺序翻文件找端口；找到第一个就停
PORT_FILES = ("vite.config.*", "config.json", "config.*", "src/config.*", *ENTRY_FILES, "src/index.*", "src/server.*")


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "project"


def _port_from(text: str, pat: re.Pattern[str]) -> int | None:
    m = pat.search(text)
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 65535 else None


def _read(path: Path, limit: int = 64 * 1024) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _node(cwd: Path, pkg: dict) -> tuple[str | None, list[str], int | None]:
    scripts = pkg.get("scripts") or {}
    if not isinstance(scripts, dict):
        scripts = {}
    runner = "npm"
    if (cwd / "pnpm-lock.yaml").is_file():
        runner = "pnpm"
    elif (cwd / "yarn.lock").is_file():
        runner = "yarn"
    elif (cwd / "bun.lockb").is_file() or (cwd / "bun.lock").is_file():
        runner = "bun"

    def run(name: str) -> str:
        if name == "start" and runner == "npm":
            return "npm start"
        return f"{runner} run {name}" if runner == "npm" else f"{runner} {name}"

    order = ["dev", "start", "serve", "preview"]
    cands = [run(n) for n in order if n in scripts]
    cands += [run(n) for n in scripts if n not in order and not n.startswith(("build", "test", "lint", "pre", "post"))]
    port = None
    for n in order:
        if n in scripts:
            port = _port_from(str(scripts[n]), PORT_IN_ARGS)
            if port:
                break
    return (cands[0] if cands else None), cands, port


def _python(cwd: Path, pyproject: dict | None) -> tuple[str | None, list[str], int | None]:
    cands: list[str] = []
    # 有 pyproject 就当 uv 项目；--no-sync 是 README 里的约定（项目常驻时 sync 会失败）
    prefix = "uv run --no-sync " if pyproject is not None else ""
    scripts = ((pyproject or {}).get("project") or {}).get("scripts") or {}
    for name in scripts:
        cands.append(f"{prefix}{name}")
    entries = [f for f in ENTRY_FILES if (cwd / f).is_file()]
    for f in entries:
        cands.append(f"{prefix}python {f}")
    return (cands[0] if cands else None), cands, None


def detect(cwd: Path) -> dict:
    cwd = cwd.expanduser()
    out: dict = {"cwd": str(cwd), "kind": "unknown", "name": cwd.name, "id": slug(cwd.name),
                 "cmd": None, "candidates": [], "port": None, "notes": []}
    if not cwd.is_dir():
        out["notes"].append("目录不存在")
        return out

    pkg = None
    if (cwd / "package.json").is_file():
        try:
            pkg = json.loads(_read(cwd / "package.json", 1 << 20))
            if not isinstance(pkg, dict):
                pkg = None
        except ValueError:
            out["notes"].append("package.json 不是合法 JSON")
    pyproject = None
    if (cwd / "pyproject.toml").is_file():
        try:
            pyproject = tomllib.loads(_read(cwd / "pyproject.toml", 1 << 20))
        except tomllib.TOMLDecodeError:
            out["notes"].append("pyproject.toml 解析失败")
            pyproject = {}

    if pkg is not None:
        out["kind"] = "node"
        if isinstance(pkg.get("name"), str) and pkg["name"]:
            out["name"] = pkg["name"].split("/")[-1]
        cmd, cands, port = _node(cwd, pkg)
        out["cmd"], out["candidates"], out["port"] = cmd, cands, port
        if not cands:
            out["notes"].append("package.json 里没有 scripts")
        if not (cwd / "node_modules").is_dir():
            out["notes"].append("还没有 node_modules，先 npm install")
    elif pyproject is not None or any((cwd / f).is_file() for f in ENTRY_FILES):
        out["kind"] = "python"
        pname = ((pyproject or {}).get("project") or {}).get("name")
        if isinstance(pname, str) and pname:
            out["name"] = pname
        cmd, cands, port = _python(cwd, pyproject)
        out["cmd"], out["candidates"], out["port"] = cmd, cands, port
        if pyproject is not None and not (cwd / ".venv").is_dir():
            out["notes"].append("还没有 .venv，先 uv sync")
    else:
        out["notes"].append("没认出来：没有 package.json / pyproject.toml / main.py，命令自己填")

    out["id"] = slug(out["name"])
    if out["port"] is None:
        out["port"] = _guess_port(cwd)
    return out


def _guess_port(cwd: Path) -> int | None:
    for pat in PORT_FILES:
        for f in sorted(cwd.glob(pat)):
            if f.is_file():
                port = _port_from(_read(f), PORT_IN_SRC)
                if port:
                    return port
    if (cwd / ".env").is_file():
        return _port_from(_read(cwd / ".env"), PORT_IN_ENV)
    return None
