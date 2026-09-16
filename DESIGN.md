# 司命 devpanel — 设计文档

> 本地项目控制台：把机器上所有本地网页项目（npm 起的、uv 起的）放到一页里，
> 点一下启动/停止，一眼看到谁在线、跑了多久、占多少内存，点进日志，点链接直达。
>
> 第 1–7 节是 v0.1 要落地的东西，第 8 节是之后的路线。

---

## 1. 目标与边界

**要解决的事**

- 本机十几个网页项目，启动方式各异（`npm start` / `npm run dev` / `uv run xxx`），端口各异，散落在不同终端里。想要一个网页，收藏在浏览器里，所有项目从这一页进出。
- 对每个项目：**启动 / 停止 / 重启**，**当前状态**（在线 / 已停 / 启动中 / 崩溃），**运行时长、CPU、内存**，**最近日志**，**一键打开**（跳到它的 `localhost:端口`）。
- 开机后面板自己起来，标了 `autostart` 的项目跟着起来。

**明确不做**

- 不做部署、不做容器、不做远程机器。它只管**本机、本用户**的进程。
- 不对外开放。面板能执行 `projects.yaml` 里写的任意命令，等价于一个 shell——**只绑 `127.0.0.1`**，不要放到 Tailscale / 局域网上。
- 不做鉴权、不做多用户。这是单人工具。

**运行环境**：Windows 11，Python 用 uv 管理，Node 22 已装。所有依赖照旧落 `E:\personal\.cache`。

---

## 2. 关键决定：自己管进程，不用 PM2

最初的设想是「PM2 做守护，面板只是它的网页皮」。放弃它，理由：

1. **这台机器上 PM2 已经坏了**：`~/.pm2` 下是 2025 年 12 月留下的 nvm/v22.0.0 安装，现在任何 `pm2` 命令都在 `connect EPERM //./pipe/rpc.sock` 上死掉，每次调用还会多留一个连不上管道的僵尸 Daemon。修它的成本不比替代它低。
2. **Windows 上 PM2 拉 `npm` 一直不利索**：`npm` 是 `.cmd`，PM2 起的是 `cmd.exe → node` 两层，停止时经常只杀外层，端口继续被占着。
3. **我们要的守护功能其实很少**：拉起、整棵进程树杀掉、收日志、知道活没活、挂了拉一把。用 `psutil` 一百多行就够，没必要为此多背一个 Node 全局包 + 一个后台 Daemon。

所以：**面板自己就是 supervisor**。FastAPI 进程 spawn 子进程、记 pid、收 stdout、按需杀树。面板本身由 Windows 任务计划在登录时拉起。

一个附带的好处：项目不是面板起的也能管。用户在终端里手动 `npm start` 起的实例，面板按端口找到监听它的 pid，照样显示状态、照样能停（见 4.3）。

---

## 3. 整体架构

```
浏览器 http://127.0.0.1:9000
   │  轮询 /api/projects（2s）· SSE /api/projects/{id}/logs/stream
   ▼
┌─ devpanel（FastAPI + uvicorn，只绑 127.0.0.1）──────────────────────┐
│                                                                     │
│  config.py      projects.yaml → [Project]，按 mtime 热重载            │
│                                                                     │
│  supervisor.py  每个项目一个 Runtime：                                 │
│                   spawn（CREATE_NO_WINDOW，cwd，env，stdout→管道）      │
│                   status（pid 存活 + create_time 核对 + 端口探测）      │
│                   stop（psutil 杀整棵树，子进程先、父进程后）            │
│                   restart 策略（on-failure，指数退避，10 分钟内最多 5 次）│
│                   adopt（按端口找到外部起的实例，纳入管理）              │
│                                                                     │
│  logs.py        每项目：文件 logs/<id>.log（滚动）+ 内存环形缓冲 2000 行 │
│                   + 订阅者队列 → SSE                                  │
│                                                                     │
│  api.py         REST + SSE + 静态 dist                                │
│  cli.py         serve / install-startup / uninstall-startup          │
└─────────────────────────────────────────────────────────────────────┘
   │ spawn / kill
   ▼
 vsum (uv)   blog (vite)   api-x (uvicorn)   …
```

