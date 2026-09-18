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

> **v0.2 修订**：子进程的 stdout **不走管道**，直接以追加模式写 `logs/<id>.log`（Windows 上用 `FILE_APPEND_DATA` 句柄，
> OS 保证原子追加；`FILE_SHARE_DELETE` 让滚动在子进程握着句柄时也能改名）。面板 tail 这个文件喂环形缓冲和 SSE。
> 原因：管道的读端在面板里，面板一死管道断，Node 往里写会 EPIPE 崩掉——实测 2 秒内死。文件没有这个问题，
> 而且面板重启后认领回来的项目日志是连续的。代价：滚动只在项目启动前判断，一次运行里不滚。

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
    gitinfo.py              # v0.3：每个 cwd 的 git 分支 / 未提交 / 最近提交，后台缓存
    health.py               # v0.3：健康检查 URL 的 HTTP 探测
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

**v0.2 — 不用碰文件**（已做，见第 9 节）

**v0.3 — 更懂项目**（已做，见第 10 节）

**v0.4 — 优雅停止**
- 面板以「有控制台但隐藏」的方式启动，子进程共享控制台，`CTRL_BREAK` 先礼后兵
- 或者约定：项目提供 `stop_cmd`，面板先跑它再杀树

**不打算做**：远程机器、Docker、鉴权、通知。要这些就该换 Dockge / Coolify 那类东西了。

---

## 9. v0.2 设计：不用碰文件

v0.1 的所有输入都是手改 `projects.yaml`。v0.2 让日常操作——加一个项目、改个端口、删掉不用的、拖一下顺序——全部在页面上完成，`projects.yaml` 仍然是唯一事实来源，页面只是它的编辑器。

### 9.1 原则

- **yaml 还是唯一事实**。页面上的增删改都直接写回文件，不引入第二份状态。面板重启、手改文件、界面编辑，三者互不冲突。
- **写回要保留注释和顺序**。用户手写的注释（「地址带一次性 token」这种）是文档，不能被一次界面编辑冲掉。用 `ruamel.yaml` 的 round-trip 模式，改哪个键只动哪个键。
- **原子写**：先写 `projects.yaml.tmp` 再 `os.replace`，写到一半掉电不会留下半个文件。写完立即 `watcher.reload()`，不等下一次 mtime 检查。
- **校验分两档**。「硬错误」拒绝保存（id 格式不对、id 重复、缺 cwd / cmd）；「软错误」允许保存但卡片照 v0.1 标「配置错误」（cwd 不存在、命令没装、端口冲突、正则不合法）——用户可能先把项目记下来，回头再装依赖。
- **id 建了就不改**。id 是日志文件名、`pids.json` 的键、URL 的一部分；改 id 等于删了建一个。界面上编辑时 id 只读。

### 9.2 后端

新模块 `yamledit.py`：

```python
add_project(path, raw: dict) -> None       # 追加到 projects 末尾
update_project(path, id, raw: dict) -> None  # 原位改键；raw 里没有的键删掉（用户在表单里清空了）
delete_project(path, id) -> None
reorder(path, order: list[{id, group}]) -> None   # 按给的顺序重排，顺便改 group（拖到别的组）
```

所有写操作在一个进程级 `threading.Lock` 里做，每次都是「读文件 → 改 → 写回」，不缓存文档对象——手改和界面改交错也不会互相覆盖。

`raw` 的形状就是 yaml 里一个项目的映射：`{id, name, cwd, cmd, port, group, autostart, restart, url, url_pattern, env_file, env}`。写入前清理：空字符串 / `None` 的键不写；`autostart: false`、`restart: never` 这类默认值不写，保持文件干净。

新模块 `detect.py`：给一个目录，猜出项目名、id、启动命令、端口。

| 看到 | 猜 |
|---|---|
| `package.json` 有 `scripts.dev` | `npm run dev`（有 `pnpm-lock.yaml` → `pnpm dev`，`yarn.lock` → `yarn dev`） |
| `package.json` 只有 `scripts.start` | `npm start` |
| `pyproject.toml` 有 `[project.scripts]` | `uv run --no-sync <第一个脚本名>` |
| `pyproject.toml` 没脚本，但有 `main.py` / `app.py` / `server.py` | `uv run --no-sync python main.py` |
| 只有 `main.py` 之类，没 pyproject | `python main.py` |

端口：依次翻 `scripts` 里的 `--port N` / `-p N`、`vite.config.*` 里的 `port: N`、`.env` 里的 `PORT=N`、`main.py` 等入口文件里的 `port=N`；都没有就空着。名字取 `package.json.name` / `pyproject.project.name` / 目录名；id 是名字的 slug（非 `[a-z0-9]` 全变 `-`）。返回所有候选命令，让表单里一点就填。识别只是填表，猜错了改一下就是。

