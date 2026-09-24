# 司命 · siming

![License](https://img.shields.io/badge/License-MIT-green.svg)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)

> 司命星君掌生死簿。这个面板掌的是本机上一堆本地网页项目的生死：起、停、重启、活了多久、吃了多少内存、刚才为什么挂。

机器上十几个本地网页项目——`npm run dev` 起的、`uv run` 起的——端口各异、散落在不同终端里。司命把它们放到**一页**里：浏览器收藏一个 `127.0.0.1:9000`，所有项目从这里进出。

```
┌──────────────────────────────────────────────────────────────────┐
│ 司命    3 个 · 在线 3 · 内存 1.9 GB           [全部启动] [全部停止] ☾ │
├──────────────────────────────────────────────────────────────────┤
│ 常用                                                              │
│ ┌────────────────────────┐                                        │
│ │ ● 拾光笺        :7860 ↗│                                        │
│ │ 在线 · 2h 13m          │                                        │
│ │ 1.8 GB · 0% · 重启 0   │                                        │
│ │ [停止] [重启] [日志] ⋯ │                                        │
│ └────────────────────────┘                                        │
│ 监控                                                              │
│ ┌────────────────────────┐ ┌────────────────────────┐             │
│ │ ● 谛听         :17777 ↗│ │ ● 门神          :8080 ↗│             │
│ │ 在线 · 41m             │ │ 在线 · 12m             │             │
│ │ 41 MB · 0%             │ │ 166 MB · 0.2%          │             │
│ │ [停止] [重启] [日志] ⋯ │ │ [停止] [重启] [日志] ⋯ │             │
│ └────────────────────────┘ └────────────────────────┘             │
└──────────────────────────────────────────────────────────────────┘
```

## 它做什么

- **一张卡一个项目**：状态点（在线 / 启动中 / 已停 / 异常退出 / 崩溃 / 外部实例）、运行时长、内存（整棵进程树）、CPU、重启次数。点端口号直达项目页面。
- **启动 / 停止 / 重启**：停止是杀整棵进程树，`npm` 起的 `cmd.exe → node` 两层也一起带走，端口不会被占着。
- **日志抽屉**：每个项目的 stdout/stderr 落滚动文件（5 MB × 3，启动时判断），页面里实时追加（SSE），自动滚到底，往上翻就停住。UTF-8 解不开退 GBK，ANSI 色码剥掉。
- **挂了拉一把**：`restart: on-failure` 的项目非零退出后指数退避重启，10 分钟内失败 6 次就停手标红，等你来看。
- **认领外部实例**：你在终端里手动 `npm start` 起的项目，面板按端口找到它，照样显示、照样能停。
- **面板重启不带走项目**：pid + create_time 记在 `state/pids.json`，面板改完代码重启，项目一个不掉，状态全部认领回来。顶栏 ↻ 一键自我重启，几秒后页面自动恢复。子进程的 stdout 直接写日志文件而不是管道，所以面板死了它们也不会因为 EPIPE 跟着崩。
- **开机自启**：一条命令注册登录时任务计划，面板和标了 `autostart` 的项目跟着起，没有任何窗口弹出。
- **配置即文件**：`projects.yaml` 改完保存即生效，不用重启面板。
- **不用碰文件也行**（v0.2）：页面上「新增 / 编辑 / 删除」直接写回 `projects.yaml`，注释和顺序原样保留。选一个目录，看到 `package.json` / `pyproject.toml` 自动填好命令、端口、名字。卡片可以拖着排序、拖到别的组；组标题点一下折叠，⋯ 里改名 / 上下移 / 删除，底部「新建分组」。
- **更懂项目**（v0.3）：卡片上多一行 git——分支、几处未提交、领先/落后几个提交、最近一次提交是多久前（只看不动，30s 刷一轮，页面没开就不跑）。内存和 CPU 数字旁边各一条最近 10 分钟的迷你折线。⋯ 里「在终端打开」（`wt -d <cwd>`）。配 `health: /api/health` 的项目，「在线」看这个地址返回 2xx 而不是端口有没有人听——vite 在预构建完之前端口就开了，uvicorn 的 lifespan 没跑完也在 LISTEN，端口通不等于能用。
- **合并云端改动**：用 Claude 云端会话写的代码推到了 GitHub 的 `claude/*` 分支，本地落后。面板每 10 分钟 `git fetch` 一次，卡片 git 那行出现 `☁ N` 就是有 N 个提交还没合进来；点开勾选、一键合并（能快进就快进，否则生成合并提交），可选合并后推送、合并后重启项目。冲突自动撤销不留半截，列出冲突文件交给你处理；不要的旧分支点「忽略」。

## 它不做什么

不做部署、不做容器、不做远程机器、不做鉴权。它只管**本机、本用户**的进程，只绑 `127.0.0.1`。面板能执行清单里的任意命令，等价于一个 shell——**不要放到局域网上**。

## 安装

需要 Windows 10/11、[uv](https://docs.astral.sh/uv/)。跑面板不需要 Node（前端构建产物随包走）。

```bash
git clone https://github.com/Havadking/siming.git
cd siming
uv sync
uv run devpanel serve
```

浏览器会自动打开 `http://127.0.0.1:9000`。

## 配置 `projects.yaml`

```yaml
panel:
  port: 9000            # 面板自己的端口
  open_browser: true    # serve 时顺手开浏览器

groups: [常用, 监控]     # 可省。分组的显示顺序；允许空组；项目里出现但没列的自动补在后面

projects:
  - id: vsum                        # 唯一，[a-z0-9-]，用于 URL 和日志文件名
    name: 拾光笺                     # 显示名
    cwd: E:/personal/projects/video-summarizer
    cmd: uv run --no-sync vsum ui --no-browser
    port: 7860                      # 探测「在线」+ 生成「打开」链接
    health: /api/health             # 可省。配了就用「这个地址返回 2xx/3xx」判在线，不看端口；相对端口的路径或完整 URL
    autostart: true                 # 面板启动时跟着起
    restart: on-failure             # on-failure | never
    group: 常用                     # 可省；有则按组分块

  - id: menshen
    name: 门神
    cwd: E:/personal/projects/miwifi
    cmd: npm start
    port: 8080
    env_file: .env                  # KEY=VALUE 文件，spawn 时读进环境变量；放密码用
    env:                            # 直接写的环境变量（别放密码，这文件在 git 里）
      LOG_LEVEL: debug

  - id: dsh
    name: DeepSeek Harness
    cwd: E:/personal/projects/DeepSeek Harness
    cmd: npx -y @deepseek-ai/dsh web --no-open --port 3080
    port: 3080
    url_pattern: 'dsh web: (http\S+)'   # 地址带一次性 token，直接开 :3080 是 401；
                                        # 正则在 stdout 里匹配，捕获组 1 当「打开」链接
```

几条约定：

- `cmd` 用 `shlex` 拆开，第一个词按 PATH 解析（Windows 上能找到 `npm.cmd`、`uv.exe`），**不走 `shell=True`**。找不到的命令在卡片上标「命令不存在」。
- 项目里写 `python` 解析到的是**系统** Python，不是面板自己的 venv——面板会把自己的 `.venv/Scripts` 从 PATH 里洗掉，也不传 `VIRTUAL_ENV`。
- 校验失败（`cwd` 不存在、端口冲突、`env_file` 缺失……）只影响那一个项目，卡片半透明并显示原因，其他照常。
- `url_pattern`：有些服务的地址每次启动都变（随机端口、一次性 token），写一个带捕获组的正则，面板从它的 stdout 里捞出来当「打开」链接，面板重启后也记得。
- `health`：端口通不等于服务就绪。写了它，进程活着但检查没过就一直是「启动中」（60s 后变黄「健康检查没过：HTTP 503」），卡片上会显示原因和响应耗时。检查 2s 一轮、超时 3s、不走系统代理。
- `npx` 项目加 `-y`：包没缓存时 npx 会问「要装吗」，面板给子进程的 stdin 是空的，会卡死在那里。
- `uv` 项目请写 `--no-sync`：项目常驻时 `uv sync` 会失败，要改依赖先在面板里停掉它。

## 面板自己的启停

```bash
uv run devpanel serve                # 前台起，Ctrl+C 停
uv run devpanel restart              # 重启面板本身（改了面板代码之后用）；项目不受影响，重启后认领回来
                                     # 页面顶栏的 ↻ 按钮做的是同一件事：POST /api/panel/restart
uv run devpanel stop                 # 停掉面板；项目继续跑
uv run devpanel install-startup      # 注册「登录时」任务计划，后台无窗口起
uv run devpanel uninstall-startup    # 删掉
```

用任务计划而不是 Windows 服务：服务跑在 Session 0，起的子进程没有桌面，「打开目录」「在 VS Code 打开」都打不开。任务用 `pythonw` 起，没有窗口；XML 里关掉了 72 小时执行上限和电池条件。

**别用 `schtasks /End` 停面板**：任务计划把面板放在一个 Job Object 里，`/End` 会把 Job 里的进程全杀。面板 spawn 子项目时已经用 `CREATE_BREAKAWAY_FROM_JOB` 让它们脱离了 Job，所以 `devpanel restart` / `stop` 只动面板这一个进程；但 `/End` 仍然是错误的工具。

面板常驻时 `uv run` 会因为 `devpanel.exe` 被占用而重装失败，改面板代码后用 `uv run --no-sync devpanel restart`，改面板**依赖**要先 `devpanel stop`。

## 状态一览

| 状态 | 判据 |
|---|---|
| 在线 `running` | 进程活着，端口通；配了 `health` 则是健康检查通过 |
| 启动中 `starting` | 进程活着，端口没通（或健康检查没过），< 60s |
| 端口没开 `unhealthy` | 进程活着，端口 60s 还没通——多半是 `port` 写错了；配了 `health` 则显示「健康检查没过：HTTP 503」之类 |
| 已停止 `stopped` | 退出码 0，或你点的停止 |
| 异常退出 `exited` | 退出码 ≠ 0，`restart: never` |
| 等待重启 `restarting` | 非零退出，正在退避 |
| 已崩溃 `crashed` | 10 分钟内失败 6 次，停止自动重启；点「重新启动」清零 |
| 外部实例 `external` | 面板没起过它，但端口有人监听 |
| 配置错误 `error` | 见卡片上的原因 |

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/projects` | 全部项目 + 状态 + 指标（前端 2s 轮询这一个） |
| POST | `/api/projects/{id}/start` · `stop` · `restart` | 同步：start 返回时已 spawn，stop 返回时树已死 |
| POST | `/api/projects/start-all` · `stop-all` | |
| GET | `/api/projects/{id}/logs?lines=200` | 内存环形缓冲里取 |
| GET | `/api/projects/{id}/logs/stream` | SSE，先回放 200 行再持续推 |
| POST | `/api/projects/{id}/open-folder` · `open-terminal` · `open-editor` · `open-log-file` | 终端是 `wt -d <cwd>`，没装 Windows Terminal 退回 `cmd` |
| POST | `/api/projects` · PUT / DELETE `/api/projects/{id}` | 写回 `projects.yaml`；删除时在跑的返回 409 |
| POST | `/api/projects/validate` | 表单干跑校验，`{hard, soft}`：硬错误挡保存，软错误只是卡片标红 |
| POST | `/api/projects/order` | `[{id, group}]`，拖拽后重排 / 换组 |
| POST | `/api/groups` · PUT / DELETE `/api/groups/{name}` · POST `/api/groups/order` | 分组：新建、改名（改成已有的名字 = 合并）、删除（项目归到「其他」）、排序 |
| GET | `/api/detect?cwd=` | 从目录猜名字、id、命令、端口 |
| POST | `/api/pick-folder` | 弹系统「选择文件夹」对话框 |
| GET | `/api/panel` · POST `/api/panel/restart` | 面板自己的 pid / 版本；一键自我重启（拉起脱离的助手进程来杀自己再重起） |
| GET | `/api/config` · POST `/api/config/reload` | |

接口文档在 `/api/docs`。

## 开发

```
src/devpanel/
  config.py       projects.yaml → [Project]，校验，按 mtime 热重载
  yamledit.py     界面增删改写回 projects.yaml（ruamel round-trip，保注释）
  detect.py       从目录猜命令 / 端口；pick.py 弹系统选目录框
  supervisor.py   Runtime + 状态机 + 杀树 + 退避重启 + 认领
  logs.py         滚动文件 + 环形缓冲 + SSE 订阅
  gitinfo.py      每个 cwd 的分支 / 未提交 / 最近提交，后台 30s 刷一轮
  health.py       健康检查 URL 的 HTTP 探测，后台 2s 一轮
  api.py          FastAPI 路由 + 静态前端
  cli.py          serve / restart / stop / install-startup / uninstall-startup
  web/dist/       前端构建产物，随包走
frontend/         React + Vite + Tailwind 4 + lucide
tests/
```

```bash
uv run pytest                 # 后端测试（会真的 spawn / kill 子进程）
cd frontend && npm install
npm run dev                   # 5174，/api 代理到 9000
npm run build                 # 产物进 src/devpanel/web/dist
```

设计取舍（为什么不用 PM2、为什么直接杀树不先优雅停止、为什么不用 Job Object）见 [DESIGN.md](DESIGN.md)。

## 之后

- 优雅停止（`CTRL_BREAK` 先礼后兵，或约定 `stop_cmd`）

## License

MIT