面板重启（比如改了它自己的代码）**不会**把项目一起带走：不用 Job Object，靠 `state/pids.json` 记 pid + create_time，重启后逐个核对、认领回来。

---

## 4. 后端设计

### 4.1 项目清单 `projects.yaml`

面板的唯一输入。v0.1 手改文件，面板监听 mtime 自动重载；v0.2 再做界面编辑。

```yaml
panel:
  port: 9000            # 面板自己的端口
  open_browser: true    # serve 时顺手开浏览器

projects:
  - id: vsum                        # 唯一，用于 URL 和日志文件名，[a-z0-9-]
    name: 拾光笺                     # 显示名
    cwd: E:/personal/projects/video-summarizer
    cmd: uv run --no-sync vsum ui --no-browser
    port: 7860                      # 用来探测「在线」和生成「打开」链接
    url: http://127.0.0.1:7860      # 可省，默认由 port 生成
    autostart: true                 # 面板启动时跟着起
    restart: on-failure             # on-failure | never
    env:                            # 追加到环境变量
      PYTHONUTF8: "1"
    group: 常用                     # 可省；有的话卡片按组分块

  - id: blog
    name: 博客
    cwd: E:/personal/projects/blog
    cmd: npm run dev
    port: 5173
```

**命令解析**：`cmd` 用 `shlex.split(posix=False)` 拆开，第一个词用 `shutil.which` 解析——Windows 下它能找到 `npm.cmd`、`uv.exe`，所以**不走 `shell=True`**，少一层 `cmd.exe`。找不到可执行文件的项目在界面上标「命令不存在」，不允许启动。

**校验**：`id` 唯一、`cwd` 存在、`port` 不互相冲突、不和面板自己冲突。校验失败不整体拒绝，单个项目标「配置错误」并显示原因，其他照常。

### 4.2 进程生命周期

```
        start()                      端口通了                 进程退出
stopped ───────► starting ─────────────────────► running ─────────────► exited(code)
   ▲                │ 60s 端口还没通                              │
   │                ▼                                            │ restart=on-failure
   │            unhealthy（进程活着但端口不开，黄色，                │ 且 code≠0
   │             可能是端口写错了）                                 ▼
   │                                                           backoff → starting
   └──────────── stop() 从任何状态 ──────────────────  10 分钟内第 6 次失败 → crashed（红，要手动）
```

**spawn**：`subprocess.Popen(argv, cwd=cwd, env=merged, stdout=PIPE, stderr=STDOUT, creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)`。一条线程读管道，写文件 + 塞环形缓冲 + 推给 SSE 订阅者。写 `state/pids.json`：`{id: {pid, create_time, started_at}}`。

**stop**：v0.1 **直接杀树**——`psutil.Process(pid).children(recursive=True)` 逐个 `terminate()`，等 3 秒，没退的 `kill()`，最后父进程。对开发服务器这就够了（uvicorn/vite 没有需要收尾的状态；vsum 的 sqlite 开着 WAL，中途被杀是安全的）。

为什么不先「优雅停止」：Windows 没有 SIGTERM，替代品 `GenerateConsoleCtrlEvent(CTRL_BREAK)` 要求子进程和面板共享一个控制台，而面板由任务计划隐藏启动时根本没有控制台。做不到就不假装做，放到 8.x 路线里。

**status**：每次 `/api/projects` 请求时现算，不缓存（十几个项目，psutil 查一轮 < 20ms）：

| 判据 | 状态 |
|---|---|
| pid 活着且 create_time 一致，端口通 | `running` 绿 |
| pid 活着，端口不通，启动 < 60s | `starting` 黄 |
| pid 活着，端口不通，启动 ≥ 60s | `unhealthy` 黄，提示「进程在但端口没开」 |
| pid 没了，退出码 0 或人为 stop | `stopped` 灰 |
| pid 没了，退出码 ≠ 0 | `exited` 红，显示退出码；正在退避重启则标 `restarting` |
| 重启超限 | `crashed` 红，按钮变「重新启动」清零计数 |
| 面板没起过它，但端口有人监听 | `external` 蓝，见 4.3 |

