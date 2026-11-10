# AI 速成视频（dsh-ai-fastvideo）

在 DeepSeek Harness 里一句话出片。会话视图里多一个「AI 速成视频」页签：

- **短视频（片段）**：从 **341 条**风格提示词里挑（多选批量），大模型按这条风格现场写一个单文件 Canvas 页面，
  逐帧导出 → 编码 → 配音 → 配乐 + 音效，几秒到几十秒一条。
- **AI 自拟（长片）**：只给一句要求（比如「总结这个工作区的工作流程」），模型自己拆分镜、逐屏出画面，
  15 秒到 6 分钟；可选风格提示词做视觉参考，可喂工作区 / 本次对话 / 自选文件三种材料。

没有视频模型、不占显存：画面是网页，渲帧由无头浏览器完成，风格完全由文本决定。

## 安装

要求：DSH 桌面版、Python 3.10+、ffmpeg 7+、Edge 或 Chrome。Node 用 DSH 自带的即可。

```powershell
# 方式零：从 GitHub 拿代码
git clone https://github.com/rightnowrose/dsh-ai-fastvideo.git

# 方式一：本地目录（开发 / 内网）
#   plugin_manager install_bundle target=<本包目录>

# 方式二：npm（发布后与 dsh-context 完全同款）
dsh plugin --profile web add dsh-ai-fastvideo
```

装完刷新网页，会话视图顶部页签里出现「AI 速成视频」。

## 配置（只需填模型 key）

配置文件：Windows `%USERPROFILE%\.dsh\data\dsh-ai-fastvideo\config.json`，macOS/Linux `~/.dsh/data/dsh-ai-fastvideo/config.json`。
首次打开面板会自动建目录；只有模型 key 是必填的：

```jsonc
{
  "llm": { "baseUrl": "https://api.deepseek.com/v1", "model": "deepseek-chat", "apiKey": "sk-..." }
}
```

其余都可以留空，插件会按「配置文件 → 环境变量 → 自动探测」找 python / ffmpeg / 浏览器：

```jsonc
{
  "python": "C:\\Python311\\python.exe",
  "ffmpeg": "C:\\ffmpeg\\bin\\ffmpeg.exe",
  "ffprobe": "C:\\ffmpeg\\bin\\ffprobe.exe",
  "browser": "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "renderer": "auto",            // auto | node | powershell
  "styleDir": "",                // 留空 = 用包内 341 条风格
  "bgm": { "file": "", "volume": 0.13 },
  "stickersDir": "",
  "workspaceDir": ""
}
```

面板右上「环境」角标能看到每一项的探测结果（`/ai-video/health` 的 `pythonOk / ffmpegOk / browserOk / renderer / llmReady`）。

## 风格库（341 条，随包）

`assets/styles/` 下按 10 类分目录，每条是「一个 `.md` 提示词 + 同名 `.jpg` 缩略图」。
风格库整理者：**哔哩哔哩 俯仰观照**（「代码动画」风格库）——其中 236 条由其整理/改写，其余 105 条来自三个开源项目（见下表）。

| 目录 | 数量 | 来源 | 许可 |
|---|---|---|---|
| `A-G_代码样片/` | 26 | 「代码动画」哔哩哔哩 俯仰观照 原创整理（含参考实现代码） | 哔哩哔哩 俯仰观照 约定 |
| `R_照参考学来的/` | 7 | 同上（哔哩哔哩 俯仰观照） | 哔哩哔哩 俯仰观照 约定 |
| `M_动态风格/` | 40 | 哔哩哔哩 俯仰观照 中文整理改写 | 哔哩哔哩 俯仰观照 约定 |
| `K_卡通画风/` | 38 | 哔哩哔哩 俯仰观照 中文整理改写 | 哔哩哔哩 俯仰观照 约定 |
| `Z_中国风/` | 15 | 哔哩哔哩 俯仰观照 中文整理改写 | 哔哩哔哩 俯仰观照 约定 |
| `H_现成版式/` | 12 | 哔哩哔哩 俯仰观照 中文整理改写 | 哔哩哔哩 俯仰观照 约定 |
| `O_奥斯卡98部/` | 98 | 哔哩哔哩 俯仰观照 按影史风格整理 | 哔哩哔哩 俯仰观照 约定 |
| `L_Lemo-Opuscar/` | 59 | [lemomo-ai/lemo-opuscar](https://github.com/lemomo-ai/lemo-opuscar) | MIT |
| `V_mg-styles-15/` | 15 | mg-styles-15（Vincentwei1021） | MIT |
| `X_code-video/` | 31 | code-video（Bae-ChangHyun） | MIT |

逐类来源、哔哩哔哩 俯仰观照 的约定原文、第三方许可全文见 **[assets/styles/SOURCES.md](assets/styles/SOURCES.md)** 与
**[assets/licenses/](assets/licenses/)**。哔哩哔哩 俯仰观照 约定：免费使用、转发也请免费分享，**禁止包装成课程 / 付费 skills 出售**。

加自己的风格：往 `assets/styles/<分类>/` 放 `.md`（同名 `.jpg` 可选），60 秒内或点一次「刷新」生效；
批量导入见 `scripts/import_styles.py`。

## 两条出片路径

| | 短视频（片段） | AI 自拟（长片） |
|---|---|---|
| 输入 | 选风格 + 主题/台词 | 一句要求（+ 可选风格、材料） |
| 画面 | 每屏按风格现写页面 | 模型先出分镜，再逐屏写页面 |
| 时长 | 3–60 秒 | 15–360 秒 |
| 耗时 | 十几秒（+ 写页面时间） | 分钟级（6 分钟片约 12–15 分钟） |

渲染器两套：默认 Node（`engine/node/render.mjs`，三平台通用，约 70ms/帧@1080p），
没有 node 时退回 PowerShell 版。`config.renderer` 可强制指定。

## 命令行（不走面板也能用）

```powershell
cd engine\python
python -m videogen.cli quick --words "你|懂什么|听话" --out out.mp4
python -m videogen.cli build spec.json --out out.mp4
python -m videogen.auto_film --brief "讲讲这个项目" --duration 30 --out out.mp4
python -m videogen.cli doctor
```

## 排错

| 现象 | 原因 / 处理 |
|---|---|
| 风格卡片是灰块 | 该风格没有 `.jpg`；本包 341 条都带缩略图，除非你删了图 |
| 任务一直卡在「渲染」 | 0.2 起有三层超时（CDP 读 60s / 单屏 900s / 进程看门狗），超时会自动放弃这一屏并退回模板 |
| 成片发灰 | 用旧版 PowerShell 渲染器时才有；确认 `health.renderer = "node"` |
| 没声音 | `pip install edge-tts`；或音色是自训练音色但 GPT-SoVITS 没起 |
| 6 分钟片太慢 | 长片是逐屏串行写页面 + 渲帧，1080p 约 12–15 分钟；先用 720p |

## 开发

```powershell
node --check index.js
python -m compileall -q engine/python
node scripts/selftest.mjs      # 假 ctx 跑通 health / catalog / jobs，不需要浏览器
```

结构：`index.js`（Host：HTTP 路由 + 任务队列 + 子进程） / `client.js`（浏览器：React 面板） /
`engine/python`（分镜、配音、混音、调渲染器）/ `engine/node`（CDP 逐帧渲染器）/ `assets/styles`（341 条风格）。
设计细节见 [docs/design.md](docs/design.md)，发布与分发见 [docs/publishing.md](docs/publishing.md)。

## 许可

MIT（见 `LICENSE`）。风格库的第三方部分保留原许可与署名（见 `NOTICE.md`、`assets/styles/SOURCES.md`）。
