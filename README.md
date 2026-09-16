# devpanel

本地项目控制台：把机器上所有本地网页项目放到一页里，点一下启停，看状态、日志、内存。设计见 [DESIGN.md](DESIGN.md)。

## 用

```bash
uv sync
uv run devpanel serve          # http://127.0.0.1:9000，顺手开浏览器
```

项目清单在 [projects.yaml](projects.yaml)，改完保存即生效（按 mtime 热重载），不用重启面板。

密码之类不进 `projects.yaml`（它在 git 里）。给项目写 `env_file: .env`，在项目目录里建一个 `KEY=VALUE` 的 `.env`（确认它被那个项目的 `.gitignore` 忽略），面板 spawn 时读进环境变量。

开机自启（登录时任务计划，隐藏窗口）：

```bash
uv run devpanel install-startup
uv run devpanel uninstall-startup
```

## 改前端

```bash
cd frontend
npm install
npm run dev      # 5174，/api 代理到 9000
npm run build    # 产物进 src/devpanel/web/dist，随包走
```

## 测试

```bash
uv run pytest
```

## 注意

- 面板只绑 `127.0.0.1`。它能执行清单里的任意命令，别放到局域网上。
- 面板由 `uv run` 起，改**面板自己**的依赖前先停面板；改**某个项目**的依赖前在面板里先停那个项目（所以 `cmd` 里写 `uv run --no-sync`）。
- 项目里写 `python` 解析到的是系统 Python，不是面板的 venv（面板会把自己的 `.venv/Scripts` 从 PATH 里洗掉，也不传 `VIRTUAL_ENV`）。
- 停止是直接杀整棵进程树（Windows 没有 SIGTERM），对开发服务器够用。
- `logs/` 和 `state/` 是运行时生成的，不进 git。
