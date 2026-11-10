# videogen · 机器人自己剪竖屏短视频

一句话：**给一句台词，十几秒后拿到一条 1080×1920 的成片**（有配音、有 BGM、有字幕节奏、带「AI 生成」水印），
在 QQ 里直接说一句就行，不用开任何剪辑软件。

这条链路不依赖任何视频大模型：画面是**网页**（Canvas 2D，时间纯函数），
用 Edge 的 DevTools 协议逐帧截，再用 ffmpeg 配上 Edge TTS 的配音和闪避过 BGM 后混流。
所以显存占用 0、单条成本 0、风格可无限加。

```
一句话
  → 拆词 / 定节拍 (spec.py)
  → 套模板生成单文件 HTML (render.py + templates/)
  → Edge headless + CDP 逐帧截图，33ms/帧 (tools/render-cdp.ps1)
  → ffmpeg 编码 H.264 (yuv420p, +faststart)
  → 逐句 edge-tts → 裁静音 → 按节拍 adelay 摆位 (audio.py)
  → BGM 侧链闪避 + loudnorm -14 LUFS → 混流
  → data/video_out/<日期>/xxx.mp4
```

---

## 1. 命令行

用 AstrBot 的 venv（它带 3.12，本模块纯标准库，系统 3.10 也能跑）：

```powershell
cd F:\Deepseek-Chat\apps
$py = 'F:\Deepseek-Chat\apps\AstrBot\.venv\Scripts\python.exe'

& $py -m videogen.cli doctor                  # 自检：ffmpeg / Edge / voice / BGM / 数据库
& $py -m videogen.cli styles                  # 现有样式与默认节奏
& $py -m videogen.cli quick --words "你|懂什么|听话"      # 最快出一条
& $py -m videogen.cli quick --words "你|懂什么|听话" --no-audio
& $py -m videogen.cli build spec.json --out out.mp4      # 从 JSON 精确控制
& $py -m videogen.cli enqueue --words "..." --umo "default:FriendMessage:10001"
& $py -m videogen.cli worker               # 独立跑队列（不装 AstrBot 也能用）
& $py -m videogen.cli list                 # 最近任务
& $py -m videogen.cli show 12              # 单条详情
& $py -m videogen.cli preview --words "你|懂什么"   # 只生成 HTML，浏览器里调样式
```

`quick` 常用参数：`--style a3|title|quote`、`--words "词|词|词"`、`--title`、`--subtitle`、
`--no-audio`、`--voice`、`--rate`、`--pitch=`（注意等号，`--pitch '-2Hz'` 会被 argparse 当选项）、
`--bgm`、`--no-bgm`、`--beat 0.55`、`--width/--height/--fps`、`--out`。

`--voice` 既可以给 Edge TTS 的名字（`zh-CN-XiaoxiaoNeural`），也可以给自训练音色的 key
（`apps/voice/config.json` → `gsv_voices` 的键，例如 `mingqiannailv`）。给自训练音色时
`pipeline.build()` 会先调 `audio.prepare_voice()`：GPT-SoVITS 没在 9880 上就自动拉
`apps/gptsovits/start-gsv.ps1` 并等最多 300 秒；起不来会退回 `zh-CN-XiaoxiaoNeural`
并把原因写进 `warnings`（不静默降级）。首句合成含模型加载，通常 30–60 秒，之后每句 1–4 秒。
例：`python -m videogen.cli quick --words "你|懂什么|听话" --voice mingqiannailv`。

## 2. 在 QQ 里用

插件在 `apps/AstrBot/data/plugins/video_gen/`，重载插件后自动生效。

- **自然语言**：用户说「给我做个视频，写 你|懂什么|听话」→ 模型调 `make_video(words, style, subtitle)`
  → 工具立刻回一句给人设，worker 在后台出片，好了之后**主动发一条 `[文字 + 视频]`**。
- **手动命令**：`/视频 你|懂什么|听话`、`/视频 大字 你|懂什么`、`/视频 标题 周末别找我`。

