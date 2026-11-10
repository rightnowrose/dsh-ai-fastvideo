# Changelog

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号用语义化。

## [0.3.0] - 2026-11-11

### 变更

- 包名改为 `dsh-ai-fastvideo`（显示名仍是「AI 速成视频」）；数据目录随之改为 `~/.dsh/data/dsh-ai-fastvideo`。
- **风格库改成随包全量分发**：`assets/styles/` 从 26 条扩到 **341 条**（10 类，341 个 `.md` + 341 张 `.jpg`，约 17.8MB）。
- 新增 `assets/styles/SOURCES.md`：逐类标注来源与许可（作者整理 236 条 / Lemo-Opuscar 59 / mg-styles-15 15 / code-video 31），
  第三方 MIT 许可原文放 `assets/licenses/`；`NOTICE.md` 同步重写。
- README 精简重写（252 行 → 约 130 行），删掉重复的引擎 README（`docs/engine-README.md`），
  只保留 `docs/design.md`（设计细节）与 `docs/publishing.md`（发布/分发）。
- 默认使用包内风格库：`config.json` 不再需要写 `styleDir`（写了则覆盖）。

### 修复

- 迁移数据目录时 `config.json` 里的 `styleDir` 会指向旧路径，已默认移除。

## [0.2.0] - 2026-11-11

### 新增

- **跨平台渲染器** `engine/node/render.mjs`：用 Node 内置 `WebSocket` 直连 CDP，零依赖，Windows / macOS / Linux 同一份代码。
  - `--mode canvas|screenshot`、`--format jpeg|png`、`--crf/--preset/--quality`、就绪轮询、CDP 读超时、单次渲染总超时
  - 1080p 实测 ≈ 70ms/帧（PowerShell 版 ≈ 110ms/帧），且不再踩 `Page.captureScreenshot` 卡死
- `config.json` 新增 `"renderer": "auto" | "node" | "powershell"`；`paths.renderer_kind()` 自动选择（node 优先，无 node 退回 ps1）。
- `/ai-video/health` 增加 `renderer` / `node` / `nodeRenderer`，面板「环境」里能直接看到用的是哪个渲染器。
- `docs/publishing.md`：npm publish、社区目录（awesome-dsh-plugin）PR、`dsh-plugins-` 命名澄清、发布前自检清单。
- `package.json` 补 `engines.dsh` 与 `dsh.compatibility.dshReleases`，与 `dsh-context` 同款写法。

### 变更

- Python 引擎不再直接拼 PowerShell 命令：`prompts.render_external()` / `render.render()` 统一走 `render.render_page()`，
  由 `renderer_kind()` 决定起 node 还是 ps1。
- PowerShell 从「必须」降级为「可选兜底」。

### 修复

- `prompts.STYLE_ROOT` 在环境变量给出字符串时丢失 `Path` 类型，导致 `AttributeError: 'str' object has no attribute 'exists'`。
- `paths.NODE_RENDER` 指向了 `engine/python/node/`，实际在 `engine/node/`。

## [0.1.0] - 2026-11-11

第一个可发布版本（从本机自用的 `dsh-videogen-console` 整理而来）。

### 新增

- **会话视图页签**：`AI 速成视频` 注册进 `conversation.view`，和「对话 / 上下文 / 画廊」并列。
- **短视频（片段）**：风格提示词 → 大模型现写单文件 Canvas 页面 → CDP 逐帧 → mp4。
  - 风格网格（搜索 / 分类 / 缩略图 / 多选批量出片）
  - 画幅（竖 / 横 / 方）、清晰度（1080/720）、音色、配乐、音效四档、时长、旁白、额外要求
- **AI 自拟（长片）**：一句话 → 分镜 JSON → 逐屏画面 → 配音 → 成片，15–360 秒。
  - 可选风格提示词（逐屏轮换），也可完全交给模型
  - 参考材料：工作区、本次对话、自选资料（文件/目录）
  - 表情包开关
- **渲染引擎**（`engine/python`）：
  - 四个原生模板：大字冲击 / 标题卡 / 竖排金句 / 讲解片
  - 时间盒（timebox）：把模型页面的时间轴拉伸铺满旁白，末端冻结，避免一句话内循环重播
  - 配音按节拍摆放 + BGM 闪避（sidechain）+ 六种合成音效 + loudnorm −14 LUFS
  - 渲染三层超时：CDP 读超时 60s、单屏总超时 900s、进程看门狗
- **可配置**：`<dataDir>/config.json`（python / ffmpeg / 浏览器 / 风格目录 / BGM / 表情包 / LLM / 音色），
  以及环境变量覆盖；首次运行自动建目录。
- **发布配套**：README、LICENSE(MIT)、NOTICE（风格库署名）、CHANGELOG、CI（node --check + python compileall）、
  `scripts/import_styles.py`（批量导入风格库）。

### 已知限制

- 长片模式逐屏写页面很慢：720p 6 分钟片约 8–12 分钟，1080p 约 12–18 分钟。
- 只带 `edge-tts`；自训练音色（GPT-SoVITS）需要自己配 `VIDEOGEN_VOICE_CONFIG` / `VIDEOGEN_GSV_START`。

### 路线图

- [x] 用 Node 重写 CDP/ffmpeg 编排，去掉对 PowerShell 与「系统 Python」的硬依赖（0.2.0）
- [x] 风格库全量随包分发（0.3.0）
- [ ] 分镜页面的并行生成（现在是串行）
- [ ] 片头/片尾模板、转场、字幕轨
- [ ] 一键把成片推到 QQ / 微信（配对 dsh 消息插件）