附带指标：uptime、CPU%（`cpu_percent(interval=None)`，两次采样之间的值）、内存（整棵树 RSS 之和）、重启次数、最近退出码。

### 4.3 认领外部实例

面板没起过某项目，但它的端口被监听着（用户在终端里手动起的、或者上次面板异常退出留下的）。`psutil.net_connections("tcp")` 里找 `LISTEN` 且 `laddr.port == 项目 port` 的 pid，就当作这个项目的进程：状态显示 `external`，「停止」按钮可用（杀那棵树），「日志」不可用（stdout 不在我们手里，提示「在终端里起的，日志看那边」）。停掉后再点「启动」就是面板自己起的了。

面板自己重启时也走同一套逻辑先认领 `pids.json` 里的，再用端口兜底。

### 4.4 日志

- 文件：`logs/<id>.log`，单文件 5MB 滚动保留 3 份。每次 start 写一行分隔 `===== 2026-09-16 14:22:31 start =====`，不清空——上次为什么挂了要能看到。
- 内存：每项目 `deque(maxlen=2000)`，`/logs?lines=N` 直接从这里取，不读文件。
- 实时：`/logs/stream` SSE，先发缓冲里最近 200 行，再持续推。前端关掉抽屉就断连接。
- 编码：子进程输出按 UTF-8 解，解不了的按 GBK 再试（Windows 上 npm 的报错偶尔是 GBK），再不行 `errors="replace"`。`env` 里默认塞 `PYTHONUTF8=1`、`FORCE_COLOR=0`，少收 ANSI 码；收到的 ANSI 序列后端剥掉再存。

### 4.5 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects` | 全部项目 + 状态 + 指标。前端 2s 轮询这一个接口 |
| POST | `/api/projects/{id}/start` | 已在跑则 409 |
| POST | `/api/projects/{id}/stop` | 杀树；`external` 也可 |
| POST | `/api/projects/{id}/restart` | stop 后 start |
| POST | `/api/projects/start-all` | 只起 `stopped/exited/crashed` 的，间隔 1s 错开 |
| POST | `/api/projects/stop-all` | |
| GET | `/api/projects/{id}/logs?lines=200` | |
| GET | `/api/projects/{id}/logs/stream` | SSE |
| POST | `/api/projects/{id}/open-folder` | `explorer <cwd>` |
| POST | `/api/projects/{id}/open-editor` | `code <cwd>`；找不到 `code` 就 404 |
| GET | `/api/config` | 当前生效的清单 + 校验错误，界面顶上显示「配置有 2 处错误」 |
| POST | `/api/config/reload` | 手动重载（正常靠 mtime 自动） |

动作接口都是同步的：start 返回时进程已 spawn（不等端口通），stop 返回时树已死。

### 4.6 面板自身的常驻

`devpanel install-startup`：用 `schtasks` 建一个「登录时」任务，运行 `<venv>\Scripts\devpanel.exe serve --no-browser`，窗口隐藏，工作目录是项目目录。`uninstall-startup` 删掉。不用 Windows 服务——服务跑在 Session 0，起的子进程没有桌面，`explorer` / `code` 都打不开。

serve 起来后：认领旧进程 → 起 `autostart` 的项目（间隔 1s）→ 开浏览器（如果 `open_browser`）。

`uv run` 的老问题（服务常驻时 `uv sync` 失败）在这里同样存在：改面板自己的依赖前先停面板；改**某个项目**的依赖前在面板里先停那个项目。这是 `--no-sync` 写进 `cmd` 的原因。

### 4.7 代码布局