配置 `apps/AstrBot/data/plugins/video_gen/config.json`（改完重载插件或等 mtime 自动重读）：

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关 |
| `default_style` | `"a3"` | 没指定样式时用哪个 |
| `max_per_hour` | `3` | 每人每小时上限（按 user_id 计数） |
| `max_words` | `8` | 一句话最多拆几个词 |
| `voice` | `"zh-CN-YunxiNeural"` | 音色：Edge TTS 名（`edge-tts --list-voices` 查）或 `apps/voice/config.json` 里 `gsv_voices` 的 key（如 `mingqiannailv`，走本机 GPT-SoVITS 9880） |
| `bgm_enabled` | `true` | 是否配 BGM |
| `notify_errors` | `true` | 失败是否回一句 |

任务落库在 `data/videogen_jobs.db`（SQLite，WAL），状态 `pending → running → done/failed`。
插件启动时会把上次卡在 `running` 超过 30 分钟的任务放回队列，所以杀进程不丢单。

## 3. 规格 JSON（`build` / 队列都用它）

```json
{
  "style": "title",
  "width": 1080, "height": 1920, "fps": 24,
  "title": "周末别找我",
  "subtitle": "我在补觉",
  "lines": ["周五晚十点前睡", "周六全天失联"],
  "seed": 7,
  "voice": {"enabled": true, "name": "zh-CN-YunxiNeural", "rate": "+10%", "pitch": "-2Hz"},
  "bgm": {"enabled": true, "path": "F:\\Deepseek-Chat\\data\\songs\\stems\\accompaniment.wav", "volume": 0.16},
  "accent": "#ff4a32",
  "bg": "#08080a",
  "footer": "AI 生成 · Deepseek-Chat"
}
```

- `lines` 可以直接给字符串数组，也可以给 `{"text": "...", "at": 0.55, "color": "#fff"}` 精确控时间；
  给了 `at` 就按你给的走，没给就按样式节奏推。
- `voice` / `bgm` 也可以写成 `true/false`（简写）。
- `duration` 留空就自动算（如 a3：`n*beat + 1.4`）。

## 4. 四种样式

| key | 长相 | 默认节奏 | 适合 |
|---|---|---|---|
| `a3` 大字冲击 | 词一个个砸进画面，旧的变灰，最新的白/红放大 | `beat 0.55s`，尾部 1.4s | 狠话、吐槽、金句爆发 |
| `title` 标题卡 | 渐变+斜线背景、左侧红竖条、大标题缓出、副标题、下划线、逐条 bullet | 首条 0.7s，间隔 0.6s | 通知、公告、清单 |
| `quote` 竖排金句 | 中文竖排逐字淡入、噪点背景、左下红章「AI 生成」 | 首条 0.9s，间隔 0.62s | 文艺感慨、签名档 |
| `explain` 讲解片 | 一屏一个知识点：小标签 + 大标题 + 说明 + 表情包 + 底部进度条 | 每屏 = 该句配音时长 + 1.05s | 功能导览、教程、汇报 |

`explain` 的 `lines` 每项是一「屏」，比别的样式多几个字段：

```json
{
  "label": "① 入口层",            // 左上角小标签
  "text": "消息从哪进来",          // 大标题（支持 \n 强制断行）
  "sub": "QQ > NTQQ > SnowLuma > AstrBot",  // 说明文字；含两个以上 > 自动画成流程链
  "sticker": "F:\\Deepseek-Chat\\data\\stickers\\maid\\dfy_6N02_猫在么.gif",
  "say": "这一屏实际念的话",       // 不填就念 text（讲解片基本都要填）
  "at": 9.42, "dur": 8.69          // 一般由 examples/workflow_film.py 量出来写进去
}
```

表情包 GIF 会先用 ffmpeg 抽成 10fps 的 PNG 序列（缓存在 `data/videogen_work/stickers/`），
再以 data URI 内联进页面 —— GIF 交给浏览器播的话，逐帧截图同一秒会拿到不同帧，
而 `file://` 图片画进 canvas 会把 canvas 污染，`toDataURL()` 直接抛异常。这两条都踩过。

