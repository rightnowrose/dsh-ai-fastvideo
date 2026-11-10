# NOTICE

本仓库代码为 MIT（见 `LICENSE`）。此外随包分发了 **341 条风格提示词**（`assets/styles/`），来源与许可见
**[assets/styles/SOURCES.md](assets/styles/SOURCES.md)**，第三方许可全文在 **[assets/licenses/](assets/licenses/)**。

## 随包的风格库

| 目录 | 数量 | 来源 | 许可 |
|---|---|---|---|
| `A-G_代码样片/`、`R_照参考学来的/`、`M_动态风格/`、`K_卡通画风/`、`Z_中国风/`、`H_现成版式/`、`O_奥斯卡98部/` | 236 | 「代码动画」作者整理（含中文改写与参考实现） | 作者约定：免费使用、转发也请免费分享，禁止包装成课程 / 付费 skills / 收费产品出售 |
| `L_Lemo-Opuscar/` | 59 | [lemomo-ai/lemo-opuscar](https://github.com/lemomo-ai/lemo-opuscar) | MIT，Copyright (c) 2026 LemoLab |
| `V_mg-styles-15/` | 15 | mg-styles-15（Vincentwei1021） | MIT，Copyright (c) 2026 Vincentwei1021 |
| `X_code-video/` | 31 | code-video（Bae-ChangHyun） | MIT，Copyright (c) 2026 Bae-ChangHyun |

二次分发请保留：本文件、`assets/styles/SOURCES.md`、`assets/licenses/`，以及每条风格 `.md` 里的原始署名行。

## 不随包的东西

- 表情包（如 maid 系列 GIF）：只在运行时从 `stickersDir` 读取，不在仓库里；
- BGM 音频：运行时从 `bgm.file` 读取；
- 自训练音色（GPT-SoVITS 权重与参考音频）：不随包；
- 任何 API key：写在用户自己的 `<dataDir>/config.json` 里。

## 运行时依赖（由用户自备，不随包）

ffmpeg / ffprobe（7+）、Edge 或 Chrome、Python 3.10+、`edge-tts`、一个 OpenAI 兼容大模型 key。
