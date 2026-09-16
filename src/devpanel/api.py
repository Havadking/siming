"""REST + SSE + 静态前端。动作接口都是同步的：start 返回时已 spawn，stop 返回时树已死。"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from . import __version__
from .config import Config, ConfigWatcher, Project
from .logs import LogManager
from .supervisor import ActionError, Supervisor

DIST_DIR = Path(__file__).parent / "web" / "dist"


class PanelState:
    def __init__(self, config_path: Path):
        self.watcher = ConfigWatcher(config_path)
        cfg = self.watcher.current()
        self.logs = LogManager(cfg.base_dir / "logs")
        self.supervisor = Supervisor(cfg.base_dir / "state", self.logs)

    def config(self) -> Config:
        return self.watcher.current()

    def project(self, project_id: str) -> Project:
        for p in self.config().projects:
            if p.id == project_id:
                return p
        raise HTTPException(404, f"没有这个项目：{project_id}")

    def boot(self) -> None:
        """serve 起来后：认领旧进程 → 起 autostart 的项目（间隔 1s）。在后台线程跑，不挡 uvicorn。"""
        cfg = self.config()
        adopted = self.supervisor.adopt_saved(cfg.projects)
        snap = {d["id"]: d for d in self.supervisor.snapshot(cfg.projects)}
        for p in cfg.projects:
            if not p.autostart or p.error or p.id in adopted:
                continue
            if snap.get(p.id, {}).get("status") in ("running", "starting", "external"):
                continue
            try:
                self.supervisor.start(p)
            except ActionError:
                pass
            time.sleep(1.0)


def _run(fn, *args):
    try:
        fn(*args)
    except ActionError as e:
        raise HTTPException(e.status, str(e)) from e


def _open_with(argv: list[str], cwd: Path) -> None:
    subprocess.Popen(argv, cwd=str(cwd), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def create_app(config_path: Path, *, autostart: bool = True) -> FastAPI:
    state = PanelState(config_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if autostart:
            threading.Thread(target=state.boot, daemon=True).start()
        yield

    app = FastAPI(title="devpanel", version=__version__, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.panel = state

    # ----- 项目 -----

    @app.get("/api/projects")
    def list_projects():
        cfg = state.config()
        return {"projects": state.supervisor.snapshot(cfg.projects), "errors": cfg.errors}

    @app.post("/api/projects/start-all")
    def start_all():
        cfg = state.config()
        snap = {d["id"]: d for d in state.supervisor.snapshot(cfg.projects)}
        todo = [p for p in cfg.projects if snap[p.id]["status"] in ("stopped", "exited", "crashed")]

        def go():
            for i, p in enumerate(todo):
                if i:
                    time.sleep(1.0)
                try:
                    state.supervisor.start(p)
                except ActionError:
                    pass

        threading.Thread(target=go, daemon=True).start()
        return {"started": [p.id for p in todo]}

    @app.post("/api/projects/stop-all")
    def stop_all():
        cfg = state.config()
        snap = {d["id"]: d for d in state.supervisor.snapshot(cfg.projects)}
        stopped = []
        for p in cfg.projects:
            if snap[p.id]["status"] in ("running", "starting", "unhealthy", "external", "restarting"):
                try:
                    state.supervisor.stop(p)
                    stopped.append(p.id)
                except ActionError:
                    pass
        return {"stopped": stopped}

    @app.post("/api/projects/{project_id}/start")
    def start(project_id: str):
        p = state.project(project_id)
        _run(state.supervisor.start, p)
        return {"ok": True}

    @app.post("/api/projects/{project_id}/stop")
    def stop(project_id: str):
        p = state.project(project_id)
        _run(state.supervisor.stop, p)
        return {"ok": True}

    @app.post("/api/projects/{project_id}/restart")
    def restart(project_id: str):
        p = state.project(project_id)
        _run(state.supervisor.restart, p)
        return {"ok": True}

    # ----- 日志 -----

    @app.get("/api/projects/{project_id}/logs")
    def logs(project_id: str, lines: int = Query(200, ge=1, le=2000)):
        state.project(project_id)
        return {"lines": state.logs.get(project_id).tail(lines)}

    @app.get("/api/projects/{project_id}/logs/stream")
    async def logs_stream(project_id: str, request: Request):
        state.project(project_id)
        log = state.logs.get(project_id)
        replay, q = log.subscribe()

        async def gen():
            try:
                for line in replay:
                    yield _sse(line)
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        line = await asyncio.wait_for(q.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    if line is None:
                        break
                    yield _sse(line)
            finally:
                log.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ----- 打开 -----

    @app.post("/api/projects/{project_id}/open-folder")
    def open_folder(project_id: str):
        p = state.project(project_id)
        if not p.cwd.is_dir():
            raise HTTPException(404, f"目录不存在：{p.cwd}")
        os.startfile(str(p.cwd))  # type: ignore[attr-defined]
        return {"ok": True}

    @app.post("/api/projects/{project_id}/open-editor")
    def open_editor(project_id: str):
        p = state.project(project_id)
        code = shutil.which("code")
        if not code:
            raise HTTPException(404, "找不到 code 命令，VS Code 没装或没加进 PATH")
        _open_with([code, str(p.cwd)], p.cwd)
        return {"ok": True}

    @app.post("/api/projects/{project_id}/open-log-file")
    def open_log_file(project_id: str):
        state.project(project_id)
        path = state.logs.get(project_id).path
        if not path.is_file():
            raise HTTPException(404, "还没有日志文件")
        os.startfile(str(path))  # type: ignore[attr-defined]
        return {"ok": True}

    # ----- 配置 -----

    @app.get("/api/config")
    def get_config():
        return _config_dict(state.config())

    @app.post("/api/config/reload")
    def reload_config():
        return _config_dict(state.watcher.reload())

    # ----- 静态前端 -----

    if (DIST_DIR / "index.html").is_file():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = DIST_DIR / path
            if path and ".." not in path and candidate.is_file():
                return FileResponse(candidate)
            # index.html 不能让浏览器缓存，否则前端更新后还会引用旧的 assets
            return FileResponse(DIST_DIR / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        def no_frontend():
            return HTMLResponse(
                "<p style='font:15px system-ui;padding:40px'>前端还没构建："
                "在 <code>frontend/</code> 里执行 <code>npm install && npm run build</code>，"
                "产物会放到 <code>src/devpanel/web/dist/</code>。接口文档在 <a href='/api/docs'>/api/docs</a>。</p>"
            )

    return app


def _sse(line: str) -> str:
    # 一行里若有换行（不该有，防一手），拆成多个 data: 字段
    return "".join(f"data: {part}\n" for part in line.split("\n")) + "\n"


def _config_dict(cfg: Config) -> dict:
    return {
        "path": str(cfg.path),
        "panel": {"port": cfg.panel.port, "open_browser": cfg.panel.open_browser},
        "projects": [p.to_dict() for p in cfg.projects],
        "errors": cfg.errors,
    }
