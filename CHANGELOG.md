# Changelog

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号用语义化。

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

- 0.1 只在 **Windows** 上跑通（依赖 PowerShell 5.1 的 `render-cdp.ps1`）；macOS/Linux 的 CDP 渲染器在路线图里。
- 长片模式逐屏写页面很慢：720p 6 分钟片约 8–12 分钟，1080p 约 12–18 分钟。
- 只带 `edge-tts`；自训练音色（GPT-SoVITS）需要自己配 `VIDEOGEN_VOICE_CONFIG` / `VIDEOGEN_GSV_START`。
- 内置 26 条示例风格，其余风格库需自行导入（见 NOTICE）。

### 路线图

- [ ] 用 Node 重写 CDP/ffmpeg 编排，去掉对 PowerShell 与「系统 Python」的硬依赖
- [ ] 分镜页面的并行生成（现在是串行）
- [ ] 片头/片尾模板、转场、字幕轨
- [ ] 一键把成片推到 QQ / 微信（配对 dsh 消息插件）