选目录：`POST /api/pick-folder` 起一个 `powershell -STA` 跑 `FolderBrowserDialog`，返回选中的路径。面板跑在用户桌面会话（任务计划，不是服务），对话框能弹出来；`TopMost` 的隐形父窗口保证它不会藏在浏览器后面。用户取消返回 `null`。手输路径也行。

接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects` | 新增，body 是 raw；硬错误 422 |
| PUT | `/api/projects/{id}` | 修改；运行中也允许，下次启动生效 |
| DELETE | `/api/projects/{id}` | 运行中 409；前端先停再删 |
| POST | `/api/projects/validate` | 干跑校验，返回 `{hard: [...], soft: [...]}`，表单实时用 |
| POST | `/api/projects/order` | `[{id, group}]`，重排 + 改组 |
| GET | `/api/detect?cwd=` | 目录识别 |
| POST | `/api/pick-folder` | 弹系统目录选择框 |

删除时不删日志文件（回头可能还想看），只把 supervisor 里的 Runtime 忘掉。

### 9.3 前端

- **顶栏「+ 新增」** 打开表单对话框；卡片 ⋯ 菜单加「编辑」「删除」。
- **表单**：基础字段（名称、id、目录、命令、端口、分组、开机自启、失败重启）常显，「高级」折叠里放 url、url_pattern、env_file、env（每行一个 `KEY=VALUE`）。目录框旁边「选择目录…」按钮；目录填好（选中或失焦）就调 `/api/detect`，把**还是空的**字段填上，识别到的候选命令做成小标签一点就换。
- **校验**：输入停 300ms 调 `/validate`，硬错误红字挡保存，软错误黄字提醒。
- **删除**：确认；在跑就问「先停止再删除？」。
- **分组折叠**：点组标题收起 / 展开，状态记 `localStorage`，收起时标题旁显示「3 个 · 在线 2」。
- **拖拽排序**：原生 HTML5 DnD，卡片抓手在 ⋯ 左边。拖到某张卡片上就插到它前面、组跟它；拖到组标题上就放到那组开头。松手调 `/order`，乐观更新，失败回滚。没有分组时全部算一组。
  - 一个坑：组标题和卡片必须在**同一个父元素**里（一张平铺的 grid，标题占整行）。如果每组一个 `<section>`，卡片跨组时 React 会把它卸载重建，`dragend` 落在已卸载的节点上，拖拽永远结束不了。

### 9.4 分组管理

分组原本只是项目身上的一个 `group:` 字符串——没法建空组、没法改名、顺序只能靠项目出现先后。加一个可选的顶层列表：

```yaml
groups: [常用, 监控, 开发]
```

它决定分组显示顺序，允许空组；项目里出现但没列的按首次出现补在后面（所以老文件不写也照常工作）。第一次做分组操作时如果没有这个键，按当时的隐式顺序建一个、插在 `projects:` 前面，写成一行流式。

操作都在组标题上：点名字折叠；⋯ 里「重命名」（原地变输入框，改成已有的名字就是合并）、「上移 / 下移」、「删除分组」（里面的项目去掉 `group`，归到「其他」）。grid 末尾一行「+ 新建分组」。「其他」是伪组，没有菜单。

接口：`POST /api/groups {name}`、`PUT /api/groups/{name} {name}`、`DELETE /api/groups/{name}`、`POST /api/groups/order [names]`。

### 9.5 不做

- 编辑 `panel:` 段（端口、open_browser）。改面板端口要重启面板，界面上改没意义。
- 多选批量。
- 撤销。yaml 在 git 里，要撤销 `git diff` 看。

---

## 10. v0.3 设计：更懂项目

v0.2 之后面板能管项目的启停和清单；v0.3 让它多知道一点每个项目「现在是什么样」：
代码在哪个分支、有没有没提交的改动、进程到底健康不健康、最近十分钟资源曲线长什么样。
四件事互相独立，都不改 v0.1/v0.2 的状态机和文件格式，只加字段。

### 10.1 git 信息

新模块 `gitinfo.py`。每个项目的 `cwd` 跑两条命令：

```
git status --porcelain=v2 --branch     → 分支 / detached / 未提交条数 / ahead-behind
git log -1 --format=%ct%x1f%s          → 最近一次提交时间 + 标题
```

- 环境里塞 `GIT_OPTIONAL_LOCKS=0`：`git status` 顺手刷新索引时会写 `index.lock`，
  用户正在终端里 `git commit` 会撞上「index.lock exists」。关掉可选锁就不会。
- `CREATE_NO_WINDOW`，超时 10s。`git` 没装、目录不是仓库（退出码 128）→ `null`，卡片上不显示这一行。
- 空仓库（还没有提交）`git log` 失败，`commit_at` 为空，其他照常。

`GitCache`：进程级缓存，一条后台线程按 30s 一轮刷新所有项目的 cwd。两个细节：

- **没人看就不跑**。每次 `/api/projects` 读缓存时记一下时间，超过 90s 没人读（页面关了、在后台）线程就空转，
  不在后台每半分钟敲一遍磁盘。页面回来第一次读会触发立即刷新。
- 新加的项目（缓存里没有的 cwd）下一个 tick 就刷，不用等 30s；删掉的项目缓存顺手丢掉。

快照里每个项目多一个 `git` 字段：

```json
{"branch": "main", "detached": false, "dirty": 3, "ahead": 1, "behind": 0,
 "commit_at": 1758160000, "commit_msg": "fix: …", "error": null}