抽帧后会**丢掉全透明的空帧**（按 PNG 体积筛）：有些 GIF 中间有几帧是「角色蹦出画布」，
整张透明，照原样播就是一串空白闪帧。`dfy_9N07_蹦` 20 帧里有 5 帧是 1 KB 的全透明帧，
过滤后剩 13 帧。

完整示例（12 屏、92 秒、带配音和 BGM）见 `examples/workflow_film.py`：

```powershell
& $py -m videogen.examples.workflow_film            # 逐句量时长 → 写 spec
& $py -m videogen.examples.workflow_film --build    # 顺手出片
```

竖屏安全区：a3 词块都在中间 84% 宽度内，字号按字数自动收敛（2 字一行时按 `0.40*W/len` 夹住），
不会贴脸或叠字 —— 这条是踩过坑的，横屏参考实现直接搬到竖屏会糊成一团。

## 4.5 音效层（代码合成，不依赖素材包）

音效不是下载的素材，是 `sfx.py` 用 `wave`+`math` **现场算出来的**六个音：可复现、无版权问题、整套不到 300KB。
想换音色就把同名 wav 放进 `F:\Deepseek-Chat\data\sfx\`（如 `hit.wav`），优先级高于合成结果。

| 名字 | 听感 | 合成方式 | 时长 |
|---|---|---|---|
| `hit` | 砸字的闷响 | 55+40·e^(−10t) Hz 正弦 + 5ms 噪声瞬态，软削波 | 0.28s |
| `sub` | 低频下潜 | 58→34Hz 正弦，缓慢衰减 | 0.70s |
| `whoosh` | 转场气声 | 白噪过 350→4kHz 扫频带通，两端抛物线包络 | 0.45s |
| `pop` | 标签/贴纸弹出 | 700→1500Hz 短正弦 | 0.12s |
| `tick` | 逐条出现 | 2.2kHz 高通噪声脉冲 | 0.06s |
| `riser` | 收尾上升 | 180→1.2kHz 扫频 + 渐强噪声，末尾硬收 | 1.20s |

挂点是**按样式从时间轴自动推**的，手写脚本不用标秒数：

| 样式 | 自动音效 |
|---|---|
| `a3` | 每词一个 `hit`（首词 −4dB / 中间 −8dB / 末词 −1.5dB），末词叠 `sub` |
| `title` | 标题 `pop`，每条 bullet `tick` |
| `quote` | 每列 `whoosh`，末列 `riser` |
| `explain` | 每屏 `whoosh`，贴纸 `pop`，全片尾 `riser` |

单条覆盖：`Line.sfx = "hit"` / `"hit:0.35"`（名字:相对偏移秒）/ `"none"`；
整片关闭 `"sfx": {"enabled": false}`，音量 `"sfx": {"volume": 0.14}`。

**混音位置**（`audio.py` 的 `mix()`）：音效单独一路输入，各自 `adelay` 摆位 → `amix` → `highpass=90` → `volume`
→ 与人声/BGM 底床 `amix` → `loudnorm`。三条规矩：

1. 音效**不进 sidechain key**，BGM 只为人声让路，不会被音效「闪避」；
2. 音效**在 loudnorm 之前**并入，峰值算进整体响度，不会顶穿 TP；
3. 逐事件有独立 dB 增益，再乘整片 `sfx_volume`，改一个数就能整体轻/重。

没有人声也能出片：`--no-audio` 或配音全部失败时，`mix()` 会补一路 `anullsrc` 静音当人声床，
BGM 与音效照常混出来（只有 `--no-bgm --no-sfx` 才会退回纯静音片）。

实测（a3 五句 4.17s）：音效 RMS 比人声低 5–11dB，`I` 仍 −14.2~−14.4 LUFS、TP −1.4~−1.5 dBFS，渲染耗时不变，混音 +0.1s。

## 5. 性能实测（本机 i7-12700H + RTX 3060 Laptop，实际是 CPU 在干活）

| 环节 | 数值 |
|---|---|
| 单帧渲染（CDP canvas） | **33–36 ms** |
| 单帧渲染（每帧重启 Edge 的旧法） | 1260 ms（慢 38 倍，已弃用） |
| a3 三句、无声（3.04s 成片） | 9.4 s 总耗时 |
| a3 五句、带配音+BGM（4.17s 成片） | **12.9–13.0 s**（渲染 10.7 / 配音 1.5 / 混音 0.6） |
| a3 五句、无声（4.17s 成片） | 11.9 s |
| title（3.21s 成片） | 14.7 s |
| quote（3.58s 成片） | 23.5 s（噪点 + 逐字绘制最吃 CPU） |
| explain（92s 成片、2212 帧、12 屏） | **147 s**（渲染 134 / 配音 4 / 混音 8.4），14.8 MB |
| explain + 音效（25 个音效事件） | 154 s（渲染 134.8 / 配音 4.0 / 混音 **15.3**），14.1 MB |
| 产物体积 | 3–6s 竖屏 ≈ 130–250 KB（CRF 21）；带配音 ≈ 280–450 KB |

`explain` 每帧都要压渐变和半透明叠加，PNG 压帧要 200ms+；所以 explain 自动改用 JPEG 帧
（`toDataURL('image/jpeg', 0.93)`，单帧 81ms，快 2.8 倍），编码时再把 full range 转回 tv range，
免得手机 QQ 上发灰。

单句 Edge TTS 实测 **0.2–0.4 s**（进程内直接调，不走常驻服务）；偶尔网络抖动会到数秒，
所以同一批任务的总耗时主要看**帧数**：`时长 × fps × 33ms` 就是渲染成本。
`quote` 样式因为逐字绘制 + 900 个噪点，单帧明显比其他两个贵。

另外实测到一个坑：**BGM 开着时 `silencedetect` 测不出间隙**（BGM 填满了静音），
要验音画同步请用 `--no-bgm` 渲一条，再看 `silence_end` 是否落在节拍网格（0 / beat / 2beat …）。

## 6. 目录结构

```
apps/videogen/
├── spec.py       Spec / Line 数据类、拆词、JSON 读写、validate
├── styles.py     四个样式的节奏参数（beat/first_at/gap/tail/min_duration/max_lines）
├── stickers.py   表情包 GIF → 帧序列 → data URI（抽帧结果缓存在 data/videogen_work）
├── render.py     模板 + spec → HTML；调 PowerShell 渲帧；读进度
├── audio.py      edge-tts 逐句 → 裁静音 → adelay 摆位 → BGM 闪避 → loudnorm
├── pipeline.py   串起来 + 进度权重 + 失败降无声 + 产物命名
├── prompts.py    风格提示词模式：读 341 条提示词 → DeepSeek 写单页 HTML → 渲帧 → 配音混音
├── auto_film.py  AI 自拟模式：一句要求 → AI 写分镜 → explain 多屏 → 出长片（最长 6 分钟）
├── jobs.py       SQLite 任务表（原子抢占、重启恢复、每人限流计数）
├── worker.py     run_job / loop
├── cli.py        子命令
├── examples/     成品示例（workflow_film.py = 12 屏讲解片）
├── paths.py      所有外部路径集中在这里（换机器只改这一个文件）
├── templates/    a3 / title / quote / explain 的 index.html（单文件，`__SPEC_JSON__` 占位）
└── tools/        render-cdp.ps1（Edge CDP 逐帧截图，纯 ASCII 注释）
                build_job.py / dump_catalog.py（面板用的任务入口与风格库 JSON）
                thumb_fill.py（给风格提示词补同名缩略图）
