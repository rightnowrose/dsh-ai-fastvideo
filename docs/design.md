# 设计说明（给改代码的人看）

## 三层结构

```
client.js   （浏览器）        React 组件，注册进 conversation.view 页签
   │  fetch  /ai-video/*
index.js    （Node / Host）   路由 + 任务队列 + 子进程 + 会话/文件读取
   │  spawn python
engine/python/videogen（Python）  稿件 → 页面 → 逐帧 → 混音 → mp4
```

为什么必须三层：浏览器不能起进程；Node 起不了 ffmpeg 的长任务管理（也没有 canvas）；
Python 有最好的文本/音频处理库，而且本项目的引擎是纯标准库 + edge-tts。

## 一次出片的数据流

1. 面板 POST `/ai-video/jobs`（`mode` = `prompt` | `auto` | `native`）。
2. Host `validatePayload()` 把「画幅 + 清晰度」换算成宽高，把时长、音量夹到安全范围；
   `auto` 模式还会在验证前抓上下文（对话记录 / 用户指定文件）。
3. job 落盘 `<dataDir>/jobs/<id>.json`，进入串行队列，spawn
   `python engine/python/tools/build_job.py <job>`。
4. 引擎的 stdout 是 JSONL：`progress` / `log` / `done` / `error`，Host 逐行转成 job 状态。
5. 产物写到 `<dataDir>/out/<YYYYMMDD>/`，面板通过 `/ai-video/media` 用 Range 请求播放。

## 两条渲染路线

| | 原生模板（native） | 风格 / 自拟（prompt / auto） |
|---|---|---|
| 画面来源 | 包内 `engine/python/videogen/templates/*` | LLM 现写的单文件 Canvas 页面 |
| 页面生命周期 | 参数化 JSON，稳定 | 缓存到 `work/prompt_pages/`，按 sha1 复用 |
| 时间控制 | 模板自己按节拍算 | 渲帧前注入 **timebox**（见下） |
| 速度 | 快（秒级到十几秒） | 慢（每屏十几秒写页面 + 逐帧渲染） |

## 渲染器（两套，同一套参数）

| 后端 | 文件 | 依赖 | 适用 |
|---|---|---|---|
| Node（默认） | `engine/node/render.mjs` | node ≥ 20，内置 `WebSocket` + `fetch` | Win/mac/Linux 通用，约 70ms/帧（1080p） |
| PowerShell（兜底） | `engine/python/videogen/tools/render-cdp.ps1` | Windows PowerShell 5.1 / pwsh | 老机器没有 node 时 |

两者 CLI 参数一一对应（`-Html/-Out/-Width/-Height/-Fps/-Duration/-Mode/-Format/-Timeout` ↔ `--html/--out/...`），
由 `videogen/paths.py` 的 `renderer_kind()` 决定（`config.renderer` → 环境变量 `VIDEOGEN_RENDERER` → auto：node 优先），
Python 侧只调 `render.render_page()`，不再各自拼命令。

Node 后端要点：`--headless=new` 起浏览器 → `/json/version` 拿 `webSocketDebuggerUrl` → 每帧
`Runtime.evaluate('window.seek(t)')` 后 `canvas.toDataURL()`（canvas 模式）或 `Page.captureScreenshot`（screenshot 模式）→
ffmpeg 编码；`finally` 里必杀浏览器进程并清临时 profile。canvas 模式只接受页面自带 `#cv`/`<canvas>`，
否则直接报错提示切 screenshot。

## 三个容易踩的点

### 1. GIF 不能直接给浏览器播放

逐帧截图要求「同一 t 必须同一帧」，而 GIF 的播放进度由浏览器时钟决定；另外 `file://` 图片画进
canvas 会污染画布，`toDataURL()` 会抛异常。所以 GIF 先被 ffmpeg 抽成固定帧率的 PNG 序列，
再以 data URI 内联进页面 —— 帧号由 `floor(t * fps) % n` 算，可复现，也不污染 canvas。

### 2. LLM 页面会循环 / 提前播完

模型写的页面常把 `window.DUR` 设得比旁白短，或者直接 `t % DUR`。渲帧前统一注入一层时间映射：

```
p = clamp(t / span, 0, 1)              # span = 这一屏旁白时长
pageT = min(pageDur * 0.9995, p * pageDur * ratio)
```

`ratio` 让短页面被拉长（上限 2.5 倍），`0.9995` 避开整点回跳，末端冻结。
这样整个动效在一句话里铺开一次，播完就停。

### 3. 截图模式会挂死

`Page.captureScreenshot` 在某些 Canvas 页面上会永久阻塞。所以默认走
`Runtime.evaluate(canvas.toDataURL())`（canvas + jpeg，实测 108ms/帧，是 screenshot 的 1/8），
并加三层超时：CDP 读超时 60s、单屏总超时 900s、Python 侧进程看门狗。超时即丢弃该屏缓存，
下一次重写页面，不会一直卡在同一个坏页面上。

## 配置项的解析顺序

`环境变量` > `<dataDir>/config.json` > `自动探测`（PATH / 常见安装位置）。

Host 在 spawn 引擎时把结果统一塞进 `VIDEOGEN_*` 环境变量，所以引擎本身不读 config.json ——
这样 CLI 单独跑（`python -m videogen.cli`）也能用同一套环境变量。