```
devpanel/
  pyproject.toml            # fastapi, uvicorn[standard], psutil, pyyaml, click
  projects.yaml             # 本机清单（进 git，它本来就是本机专用的）
  DESIGN.md
  src/devpanel/
    __init__.py
    cli.py                  # serve / install-startup / uninstall-startup
    config.py               # 读、校验、热重载
    supervisor.py           # Runtime + 状态机 + 认领
    logs.py                 # 文件滚动 + 环形缓冲 + SSE 订阅
    api.py                  # 路由 + 静态文件
    web/dist/               # 前端构建产物，随包走，跑面板不需要 Node
  frontend/                 # React + Vite + Tailwind 4 + lucide（和 vsum 同一套）
  logs/                     # 运行时生成，.gitignore
  state/                    # pids.json，.gitignore
  tests/
```

---

## 5. 前端设计

和 vsum 用同一套栈和同一套设计 token（`index.css` 里的 `--bg / --surface / --ok / --warn / --bad …`，深色跟随系统 + 手动切换），看起来是一家的。只有**一个页面**，不需要路由。

### 5.1 页面结构

```
┌──────────────────────────────────────────────────────────────────────┐
│ 本地项目        6 个 · 在线 3 · 内存 1.2 GB      [全部启动] [全部停止] ☾ │
├──────────────────────────────────────────────────────────────────────┤
│ 常用                                                                  │
│ ┌────────────────────────┐ ┌────────────────────────┐ ┌────────────┐ │
│ │ ● 拾光笺        :7860 ↗│ │ ● 博客          :5173 ↗│ │ ○ api-x    │ │
│ │ 在线 · 2h 13m          │ │ 启动中 · 4s            │ │ 已停止     │ │
│ │ 412 MB · 0.3% · 重启 0 │ │ —                      │ │ 上次退出 1 │ │
│ │ [停止] [重启] [日志] ⋯ │ │ [停止]      [日志] ⋯   │ │ [启动] ⋯   │ │
│ └────────────────────────┘ └────────────────────────┘ └────────────┘ │
│ 其他                                                                  │
│ ┌────────────────────────┐ …                                         │
└──────────────────────────────────────────────────────────────────────┘
                                                    ┌── 日志：拾光笺 ──── ✕ ┐
                                                    │ 14:22:31 INFO …       │
                                                    │ …（自动滚到底，          │
                                                    │   手动上滚就停住）       │
                                                    │ [清空显示] [打开日志文件]│
                                                    └────────────────────────┘
```

**卡片**是核心单元，一个项目一张：

- 左上：状态点（绿 `running` / 黄 `starting` `unhealthy` / 灰 `stopped` / 红 `exited` `crashed` / 蓝 `external`）+ 显示名。右上：`:端口 ↗`，点了新标签页打开项目，仅 `running` 和 `external` 时可点。
- 第二行：状态文字 + 运行时长（`2h 13m`，`starting` 时显示已等待秒数）。
- 第三行：内存 · CPU · 重启次数。`exited` 显示「退出码 1 · 3 分钟前」；`crashed` 显示「10 分钟内失败 6 次，已停止自动重启」。
- 按钮行：主按钮随状态变——`stopped/exited/crashed` 是「启动」，其他是「停止」；「重启」只在 `running/unhealthy` 显示；「日志」打开抽屉；`⋯` 菜单：打开目录、在 VS Code 打开、复制命令。
- hover 卡片显示 `cwd` 和 `cmd`（小字，等宽）。
- `external` 卡片的「日志」按钮禁用，tooltip 说明原因。
- 配置错误的卡片整体半透明，状态处显示错误原因，只有 `⋯` 可用。

**分组**：有 `group` 的按组分块，组名做小标题；没有 `group` 的归到最后「其他」。只有一个组时不显示组标题。

**日志抽屉**：右侧滑出，宽 520px，等宽字体，最近 200 行 + SSE 实时追加。自动滚到底；用户往上滚了就停止自动滚，底部出现「回到最新」。关掉抽屉断开 SSE。