```

HTML 模板的约定（写新样式要遵守）：
`const SPEC = __SPEC_JSON__;`、`window.DUR = 时长`、`window.seek(t)` 在时间 t 画一帧、
`?t=2.4` 只画那一刻（给截图用）。**不要有 `Date.now()` / 无种子的 random** —— 逐帧截要求同一 t 永远同一画面。

## 7. 已知限制

- 四个样式 + 音效层都已接好；**没有做「音效卡 BGM 拍点」**（BGM 没做 beat detection，硬凑会打架）。
- 需要的机器条件：Edge（本机已有）、ffmpeg 7+（本机 9.0.2）、`apps/voice/.venv` 里能用 edge-tts；
  `doctor` 会逐项报。
- 画面是「文字动效」，不是影像：要实拍空镜/画面素材得接即梦或 MoneyPrinterTurbo，见 `扩展方案对比.md`。
- BGM 用的是本机 `data/songs/stems/accompaniment.wav`；对外发内容注意版权与「AI 生成」标识要求。
- 一人一小时默认 3 条；`quote` 样式单条 20s+ CPU 占用，多人同时要慎重。

## 8. 排错

```powershell
& $py -m videogen.cli doctor          # 先看这个
& $py -m videogen.cli list            # 最近 10 条任务的状态/耗时/产物
& $py -m videogen.cli show <id>       # 失败原因在 err 字段
```

- **渲染报 "Edge 没起来"**：检查 `paths.py` 里 EDGE 路径，或手动开一次 Edge 过掉首次运行向导。
- **跑 `.ps1` 报「禁止运行脚本」**：本模块自己用 `powershell.exe -File` 调，不受执行策略影响；
  手工跑脚本要 `powershell -NoProfile -ExecutionPolicy Bypass -File xxx.ps1`。
- **脚本中文乱码**：PowerShell 5.1 按 ANSI 读无 BOM 文件。本模块的 ps1 全是 ASCII 注释，改的时候要么保 ASCII，
  要么存 UTF-8 **带 BOM**。
- **音画不同步**：先确认每句 TTS 都经过 `silenceremove` 裁静音再量长度（否则 edge-tts 的 0.4–0.6s 尾巴会被当成语速慢）。
- **卡在 running**：等 30 分钟后重启插件自动回收；急的话 `python -m videogen.cli` 改库或删 `data/videogen_jobs.db`。
- **任务卡在「渲染第 N 屏」不动**：`render-cdp.ps1` 现在有三道闸——CDP 单次读 60s、页面就绪 15s、整屏 `-Timeout`（默认 900s，AI 分屏 420s）。
  超时会清掉该屏页面缓存并整条失败，自动退回 `explain` 模板出一条（原因写进 `warnings`）。手工查僵尸：
  `Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'build_job|render-cdp' }`。
- **LLM 页面渲得慢（800ms/帧）**：那是截图模式。AI 分屏与短视频现在默认 `-Mode canvas -Format jpeg`（1080p 约 108ms/帧），
  页面没有 `#cv` canvas 时才回退截图模式。
