# 参考的开源项目

> 状态：现状记录｜适用：历史设计来源｜核验：2026-08-28｜依据：原有来源清单；外部项目状态与许可本轮未重新核验。

以下记录项目形成过程中的参考，不是当前产品定位或承诺采用的技术路线；现行方向见 [产品说明](docs/product/current-state.md)。

这个骨架不是凭空写的。下面是每一块具体参考了谁，以及参考了什么。

| 项目 | 许可 | 这里参考了什么 |
|---|---|---|
| [memex-lab/memex](https://github.com/memex-lab/memex) | 见仓库 | 整体形态：本地优先、自带 LLM key、把照片/文字/语音收进一条时间线。Murmur 拿掉了它的"整理归档"目标，换成"即时回应"，但"数据留在本地 + BYO LLM"这条是从它那儿来的。 |
| [smixs/iva](https://github.com/smixs/iva) | 见仓库 | 分层记忆的思路——把每条消息落成一条带元数据的记录，之后能按维度检索。iva 落成 Obsidian markdown，[`murmur/memory.py`](murmur/memory.py) 为了能按「地点 × 时段」聚合改用 SQLite。 |
| [Romancha/photo-moments-telegram-bot](https://github.com/Romancha/photo-moments-telegram-bot) | 见仓库 | 它的 `/info` 命令展示了一张照片该读哪些 EXIF 字段（时间、机型、GPS）。[`murmur/photo.py`](murmur/photo.py) 是同一组字段，用 Pillow 重写。 |
| [TokenMixAi/tg-ai-bot](https://github.com/TokenMixAi/tg-ai-bot) | 见仓库 | 两处：图片 → base64 data URL → vision 输入的处理方式；以及"可配置上下文深度"这个参数化思路（这里对应 `Memory.recent(limit)`）。[`murmur/bot.py`](murmur/bot.py) 的 photo/document 双通道处理也是照着它的形状搭的。 |
| [hanamorix/companion-emergence](https://github.com/hanamorix/companion-emergence) | 见仓库 | 陪伴型 agent 的持久人格 + 情绪状态设计。Murmur 没有采用它的情绪状态机（对这个场景太重），但"人格是一等公民、单独成文件"这点照搬了 —— 见 [`murmur/persona.py`](murmur/persona.py)。 |
| [mem0ai/mem0](https://mem0.ai/) | Apache-2.0 | 没有直接依赖。曾作为语义记忆方向的参考，不是已决定的替换方案。 |

**没有直接复制任何项目的代码**，参考的是设计决策。`persona.py` 里的提示词是原创的。

## 依赖与服务

| 依赖 / 服务 | 许可 | 用途 |
|---|---|---|
| [DeepSeek](https://platform.deepseek.com) | 商业服务 | 模型与余额接口，OpenAI 兼容。对话走 `/beta`（assistant prefix 只在 beta 上有） |
| [models.dev](https://models.dev/api.json) | 见站点 | 模型目录（模态/上下文/价格）的数据源，选型时查的就是它 |
| [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) | LGPL-3.0 | Telegram 层 |
| [Tencent/openclaw-weixin](https://github.com/Tencent/openclaw-weixin) | 见仓库 | 微信官方 ClawBot 通道。**没有依赖这个 npm 包**：`wechat.py` 照着它 README 的 "Backend API Protocol" 一节和随包发布的 TS 源码，用 Python 重实现了收发那几个接口（`ilink/bot/getupdates`、`sendmessage`、`sendtyping`、`getconfig`）。扫码登录仍然交给它的 CLI 做——那部分涉及设备绑定和配对码，反解不值当 |
| [dingtalk-stream-sdk-python](https://github.com/open-dingtalk/dingtalk-stream-sdk-python) | MIT | 钉钉 Stream 长连接 |
| [tencent-connect/botpy](https://github.com/tencent-connect/botpy) | MIT | QQ 官方机器人 WebSocket 网关。收消息用它；发消息走公开的 HTTP（`/v2/users/{openid}/messages`） |
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 / BSD | 微信 CDN 上的图是 AES-128-ECB 加密的，用它解 |
| [openai-python](https://github.com/openai/openai-python) | Apache-2.0 | 调网关的客户端（不是调 OpenAI） |
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU | 图像处理与 EXIF |
| [pillow-heif](https://github.com/bigcat88/pillow_heif) | BSD-3-Clause | iPhone 的 .HEIC 解码 |

## 商业产品参考

- [Dot by New Computer](https://web.archive.org/web/2025/https://new.computer/dot) —— 已于 2025 年 10 月关停。形态最接近，本项目的"发图 → 回一句"来自它，但 Dot 是文字/语音优先。
- [Kin](https://mykin.ai/) —— 目前最像 Dot 的接班人，本地优先存储这条值得对齐。
- [Rosebud](https://www.rosebud.app/) —— AI 日记，记忆系统扎实，但需要用户主动打字。