**顶栏**：项目数 / 在线数 / 总内存；「全部启动」「全部停止」（后者弹一次确认）；深色切换。配置校验有错时顶栏下方一条黄色横幅列出错误。

### 5.2 交互细节

- 状态 2s 轮询一次；点了按钮**立即**把该卡片置为过渡态（按钮变灰、文字「正在停止…」），下一次轮询拿到真实状态再覆盖，不等 2 秒。
- 动作失败（409、500）在卡片上显示一行红字，5 秒后消失，不用全局弹窗。
- 窗口宽度自适应：卡片 `grid` 最小 280px，一行几张由宽度定。手机上打开也能用，但不为它优化。
- 页面标题随状态变：`3/6 在线 · devpanel`，收藏栏一眼能看到。

### 5.3 前端文件

```
frontend/src/
  main.tsx
  App.tsx            # 顶栏 + 分组 + 卡片网格 + 日志抽屉
  api.ts             # fetch 封装 + 类型
  components/
    ProjectCard.tsx
    LogDrawer.tsx
    StatusDot.tsx
    ui.tsx           # Button / Menu，从 vsum 抄
  hooks/
    usePolling.ts
    useLogStream.ts  # EventSource 封装，带自动滚动逻辑
  lib/format.ts      # 时长、字节数
  index.css          # token 同 vsum
```

---

## 6. 用到 vsum 的地方

- `vsum ui` 现在默认开浏览器，面板起它时要 `--no-browser`；如果这个参数还没有，加一个（cli 里是 `no_browser` 选项，已有）。
- vsum 绑的是 `127.0.0.1:7860`，面板探测端口用同一个地址，不冲突。
- 上一轮讨论的「让父母朋友用」（Tailscale + 鉴权）和本面板无关：面板只在本机看，vsum 自己去对外。

---

## 7. v0.1 验收

- [ ] `uv run devpanel serve` 起来，浏览器打开 `127.0.0.1:9000` 看到清单里的项目
- [ ] 一个 `uv` 项目和一个 `npm` 项目都能：启动 → 状态变绿 → 「打开」能进 → 停止 → 端口释放（`netstat` 查不到）→ 卡片变灰
- [ ] 手动在终端 `npm start` 起一个项目，面板显示 `external`，能停掉
- [ ] 把某项目 `port` 改错，状态一分钟后变 `unhealthy` 并提示
- [ ] 项目进程自己崩（`exit 1`），面板退避重启，六次后 `crashed`
- [ ] 面板自己 `Ctrl+C` 再起，项目不受影响，状态全部认领回来
- [ ] `install-startup` 后注销重登，面板和 `autostart` 项目自己起来，没有任何窗口弹出
- [ ] 改 `projects.yaml` 加一个项目，不重启面板，2 秒内卡片出现
- [ ] 日志抽屉实时滚，中文不乱码

---

## 8. 之后的路线

**v0.2 — 不用碰文件**
- 界面上新增 / 编辑 / 删除项目，直接写回 `projects.yaml`（保留注释——用 `ruamel.yaml`）
- 「从目录识别」：选一个目录，看到 `package.json` 就填 `npm run dev`，看到 `pyproject.toml` 就猜 `uv run`，端口从配置里猜
- 卡片拖拽排序、分组折叠

**v0.3 — 更懂项目**
- 每张卡片显示 git 分支、是否有未提交改动、最近一次 commit 时间（`git` 子进程，30s 刷一次）
- 「在终端打开」：`wt -d <cwd>`
- 健康检查 URL（`health: /api/health`）替代纯端口探测
- 内存 / CPU 迷你折线（最近 10 分钟，前端 ring buffer，不落盘）

**v0.4 — 优雅停止**
- 面板以「有控制台但隐藏」的方式启动，子进程共享控制台，`CTRL_BREAK` 先礼后兵
- 或者约定：项目提供 `stop_cmd`，面板先跑它再杀树

**不打算做**：远程机器、Docker、鉴权、通知。要这些就该换 Dockge / Coolify 那类东西了。
