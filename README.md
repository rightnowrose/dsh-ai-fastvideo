# AI 速成视频（dsh-ai-video）

> 在 DSH 的会话视图里，把一个想法变成一条能发的视频：选风格 → 填一句话 → 拿 mp4。
> 画面是网页逐帧渲出来的，配音走 Edge TTS，配乐和音效自动混，全程不用打开剪辑软件。

[![CI](https://github.com/USER/dsh-ai-video/actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

---

## 它长什么样

装好之后，会话视图顶部那排页签（`对话 / 上下文 / 画廊 / …`）里会多一个 **AI 速成视频**。
点进去是两栏：左边挑风格、填参数，右边看进度、预览、拿文件。

| 页签 | 适合 | 时长 | 谁在写画面 |
|---|---|---|---|
| **短视频（片段）** | 金句、封面、片头、单点讲解 | 3–60s | 你选的风格提示词 → 大模型现场写一个 Canvas 页面 |
| **AI 自拟（长片）** | 功能导览、教程、工作流程总结 | 15–360s | 大模型先写分镜，再逐屏出画面（可指定风格，也可完全交给它） |

两种模式都可以选：画幅（竖屏 9:16 / 横屏 16:9 / 方形）、清晰度（1080 / 720）、音色、
配乐与音量、音效档位、旁白开关、额外要求；长片还能额外喂三种材料 ——
**工作区**（README + 目录树）、**本次对话**（当前会话的问答记录）、**自选资料**（填文件/目录路径，Host 读）。

## 依赖

| 依赖 | 用途 | 必须 |
|---|---|---|
| DSH（DeepSeek Harness）桌面版 | 宿主 | ✅ |
| Python 3.10+ | 渲染引擎 | ✅ |
| ffmpeg 7+ / ffprobe | 编码、混音 | ✅ |
| Edge 或 Chrome | 逐帧截图（CDP） | ✅ |
| Windows PowerShell 5.1 / pwsh | 跑渲染脚本 `render-cdp.ps1` | ✅（目前 0.1 只在 Windows 上跑通） |
| `pip install edge-tts` | 中文配音 | 建议（不装就出无声片） |
| OpenAI 兼容大模型 key | 「写页面 / 写分镜」两种模式 | 建议（不填就只能用原生模板 CLI） |

Python 侧只用一个第三方包（`edge-tts`），其余全是标准库 —— 没有 torch、没有 whisper、不占显存。

```powershell
# 装依赖（任选一个 Python 3.10+ 解释器）
pip install edge-tts
# ffmpeg：winget install Gyan.FFmpeg 或去 https://ffmpeg.org/download.html
```

## 安装到 DSH

这个包是标准的 DSH bundle（`dsh.bundle.patch` + `dsh.client`，Host 半边 + 浏览器半边）。

```powershell
# 1) 拿到代码
git clone https://github.com/USER/dsh-ai-video.git
cd dsh-ai-video

# 2) 在 DSH 会话里让 agent 装（plugin_manager 的 install_bundle 接受本地目录）
#    或者命令行：
#    dsh plugin install ./dsh-ai-video

# 3) 刷新 DSH 网页 → 会话视图顶部页签里出现「AI 速成视频」
```

> 想手动验证装没装上：`plugin_manager` 里应能看到 `dsh-ai-video`；会话视图页签里 `AI 速成视频` 在后面。

## 配置

首次运行会自动建数据目录：

| 平台 | 默认数据目录 |
|---|---|
| Windows | `%USERPROFILE%\.dsh\data\dsh-ai-video` |
| macOS / Linux | `~/.dsh/data/dsh-ai-video` |

在这个目录下写 `config.json`（字段全可选，缺的自动探测）：

```json
{
  "python": "",
  "ffmpeg": "",
  "ffprobe": "",
  "browser": "",
  "styleDir": "",
  "bgm": { "file": "D:\\music\\bgm.wav", "volume": 0.13 },
  "stickersDir": "",
  "workspaceDir": "D:\\my-project",
  "llm": {
    "baseUrl": "https://api.deepseek.com/v1",
    "apiKey": "sk-...",
    "model": "deepseek-chat"
  },
  "voices": [
    { "id": "zh-CN-XiaoxiaoNeural", "name": "晓晓" }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `python` | 跑引擎的解释器；留空自动找 `python` / `python3` |
| `ffmpeg` / `ffprobe` | 留空从 PATH 和常见安装位置找 |
| `browser` | Edge / Chrome 可执行文件；留空自动找 |
| `styleDir` | 风格提示词目录（默认用包内 `assets/styles`） |
| `bgm.file` | 默认配乐；留空则不出配乐 |
| `stickersDir` | 表情包目录（GIF），仅长片模式用 |
| `workspaceDir` | 「参考材料 → 工作区」读哪个目录 |
| `llm` | OpenAI 兼容接口；`apiKey` 为空时两种 AI 模式会明确报错 |
| `voices` | 追加音色（id 需是 edge-tts 的音色名） |

也可以用环境变量覆盖：`DSH_AI_VIDEO_DATA`、`DSH_AI_VIDEO_PYTHON`、`DSH_AI_VIDEO_FFMPEG`、
`DSH_AI_VIDEO_BROWSER`、`DSH_AI_VIDEO_STYLE_DIR`、`DSH_AI_VIDEO_LLM_KEY` 等。

自检：

```powershell
python engine/python/tools/dump_catalog.py --force   # 打印风格库统计（一行 JSON）
node --check index.js; node --check client.js
```

## 工作原理

```
浏览器面板（client.js，React 注册进 conversation.view 页签）
        │  fetch  /ai-video/catalog | jobs | media | transcript | files
        ▼
Host 半边（index.js，Node）
        │  spawn  python engine/python/tools/build_job.py  job.json
        ▼
Python 引擎（engine/python/videogen）
        │  ① 大模型写页面 / 写分镜（可选）
        │  ② Edge 无头逐帧截图（CDP，canvas.toDataURL）
        │  ③ Edge TTS 逐句配音 → 按节拍摆放
        │  ④ ffmpeg 混 BGM + 音效 + loudnorm → mp4
        ▼
<dataDir>/out/<日期>/*.mp4   ←  面板里直接播
```

关键设计：

- **画面是网页**：一份单文件 HTML + `window.seek(t)` 时间纯函数 → headless Edge 逐帧导出 PNG/JPEG → ffmpeg 编码。
  没有视频模型、不占显存，风格完全由「提示词」决定，改风格 = 改文本。
- **先写页面再截图**：风格库里的每条提示词都可以让 LLM 现写一个 Canvas 页面，
  所以 26 条内置风格只是起点，你可以随时往目录里加自己的。
- **时间盒（timebox）**：LLM 写的页面常常自带循环或提前播完；渲帧前统一注入一层时间映射，
  把整段动画拉伸铺满这一屏旁白，末端冻结，不重播。
- **任务串行**：一次只渲一条（两条 CDP 会抢 GPU 和输出目录），关掉面板不丢任务。

## 加自己的风格

风格 = 一个 Markdown + 可选一张同名 jpg：

```
assets/styles/A3_大字冲击.md      # 名字里带代号，渲染时按「分类/代号」识别
assets/styles/A3_大字冲击.jpg     # 缩略图（没有就显示代号）
```

`.md` 约定：

```markdown
# A3 大字冲击

- 分类：代码样片
- 长什么样：黑底、超大字、逐词砸入…
- 适合：金句、情绪爆发
- 关键词：大字、冲击、节拍

## 风格提示词

（这里是给大模型看的完整画法说明，可以很长）
```

加完之后**不用重启**：面板里「刷新」或等 60 秒缓存过期即可。
批量导入别人的风格库：

```powershell
python scripts/import_styles.py "D:\some\style-library" --dry-run
python scripts/import_styles.py "D:\some\style-library"
```

## 命令行直接用（不经过面板）

```powershell
cd dsh-ai-video

# 看看有哪些原生模板
python -m videogen.cli styles

# 原生模板：三句台词、带配音和音效
python -m videogen.cli quick --words "你|懂什么|听话" --voice zh-CN-XiaoxiaoNeural

# 风格提示词模式
python -m videogen.prompts --list
python -m videogen.cli build spec.json
```

> 引擎是包内的 Python 包，路径默认按「包根目录」推；命令行直接跑时把
> `VIDEOGEN_DATA` / `VIDEOGEN_WORK` 指到一个可写目录即可，例如
> `$env:VIDEOGEN_DATA="$env:TEMP\ai-video\out"`。

## 常见问题

**面板里风格是灰块、没有缩略图**
图是懒加载的，先滚一下；还不行说明 `.jpg` 不存在（打开 `/ai-video/health` 看 `styleRoot` 对不对）。

**任务一直停在「渲染第 N/M 屏」**
渲染单帧卡死以前会无限挂；0.2 起每屏有 900 秒总超时 + 60 秒 CDP 读超时，超时会跳过该屏并退回讲解片模板。
若仍卡住，`Get-Process | Where-Object { $_.ProcessName -match 'edge|python|powershell' }` 看看有没有僵尸进程。

**出片是黑屏 / 手机端发灰**
必须是 `h264 + yuv420p + tv range`；本引擎默认就是，若你手改了 `render-cdp.ps1`，注意 JPEG 帧要转 tv range。

**没有声音**
`pip install edge-tts` 没装，或 `config.json` 的 `llm` 没配（长片的旁白文案由模型写）。不装也能出片，会降级成无声。

**6 分钟长片要多久**
本机（i7-12700H + 3060 Laptop）实测：720p 6 分钟约 8–12 分钟出片；1080p 约 12–18 分钟。
真正的时间大头是「逐屏让模型写页面」+「逐帧截图」，不是编码。

## 证书与致谢

代码 MIT（见 [LICENSE](LICENSE)）。内置的 26 条示例风格来自作者的风格库，
上游来源与许可见 [NOTICE.md](NOTICE.md)；如果你想二次分发，请一并保留那份说明。

## 变更记录

见 [CHANGELOG.md](CHANGELOG.md)。