```

卡片第四行：`⎇ main · ●3 · 2 小时前`。`●3` 用 warn 色表示 3 条未提交改动，干净时不显示；
`↑1 ↓2` 是 ahead / behind；detached 时显示短 sha 而不是分支名；hover 看提交标题。
不是仓库的卡片没有这一行——按钮行改成 `margin-top: auto` 贴底，同一行的卡片高度仍然对齐。

### 10.2 在终端打开

`POST /api/projects/{id}/open-terminal`：`wt -d <cwd>`（Windows Terminal），找不到 `wt` 退回
`cmd.exe`（`CREATE_NEW_CONSOLE`，cwd 设好）。⋯ 菜单里加一项，放在「打开目录」和「在 VS Code 打开」之间。
不传命令进去——终端是让人手动干活的，不是又一个启动入口。

### 10.3 健康检查 URL

```yaml
  - id: api
    port: 8000
    health: /api/health          # 相对端口；也可以写完整 http://… 地址
```

端口通不等于服务就绪：vite 在依赖预构建完之前端口就开了，uvicorn 的 lifespan 还没跑完也在 LISTEN。
配了 `health` 的项目，「在线」的判据从「端口通」换成「这个地址返回 2xx/3xx」：

| 进程活着 | 没配 health | 配了 health |
|---|---|---|
| running | 端口通 | 健康检查通过（且检查时间晚于本次启动） |
| starting | 端口不通，< 60s | 检查没过，< 60s |
| unhealthy | 端口不通，≥ 60s | 检查没过，≥ 60s，卡片上显示原因（`HTTP 503` / `连接被拒绝` / `超时`）|

新模块 `health.py`：`HealthChecker` 一条后台线程，2s 一轮，只查「有 `health` 且进程活着（或端口有人听）」的项目；
上次通过的 5s 查一次，没过的 2s 查一次，启动阶段状态翻绿快一点。检查用 `urllib`，超时 3s（Windows 上 loopback 的「连接被拒绝」要 SYN 重试约 2s 才报出来，再短就分不清是拒绝还是超时），
**显式关掉代理**（`ProxyHandler({})`——Windows 系统代理开着时 `urllib` 会把 127.0.0.1 也送去代理），
线程池并发 4 个，一个慢项目不拖住别人。结果不落盘，面板重启后从头查。

`health` 是相对路径但项目没写 `port` 是配置错误（软错误，允许保存）。快照里多 `health_result`：
`{ok, detail, checked_at, latency_ms}`，`external` 实例也查、也显示，但状态仍然是 `external`。

### 10.4 内存 / CPU 迷你折线

纯前端：`useHistory` 钩子把每次轮询拿到的 `rss` / `cpu` 按项目塞进一个 ring buffer（最近 10 分钟，2s 一个点，
最多 300 个），项目不在跑就把它的历史清掉。不落盘、不加接口——刷新页面就从头画，这是有意的：
它回答的是「刚才这几分钟发生了什么」，不是监控系统。

画法：`<Sparkline>` 一个 inline SVG，56×16，`preserveAspectRatio="none"`。内存按 min–max 缩放
（但最小量程是最大值的 5%，不然稳定在 400 MB 的项目会把 1 MB 的抖动画成山峰）；CPU 从 0 起算。
轮询暂停（页面在后台）造成的时间断档超过 6s 就断线不连。放在第三行数字旁边：`412 MB ▁▂▃▅ · 0.3% ▁▁▂▁`，
两个点以下不画。

### 10.5 不做

- git 操作（commit / pull / 切分支）。面板只看不动，要动去终端——所以有 10.2。
- 健康检查失败自动重启。「进程活着但不健康」大多是配置错、依赖没起，重启没用，标黄让人看到就行。
- 历史曲线落盘。
