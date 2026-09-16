"""devpanel serve / install-startup / uninstall-startup"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

import click

from . import __version__

TASK_NAME = "devpanel"
DEFAULT_CONFIG = "projects.yaml"


def _find_config(path: str | None) -> Path:
    if path:
        return Path(path).resolve()
    # 没指定就从 cwd 往上找 projects.yaml，找不到退回包所在项目根（开发时的布局）
    for d in [Path.cwd(), *Path.cwd().parents]:
        if (d / DEFAULT_CONFIG).is_file():
            return (d / DEFAULT_CONFIG).resolve()
    root = Path(__file__).resolve().parents[2]
    return (root / DEFAULT_CONFIG).resolve()


@click.group()
@click.version_option(__version__)
def main() -> None:
    """本地项目控制台。"""


@main.command()
@click.option("--config", "config_path", type=click.Path(), default=None, help="projects.yaml 路径，默认在 cwd 往上找")
@click.option("--port", type=int, default=None, help="覆盖 panel.port")
@click.option("--no-browser", is_flag=True, help="不自动打开浏览器")
@click.option("--no-autostart", is_flag=True, help="不起 autostart 项目（调试面板用）")
def serve(config_path: str | None, port: int | None, no_browser: bool, no_autostart: bool) -> None:
    """起面板。只绑 127.0.0.1。"""
    cfg_path = _find_config(config_path)
    _redirect_stdio_if_headless(cfg_path.parent / "logs")

    from .api import create_app
    from .config import load

    cfg = load(cfg_path)
    for err in cfg.errors:
        click.echo(f"[配置] {err}", err=True)
    listen_port = port or cfg.panel.port
    app = create_app(cfg_path, autostart=not no_autostart)
    click.echo(f"devpanel {__version__} · 配置 {cfg_path} · http://127.0.0.1:{listen_port}")

    if not no_browser and cfg.panel.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{listen_port}")).start()

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=listen_port, log_level="warning", access_log=False)


def _redirect_stdio_if_headless(log_dir: Path) -> None:
    """pythonw / 任务计划隐藏启动时没有控制台，stdout 是 None，logging 一写就炸。落到 logs/devpanel.log。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = open(log_dir / "devpanel.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = fh
    if sys.stderr is None:
        sys.stderr = fh


def _pythonw() -> Path:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return w if w.is_file() else exe


def _task_xml(cfg_path: Path) -> str:
    """任务计划的 XML。用 XML 而不是 schtasks 参数，因为要关掉 72 小时执行上限和电池条件。"""
    cmd = str(_pythonw())
    args = f'-m devpanel serve --no-browser --config "{cfg_path}"'
    user = f"{os.environ.get('USERDOMAIN', '')}\\{getpass.getuser()}"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>devpanel 本地项目控制台，登录时自动启动</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>PT10S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{cmd}</Command>
      <Arguments>{args.replace('&', '&amp;').replace('"', '&quot;')}</Arguments>
      <WorkingDirectory>{cfg_path.parent}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


@main.command("install-startup")
@click.option("--config", "config_path", type=click.Path(), default=None, help="projects.yaml 路径")
def install_startup(config_path: str | None) -> None:
    """建一个「登录时」任务计划，隐藏窗口起面板。"""
    if sys.platform != "win32":
        raise click.ClickException("只支持 Windows")
    cfg_path = _find_config(config_path)
    xml = _task_xml(cfg_path)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-16", delete=False) as f:
        f.write(xml)
        tmp = f.name
    try:
        r = subprocess.run(["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/XML", tmp],
                           capture_output=True, text=True, encoding="gbk", errors="replace")
    finally:
        os.unlink(tmp)
    if r.returncode != 0:
        raise click.ClickException(f"schtasks 失败：{r.stderr.strip() or r.stdout.strip()}")
    click.echo(f"已注册任务计划 {TASK_NAME}：{_pythonw()} -m devpanel serve --no-browser --config {cfg_path}")
    click.echo("下次登录自动起。现在就起：schtasks /Run /TN devpanel")


@main.command("uninstall-startup")
def uninstall_startup() -> None:
    """删掉登录时任务计划。"""
    r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
                       capture_output=True, text=True, encoding="gbk", errors="replace")
    if r.returncode != 0:
        raise click.ClickException(f"schtasks 失败：{r.stderr.strip() or r.stdout.strip()}")
    click.echo(f"已删除任务计划 {TASK_NAME}")


if __name__ == "__main__":
    main()
