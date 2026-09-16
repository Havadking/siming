"""弹系统的「选择文件夹」对话框。

面板跑在用户的桌面会话里（任务计划，不是服务），所以对话框弹得出来。用 PowerShell -STA 起
WinForms 的 FolderBrowserDialog；一个 TopMost 的隐形父窗口保证它不会藏在浏览器后面。
"""

from __future__ import annotations

import os
import subprocess

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Show()
$owner.Activate()
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = $env:DEVPANEL_PICK_TITLE
$d.ShowNewFolderButton = $false
try { $d.UseDescriptionForTitle = $true } catch {}
if ($env:DEVPANEL_PICK_INITIAL) { $d.SelectedPath = $env:DEVPANEL_PICK_INITIAL }
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($d.SelectedPath) }
$owner.Close()
"""


def pick_folder(initial: str | None = None, title: str = "选择项目目录", timeout: float = 600) -> str | None:
    """阻塞到用户选完。取消返回 None。"""
    env = {**os.environ, "DEVPANEL_PICK_TITLE": title, "DEVPANEL_PICK_INITIAL": initial or ""}
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
            capture_output=True, timeout=timeout, env=env, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("目录选择框等太久没人选，已关掉") from e
    except OSError as e:
        raise RuntimeError(f"弹不出目录选择框：{e}") from e
    if r.returncode != 0:
        raise RuntimeError(f"目录选择框出错：{r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    out = r.stdout.decode("utf-8", "replace").strip()
    return out or None


_FILE_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Show()
$owner.Activate()
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = $env:DEVPANEL_PICK_TITLE
$d.Filter = "HTML 文件 (*.html;*.htm)|*.html;*.htm|所有文件 (*.*)|*.*"
if ($env:DEVPANEL_PICK_INITIAL) { $d.InitialDirectory = $env:DEVPANEL_PICK_INITIAL }
if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($d.FileName) }
$owner.Close()
"""


def pick_html_file(initial: str | None = None, title: str = "选择本地 HTML 小工具", timeout: float = 600) -> str | None:
    """阻塞到用户选完。取消返回 None。"""
    env = {**os.environ, "DEVPANEL_PICK_TITLE": title, "DEVPANEL_PICK_INITIAL": initial or ""}
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass", "-Command", _FILE_SCRIPT],
            capture_output=True, timeout=timeout, env=env, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("文件选择框等太久没人选，已关掉") from e
    except OSError as e:
        raise RuntimeError(f"弹不出文件选择框：{e}") from e
    if r.returncode != 0:
        raise RuntimeError(f"文件选择框出错：{r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    out = r.stdout.decode("utf-8", "replace").strip()
    return out or None