- **动效在同一句话里反复播**：`prompts.timebox_page()` 会在渲帧前把页面的时间轴压进这一屏的时长、末帧 0.9995 防回跳；
  提示词侧也要求分 3–5 个节拍、禁止 `t % DUR`。

## 9. 加一个新样式

1. `templates/<key>/index.html`：抄 `a3` 的骨架，实现 `window.seek(t)` + `window.DUR`。
2. `styles.py`：往 `STYLES` 里加一条 `StyleInfo(key=..., label=..., template=..., default_beat=..., first_at=..., gap=..., tail=..., min_duration=..., max_lines=...)`。
3. `python -m videogen.cli preview --style <key> --words "..."` 在浏览器里对着 `?t=` 调。
4. `python -m videogen.cli quick --style <key> --words "..."` 出片。
5. 插件那边不用改：`make_video` 的 `style` 参数直接吃新 key。

`代码动画\风格提示词\` 里还有 341 种风格提示词（含参考实现），按上面 5 步挑着搬即可。

## 10. DSH 面板（视频工坊）

不想敲命令的话，DSH 里有一个面板：会话视图的页签行里（「对话 / 上下文 / 画廊 / … / 统计」）
点「视频工坊」，就切到面板，不换会话。入口注册在 `conversation.view`（id `videogen`，order 45）。

- 风格库 = `F:\Deepseek-Chat\代码动画\风格提示词` 全部 341 条（搜索 / 分类 / 缩略图）；
  选中后由 DeepSeek 按提示词写单文件动画页面，再交给 `prompts.render_external()` 渲帧。
  **可多选**：面板会为每条选中风格各建一个任务、共用同一句主题，队列串行逐条渲。
- 引擎 = 本模块的四个原生模板，走 `pipeline.build()`。
- AI 自拟（长片）= `auto_film.py`：一句要求 → 分镜 JSON（`round(duration/7.6)` 屏，3–60 屏）
  → 逐屏 TTS 量时长 → 灌 explain 模板 → 出 15–360 秒的片子；可选读真实工作区 README
  或插件清单当素材，表情包从 `data/stickers/maid` 的 14 个候选中挑（`--no-stickers` 可关），
  还能用 `--styles` 给几条风格提示词：**选中后画面改成逐屏按风格现写页面**
  （`render_style_film()`，多选按屏轮换，`prompts._chat` 写页面 → `render_external` 渲帧 →
  ffmpeg concat，页面缓存 `data/videogen_work/prompt_pages/auto_scenes/`），
  不选才用 explain 模板（此时 `style_hints()` 只影响分镜措辞）。单屏写页面失败会自动
  退回 explain 模板并在 `warnings` 里说明，不会让整条任务失败。横/竖/方屏在两种路径下都生效。
  **动效节奏（时间盒）**：模型写页面时经常把 `window.DUR` 写短、或者写成循环，渲出来的画面
  会在同一句旁白里重复播。现在两处都套了 `prompts.timebox_page()`：渲帧前把页面时间轴映射到
  这一屏真实时长（最多放慢 2.5 倍，超出就停在末帧），并把 `window.DUR` 改成真实时长，
  所以动效在整句旁白里逐步展开、播完停住、不会回头。提示词里也加了硬要求：
  3–5 个节拍（p≈0.15/0.4/0.65/0.9）、p=0.3/0.6/0.9 必须都有肉眼可见变化、禁止 `t % DUR`。
  `--context-file <txt>`（或 API 里的 `context_text`）可以再塞一段外部资料——面板的
  「本次对话」和「自选资料」就是这么进来的：Host 先读会话日志 / 指定文件，拼成文本随 job 传下来，
  Python 侧把它接在 `gather_context(kind)` 后面一起写进分镜提示词。
- 参数：主题、一句要求、参考材料、旁白、时长、画幅（竖/横/方）、清晰度、音色、配乐、音效档位、
  节拍、额外要求。
- 面板代码在 `F:\Deepseek-Chat\apps\dsh-videogen-console`（Host 路由 + React 面板），
  它只负责调度；出片逻辑、进度事件、任务表仍然全在这里。

命令行等价物：

```powershell
& $py -m videogen.cli quick --words "你|懂什么|听话" --out out.mp4
& $py -m videogen.prompts --list                  # 341 条风格清单
& $py -m videogen.auto_film --brief "总结这个工作区的工作流程" --duration 180 --context workspace
& $py -m videogen.auto_film --brief "讲讲面板怎么用" --duration 20 --no-stickers --styles "A-G_代码样片/A1_字雨" --plan-only
& $py -m videogen.auto_film --brief "把我们这次聊天总结一下" --duration 24 --context-file transcript.txt
python tools/build_job.py job.json                # 面板用的任务入口（JSONL 进度）
python tools/dump_catalog.py                      # 面板用的风格库 JSON
python tools/thumb_fill.py --dry-run               # 看哪些风格还没有同名 jpg
python tools/thumb_fill.py                         # 给缺的风格补缩略图（约 30s/条，可续跑）
python tools/thumb_fill.py --repair                # 只重做空白/纯色的静帧（小于 12KB）
```

> 缩略图现为 **341/341**。取帧方式：渲 2.6 秒，在 0.9 / 1.6 / 2.3 秒各取一张，挑细节最多（文件最大）的那张——单点取帧会撞上页面淡入，前半秒常是纯黑或纯白。

`build_job.py` 的 job JSON 形如：

```json
{"id":"x","mode":"auto","auto":{"brief":"…","duration":180,"context":"workspace",
 "width":1080,"height":1920,"fps":24,"voice":"zh-CN-XiaoxiaoNeural","rate":"+6%"}}
```
