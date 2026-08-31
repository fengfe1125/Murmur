# `netease-music-mcp` “一起听”源码审计

> 状态：技术与产品可行性调研，不代表生产验收
>
> 核验日期：2026-08-31
>
> 目标仓库：[`tianyupaipai-cmd/netease-music-mcp`](https://github.com/tianyupaipai-cmd/netease-music-mcp)
>
> 审计提交：[`0e27816d6ad8dac59ae54fcfc71b69cb5a41171b`](https://github.com/tianyupaipai-cmd/netease-music-mcp/tree/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b)（仓库 `package.json` 标记 `0.6.0`）

## 一句话结论

这个项目**可以作为 Murmur 的“网易云搜索、歌词、歌单和 Mac 官方客户端遥控”参考实现**，也能在 Mac 上打开网易云的“一起听”邀请页；但它**没有实现网易云一起听房间协议，不能让 AI 以第二名参与者身份创建、加入或维持房间**。

因此结论是：

- **Mac 陪听体验原型：有条件 Go。** Murmur 知道选了哪首歌，打开或遥控用户 Mac 上的网易云客户端，在聊天里陪伴；这是“AI 围绕同一次播放陪你聊”，不是“AI 账号加入网易云一起听房间”。
- **严格的网易云一起听：No-Go。** 当前源码没有房间 ID、参与者身份、邀请发送/接受、队列同步、进度同步、心跳、房间聊天或离开房间实现。
- **Murmur iOS/Android 正式功能：No-Go，不能直接复用。** 移动端只能远程调用搜索、歌词和歌单工具；仓库自己明确说明本地手机播放器需要另做 companion，且当前版本不包含。

最容易产生的误解是：工具名 `netease_listen_together_invite` 并不等于“一起听 API”。它实际上只执行一次 macOS `open`，让官方网易云客户端处理一个私有 URL Scheme。

## 1. 审计口径

### 1.1 “AI 和我一起听”的严格验收条件

若把目标理解为“AI 是网易云一起听房间中的参与者”，至少需要：

1. AI/机器人有网易云认可的参与者身份；
2. 能创建或加入房间并取得 `room/session ID`；
3. 能发送或接受邀请；
4. 能收发房间事件：成员、当前曲目、队列、播放/暂停、进度和切歌；
5. 能维持连接、心跳、重连与退出；
6. 若要在房间里对话，还需聊天/语音/表情接口；
7. 若声称 AI “听到音乐”，还需要合法取得音频或至少可靠的播放时间轴，并把相应信号送入模型。

这个仓库没有完成上述任何一个房间协议闭环。它能完成的是“让 MCP 客户端调用一组网易云相关工具”。

### 1.2 证据等级

- **已证实**：直接来自本次锁定提交的实际执行路径、工具注册、测试或网易公开页面。
- **合理推断**：从完整调用链可以推出，但没有在真实账号/真机上执行。
- **未知**：需要网易书面文档、合作准入或受控真机实验；本文不把未知写成可用。

本次没有导入 Cookie、没有登录网易云、没有启动播放器、没有操作用户账号，也没有安装依赖。

## 2. 实际能力矩阵

| 能力 | 代码事实 | 判断 |
| --- | --- | --- |
| 匿名搜索 | 请求 `music.163.com/api/search/get/web` | 已实现，但不是公开 SDK 合同 |
| 歌曲详情 | 请求 `music.163.com/api/song/detail/` | 已实现，但不是公开 SDK 合同 |
| 歌词 | 请求 `music.163.com/api/song/lyric` | 已实现；没有音频 |
| 读取/创建/修改自有歌单 | 导入 `MUSIC_U` / `__csrf`，仿网易云 Mac 客户端构造 EAPI | 已实现的非官方账号能力，稳定性与合规性需另审 |
| 打开歌曲/歌单/专辑/歌手 | 用 `open -b com.netease.163music` 打开网页 URL | 已实现的是打开页面，不保证直接播放 |
| Mac 播放/暂停/上一首/下一首 | JXA + `System Events` 遍历菜单并点击 | 已实现的 Mac UI 自动化，需要辅助功能权限 |
| Mac 系统级下一首 | 调用 macOS 私有 `MediaRemote.framework` | 已实现全局媒体命令；代码明确标记不是房间控制 |
| 检查“一起听能力” | 只按 `process.platform === 'darwin'` 返回固定 JSON | 不是运行时能力探测，也不是房间状态 |
| 打开“一起听”邀请页 | 打开 `orpheus://nm/play/listenTogether?refer=mcp` | 仅深链跳转；用户仍需手动选人/分享 |
| 创建/加入一起听房间 | 无对应请求、模型或工具 | 未实现 |
| 获取房间/会话 ID | 无字段、解析或存储 | 未实现 |
| 发送邀请或接受邀请 | 无好友/邀请接口 | 未实现；只打开邀请 UI |
| 房间成员、队列、进度、心跳、重连 | 无协议、轮询、WebSocket 或状态机 | 未实现 |
| 一起听文字/语音/表情 | 工具声明明确为 `false`；README 明确不调用私聊接口 | 未实现 |
| 音频流/播放 URL | 全仓库无音频流获取或播放实现 | 未实现 |
| iOS/Android 播放控制 | 只提供远程 MCP；无移动 companion | 未实现 |

## 3. 源码逐项核验

### 3.1 MCP 是“给 AI 调工具”，不是一个网易云房间参与者

服务入口只是创建 MCP Server 并连接 stdio transport，没有模型运行时、网易云机器人身份或房间客户端：[`src/server.js#L1-L12`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/server.js#L1-L12)。HTTP 版本也只是把请求交给 `createNeteaseMcpServer()`：[`src/http-server.js#L66-L80`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/http-server.js#L66-L80)。

实际注册了 16 个工具。与一起听相关的只有：

- `netease_listen_together_capabilities`：返回能力描述；
- `netease_listen_together_invite`：打开邀请界面。

工具注册可见 [`src/mcp-server.js#L257-L284`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/mcp-server.js#L257-L284)。没有 `create_room`、`join_room`、`room_state`、`send_invite`、`heartbeat`、`sync_queue`、`send_chat` 等工具。

**已证实：** MCP 让宿主 AI 决定何时调用工具，但 MCP 自身既不是 AI，也不是网易云一起听参与者。

### 3.2 `listen_together_capabilities` 是静态声明，不是探测

`getListenTogetherCapabilities(platform)` 的唯一判断条件是平台是否为 `darwin`。Mac 时直接写死：

- `invite.supported = true`；
- `room.synchronizedPlayback = true`；
- 文字、语音、表情全部为 `false`。

源码见 [`src/netease.js#L121-L141`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L121-L141)。它没有检查：

- 网易云客户端是否安装或处于支持版本；
- 用户是否登录；
- 私有 URL Scheme 是否仍有效；
- 当前是否已有房间；
- 房间成员或同步状态；
- 账号、地区、灰度或会员限制。

测试只是断言这段固定 JSON 原样返回：[`test/netease.test.js#L40-L59`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/test/netease.test.js#L40-L59)。测试名写着 “truthful”，不能替代真实客户端或房间集成测试。

**结论：** `synchronizedPlayback: true` 只能理解为作者对官方 Mac 客户端能力的描述，不能理解为 MCP 已经实现同步播放。

### 3.3 `listen_together_invite` 只打开深链

一起听 URI 是单个常量：

```text
orpheus://nm/play/listenTogether?refer=mcp
```

定义见 [`src/netease.js#L24-L31`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L24-L31)。真正执行时只做：

1. 确认运行在 macOS；
2. 确认 `/Applications/NeteaseMusic.app` 存在；
3. 执行 `/usr/bin/open -b com.netease.163music <URI>`；
4. 返回 `opened: true` 和“请在弹出界面中选择好友或分享方式”。

完整调用见 [`src/netease.js#L341-L351`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L341-L351)。

这里的 `opened: true` 只表示 `open` 命令没有报错，不证明：

- 邀请页真正显示；
- 创建了房间；
- 发出了邀请；
- 对方接受；
- 返回了房间 ID；
- AI 加入了房间。

单元测试也只检查 URI 字符串：[`test/netease.test.js#L30-L38`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/test/netease.test.js#L30-L38)。HTTP 测试只检查工具是否出现在工具列表：[`test/http-server.test.js#L99-L114`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/test/http-server.test.js#L99-L114)。没有一起听端到端测试。

### 3.4 Mac 播放控制不是一起听房间控制

常规播放控制通过 JXA 启动 `System Events`，扫描菜单栏并点击“播放/暂停、上一首、下一首”：[`src/netease.js#L354-L415`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L354-L415)。这意味着：

- 需要辅助功能/自动化授权；
- 依赖菜单结构和中英文名称；
- 会激活网易云客户端；
- 不读取当前歌曲、进度、队列或房间状态；
- 客户端 UI 改版可能使其失效。

“直接下一首”会首次使用时编译一个 C helper，再调用 macOS 私有 `MediaRemote.framework` 的全局下一首命令：[`native/media-remote.c#L1-L46`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/native/media-remote.c#L1-L46)。Node 返回值明确写着：

```json
{
  "method": "macos_media_remote",
  "uiAutomation": false,
  "roomControl": false
}
```

见 [`src/netease.js#L417-L440`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L417-L440)。仓库 README 也明确说只保证普通播放，房间是否接受切歌由官方客户端决定：[`README.md#L197-L201`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/README.md#L197-L201)。

**结论：** 这些控制可用于 Mac companion 原型，但不能当作房间同步 API，也不能可靠证明用户此刻听到了什么。

### 3.5 搜索、详情和歌词没有账号，也没有音频

搜索、详情和歌词使用匿名 HTTP 请求：

- `/api/search/get/web`：[`src/netease.js#L223-L238`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L223-L238)
- `/api/song/detail/`：[`src/netease.js#L240-L246`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L240-L246)
- `/api/song/lyric`：[`src/netease.js#L248-L257`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L248-L257)

返回的歌曲结构只有 ID、名称、艺人、专辑、时长和页面 URL：[`src/netease.js#L154-L164`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L154-L164)。歌词结构是 LRC/翻译/罗马音：[`src/netease.js#L186-L195`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/netease.js#L186-L195)。

全仓库没有歌曲播放 URL、音频流、DRM、缓存或播放器实现；项目也主动声明不提供音频下载：[`SECURITY.md#L35-L36`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/SECURITY.md#L35-L36)。

**结论：** AI 最多读到元数据和歌词。它没有“听到”网易云的音频波形，也不能据此准确知道当前播放秒数。

### 3.6 登录数据只用于歌单 EAPI，不用于一起听

项目从官方 Mac 客户端的 BinaryCookies 或本地 MAM 数据库提取 `MUSIC_U` 与 `__csrf`，保存为权限 `600` 的会话文件：[`scripts/import-macos-session.js#L16-L24`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/scripts/import-macos-session.js#L16-L24)、[`scripts/import-macos-session.js#L37-L80`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/scripts/import-macos-session.js#L37-L80)。加载时同样只接受这两个 Cookie：[`src/session.js#L107-L145`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/session.js#L107-L145)。

Cookie 被用于一组自制 EAPI 请求。代码包含固定 AES key、EAPI 拼装、伪装的 `osx / 3.1.9` header：[`src/playlist.js#L1-L59`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/playlist.js#L1-L59)，并通过 Cookie 请求账号和歌单接口：[`src/playlist.js#L62-L91`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/playlist.js#L62-L91)。实际账号操作只有：读取账号、自有歌单、创建歌单、增加和移除歌曲：[`src/playlist.js#L112-L185`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/playlist.js#L112-L185)。

远程个人模式会用 AES-256-GCM 加密保存 Cookie：[`src/personal-store.js#L112-L135`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/personal-store.js#L112-L135)、[`src/personal-store.js#L528-L570`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/src/personal-store.js#L528-L570)。这改善了静态存储安全，却不等于获得网易官方 OAuth 或 API 授权。

**已证实：** 登录 Cookie 没有进入 `openListenTogetherInvite()`，也没有用于任何房间调用。邀请深链依靠已经登录的官方 Mac 客户端。

**风险判断：** 这些端点、EAPI 格式和 URL Scheme 都不是仓库中附带的网易公开 SDK 合同。仓库也明确标注“非网易云音乐官方项目”：[`README.md#L1-L7`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/README.md#L1-L7)。MIT 许可证只覆盖该仓库代码，不授予网易接口、用户数据或音乐内容的商业使用权。

### 3.7 手机支持是远程 MCP，不是手机播放控制

仓库的跨平台矩阵写得很清楚：

- 搜索、详情、歌词和歌单可由手机连接远程 MCP；
- 播放控制需要本地 companion；
- Android 可设想 MediaSession/Accessibility adapter；
- iOS 更受限，需要获准的 App/Shortcut bridge；
- **当前发行版两者均不包含**。

见 [`docs/PLATFORM_SUPPORT.md#L17-L28`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/docs/PLATFORM_SUPPORT.md#L17-L28)。设备指南也明确说云端代码不能按官方手机 App 的按钮：[`docs/DEVICE_GUIDE.md#L68-L83`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/docs/DEVICE_GUIDE.md#L68-L83)。

**结论：** 把这个 Node MCP 部署到 VPS，再让 Murmur 手机 App 连接，不能得到“手机网易云一起听遥控”。

## 4. README 宣称与代码证据的差异

| README/命名容易造成的理解 | 实际代码证据 |
| --- | --- |
| “报告当前设备的一起听能力” | 只按平台返回固定布尔值，没有运行时探测 |
| “邀请好友一起听” | 只打开邀请界面，好友和分享方式由用户手动选择 |
| `room.synchronizedPlayback = true` | 代码没有读取或同步任何房间状态，只是在能力对象中写死 |
| Mac “打开一起听邀请 Stable” | 测试只校验 URI 和工具注册，没有真机/真账号集成测试 |
| 移动端可用 | 可用的是远程 MCP 通用工具；手机本地播放器 adapter 不存在 |

反过来，README 在安全边界上是诚实的：它明确写着“不调用私有聊天接口，也不自动发送一起听消息”，并明确普通下一首不保证房间接受：[`README.md#L190-L201`](https://github.com/tianyupaipai-cmd/netease-music-mcp/blob/0e27816d6ad8dac59ae54fcfc71b69cb5a41171b/README.md#L190-L201)。

## 5. 网易公开信息能证明什么

网易云音乐的 App Store 开发者页面公开列出“音乐视频/一起听/动态/歌房”等官方 App 功能，能证明**消费端一起听产品存在**，不能证明第三方获得房间 API：[网易云音乐 App Store 页面](https://apps.apple.com/cn/app/id590338362)。该页面同时链接网易云音乐的[用户服务条款](https://music.163.com/html/web2/service.html)。在没有公开开发者合同或书面授权前，不应把客户端内部接口推断为 Murmur 可商用的 API。

另有网易云信的官方场景示例 [`netease-kit/NEListenTogether`](https://github.com/netease-kit/NEListenTogether)，公开描述 iOS/Android 双人房、同步点歌/切歌/暂停/拖动、实时语音和即时消息。它证明“用网易云信能力构建自己的双人一起听产品”是一条商务集成路线，但它是**自建语聊房/即时通信解决方案**，不等于第三方加入网易云音乐消费 App 的“一起听”房间，也不自动授予网易云音乐消费曲库版权。

### 5.1 社区逆向实现能证明协议复杂度，不能作为生产授权

社区项目确实逆向出过网易云音乐消费端的一起听接口。已归档的 `Binaryify/NeteaseCloudMusicApi` 讨论明确说这些接口来自抓包，并且“特别复杂”：[`Issue #1676`](https://github.com/Binaryify/NeteaseCloudMusicApi/issues/1676)。后续社区实现列出的完整链路至少包括：

- 创建/检查房间；
- 接受邀请；
- 查询房间状态；
- 心跳；
- 播放命令；
- 同步播放列表命令；
- 获取同步歌单；
- 结束房间。

可交叉查看社区 `ncm-api-rs` 的[一起听接口清单](https://github.com/SPlayer-Dev/ncm-api-rs#%E4%B8%80%E8%B5%B7%E5%90%AC)。它自己的说明也明确承认通过伪造请求头/CSRF 调用接口、仅供学习并提示风控，不能视为官方 SDK 或商业授权。

这些社区资料在本报告中的用途只有一个：**证明真正的一起听实现需要房间生命周期、心跳和同步命令，而目标 `netease-music-mcp` 完全没有这些调用。** 它们不是建议的依赖，更不能据此把消费端私有协议加入 Murmur。即使技术上补齐所有逆向端点，仍然是生产 **No-Go**：接口可随时变化、账号 Cookie 风险高、可能触发风控，也没有曲库、用户数据和房间自动化的官方授权。

### 5.2 正式路线必须从网易开放平台入驻和厂商授权开始

网易官方 GitHub 组织发布的 [`NetEase/skills`](https://github.com/NetEase/skills) 已给出官方 Agent 接入方向：所有 `ncm-cli` 命令都要求先在[网易云音乐开放平台](https://developer.music.163.com/)入驻并申请 API Key（`appId` 和 `privateKey`），再完成用户登录授权。官方公开列出的 Agent 能力包括搜索、歌单管理、每日推荐和用户信息；Agent Skills 页面本身没有承诺消费端“一起听房间”接口。

网易云音乐开放平台的公开文档目录中另有[创建一起听房间](https://developer.music.163.com/st/developer/document?docId=09cb77284e224545a9e457b9fbd4bd03)、[多人房间二维码](https://developer.music.163.com/st/developer/document?docId=c31bba05ee5c4eedb8537db826eba987)和[房间用户](https://developer.music.163.com/st/developer/document?docId=d48b4bc986a44015b8537db826eba987)等能力。这证明官方合作体系里存在正式房间接口，是比社区逆向更正确的技术方向；但公开文档存在不等于 Murmur 的 App ID 已获得调用权限，也不能证明个人开发者、AI 机器人身份或 App Store 分发场景默认获准。个人开发者 FAQ 仍把个人场景限制在 `ncm-cli`，完整移动集成需要厂商评估和书面授权。

因此 Murmur 的正式询证顺序应是：

1. 以公司/产品身份申请开放平台入驻；
2. 询问允许的账号登录、搜索、曲库、播放和 AI 数据边界；
3. 以公开“一起听”文档为清单，逐项确认 Murmur 可获批的创建、加入、房间用户、播放同步、结算上报和机器人参与权限；
4. 若不提供消费端房间接口，转为网易云信自建双人房方案，并分别谈曲库播放版权；
5. 只有书面合同、API 权限和上架场景都明确后，才进入移动正式产品开发。

`netease-music-mcp` 的 Cookie/EAPI/私有深链不能替代这套厂商准入。

## 6. 对 Murmur 目标的逐条判断

| Murmur 目标 | 用本仓库能否完成 | 说明 |
| --- | --- | --- |
| 有网易云登录数据 | 部分 | 能导入 Cookie 做歌单；不是官方 OAuth，也没有房间授权 |
| Murmur 搜歌并在聊天里发歌 | 可以参考 | 搜索和详情可形成歌曲卡；接口稳定性/授权另审 |
| 用户向 Murmur 发网易云歌曲 | 可以参考 | 分享 URL/歌曲 ID 可解析成元数据；仓库未直接实现分享入口 |
| 在 Murmur App 内播放完整网易云歌曲 | 不可以 | 无音频 URL、SDK 或播放器；只是打开官方客户端 |
| Murmur 遥控 Mac 网易云播放 | 可以做原型 | 播放/暂停/切歌可用 UI 自动化/全局媒体命令；不能可靠读状态 |
| Murmur 遥控手机网易云播放 | 不可以 | iOS/Android companion 都未实现 |
| AI 加入网易云一起听房间 | 不可以 | 无房间协议和参与者身份 |
| AI 在一起听房间发送聊天/语音 | 不可以 | 代码明确为 false，项目明确不自动发送消息 |
| AI 真正“听到”当前音频 | 不可以 | 无音频或可靠时间轴输入；只能理解元数据/歌词 |

Murmur 当前已经定义了 `TrackV1` 歌曲卡、可选音乐 wire、客户端令牌边界和离散播放状态，见本仓库的[系统边界](../architecture/system-boundaries.md)与[数据生命周期](../architecture/data-lifecycle.md)。这个 MCP 可提供新的“网易云元数据/外部播放器 adapter”参考，但不应取代现有客户端播放器抽象，也不应把网易 Cookie 上传给模型或混入长期记忆。

## 7. 可复用与不可复用

### 7.1 可复用

1. **MCP 工具设计**：搜索、详情、歌词、打开资源、显式确认写歌单的职责拆分。
2. **歌曲标准化**：ID、标题、艺人、专辑、时长、官方页面 URL 可映射到 Murmur `TrackV1`。
3. **Mac companion 思路**：本地进程持有操作系统权限，云端/Murmur 只发严格白名单命令。
4. **深链试验**：把 `orpheus://...listenTogether` 当成受控 spike 的候选，不把它当正式 API。
5. **最小 Cookie 白名单与文件权限检查**：若仅做个人研究原型，可以参考 `MUSIC_U`/`__csrf` 的隔离方式。
6. **安全文案**：写操作必须用户确认，不提供音频下载、会员绕过和私聊自动化。

### 7.2 不应直接复用到正式产品

1. **硬编码私有 URL Scheme**：版本变化无兼容承诺，也没有回调确认。
2. **JXA 菜单遍历**：脆弱、会激活 App、需要高权限，无法移植到 iOS/Android。
3. **macOS 私有 MediaRemote framework**：全局命令且不是房间控制，分发和长期兼容风险高。
4. **Cookie 抽取与 EAPI 仿客户端协议**：不是官方 OAuth/SDK；不应成为邀请制移动产品的账号基础设施。
5. **静态 capability 对象**：不能用来驱动真实产品状态。
6. **“打开成功”当“业务成功”**：正式实现必须有房间创建/加入/播放的可验证回执。
7. **远程保存网易 Cookie**：即使静态加密，仍扩大账号会话攻击面；Murmur 不需要为了 Mac 遥控这么做。

## 8. 两档实施方案

### 8.1 A 档：Mac companion prototype

**产品定义：** “Murmur 和你围绕同一次网易云播放聊天”，不是“AI 作为网易云好友进入一起听房间”。

架构建议：

```text
Murmur 对话/选曲
       │ TrackV1 + 白名单命令
       ▼
Mac 本地 companion ── open / JXA / MediaRemote ──► 官方网易云 Mac App
       │
       └── 仅回传命令结果与有限状态，不回传 Cookie/音频
```

首版只做：

- 搜索/歌曲详情生成聊天卡片；
- 用户点击“用网易云播放”，打开官方歌曲页；
- 用户明确操作后播放/暂停/下一首；
- Murmur 根据自己选择的 `track_id` 和本地命令事件陪聊；
- “打开一起听”只作为高级跳转，让用户自行邀请真实好友。

必须在 UI 上如实写：

- “正在控制你 Mac 上的网易云音乐”；
- “Murmur 没有加入网易云一起听房间”；
- “Murmur 根据歌曲信息和播放操作陪伴，并没有接收完整音频”。

**风险门槛：** 只在个人/受控测试机使用；不上传 Cookie；默认不用歌单 EAPI；不承诺跨网易云版本稳定。

### 8.2 B 档：iOS/Android 移动正式产品

不要从本仓库的 UI 自动化继续外推。可选路线只有两类：

1. **外跳官方网易云**：Murmur 发歌曲卡和分享链接，用户在网易云 App 中播放/发起一起听。Murmur 不控制房间，只保留聊天陪伴。这是低集成、低承诺路线。
2. **自建 Murmur 一起听会话**：沿用 Murmur 的歌曲卡和播放状态，在 Murmur 内建立自己的会话同步；音乐来自已有合规播放器提供方。若必须使用网易能力，应向网易云音乐/TME 类版权方询证曲库与播放授权，或评估网易云信官方双人房方案，但需分别解决曲库版权、账号、付费和 SDK 商务准入。

若产品要求“用户继续用网易云消费 App，而 AI 作为第二位房间成员”，在网易提供书面合作接口之前应保持 **No-Go**，不以逆向房间协议、Cookie 自动化或第二账号机器人绕过。

## 9. 最小 PoC 设计与终止条件

### 9.1 推荐 PoC：验证 Mac companion，而非伪装成房间 PoC

范围：1 台 Mac、1 个用户自己的网易云官方客户端、固定的测试歌单；不导入 Cookie，不测试账号写入。

步骤：

1. `search -> detail -> TrackV1`，在 Murmur 测试聊天中显示歌曲卡；
2. 用户点击卡片，companion 打开官方歌曲页；
3. 用户手动开始播放，Murmur 发送播放/暂停/下一首白名单命令；
4. 连续 30 分钟记录命令成功率、焦点抢占、权限失败与客户端版本；
5. 单独验证一起听 URI 只作为“能否打开邀请 UI”的兼容实验，不宣称创建房间；
6. 断开 companion 后确保 Murmur 聊天和现有 Audius 功能不受影响。

### 9.2 明确不做

- 不导入或上传 `MUSIC_U`；
- 不抓包、逆向房间协议或复用私有聊天接口；
- 不自动选择好友、发送邀请或操作第二个网易账号；
- 不抓取播放音频；
- 不把 `open` 命令成功当作房间成功；
- 不将 Mac 结果推断成 iOS/Android 可行。

### 9.3 通过条件

- 搜索卡片解析稳定，旧 Murmur 客户端仍能文本降级；
- 用户每次外部播放或控制都有明确操作；
- companion 权限、断连和客户端未安装都有清晰状态；
- 30 分钟内基础控制成功率达到原型要求，且不会控制错误的媒体 App；
- 账号 Cookie、音频和歌词不进入日志、模型长期记忆或服务端播放状态。

### 9.4 终止条件

出现任一项就停止把该技术作为产品路线：

- `orpheus://` 在当前官方客户端无效或无稳定行为；
- MediaRemote 命令会控制到其他 App，无法可靠归属网易云；
- JXA 权限或焦点行为破坏 Murmur 核心体验；
- 需要导入 Cookie 才能完成基础播放；
- 需求重新要求“AI 必须是网易云一起听房间成员”；
- 网易书面条款或审核意见不允许该类第三方控制。

## 10. 最终决策

### Go

- 把仓库当成**源码参考**，提取歌曲标准化、MCP 工具边界和 Mac 本地 adapter 思路；
- 做一个不接账号 Cookie、不触碰房间私有协议的 Mac companion spike；
- 保留“打开官方一起听邀请页”作为用户手动动作，不把它包装成 AI 入房。

### No-Go

- 直接把这个 MCP 接进 Murmur 后宣称“AI 已经和用户用网易云一起听”；
- 用 `synchronizedPlayback: true` 作为房间同步已实现的证据；
- 在 iOS/Android 上承诺遥控网易云官方 App；
- 依赖 Cookie/EAPI 和客户端内部房间接口做公开、长期或商业能力；
- 把社区逆向的 create/check/heartbeat/play-command 链路当成正式产品 API；
- 让模型或服务端持有用户网易 Cookie、音频流或未最小化的个人歌单数据。

**最终判断：这个仓库把“AI 能帮你操作网易云”向前推进了一步，但距离“AI 作为网易云一起听参与者”仍缺完整且最关键的一层。** 对 Murmur 最现实的价值是 Mac 侧陪听原型和歌曲工具参考，不是移动端一起听方案。

## 11. 验证记录与限制

- 锁定提交：`0e27816d6ad8dac59ae54fcfc71b69cb5a41171b`。
- 全仓库检查了工具注册、网易适配器、歌单/EAPI、Cookie 导入、native helper、平台文档和相关测试。
- 静态检索未发现房间 ID、会话 ID、邀请发送、聊天、心跳、队列、WebSocket、音频流或房间状态实现。
- 未安装依赖。直接运行 `npm test` 时，20 个测试中 18 个通过；2 个涉及 MCP HTTP/Personal Server 的测试文件因为本地不存在 `@modelcontextprotocol/server` 依赖而在加载阶段失败。这个结果不是产品失败，也不是全套测试通过。
- 已通过的“一起听”测试只覆盖 URI 构造与静态 capability JSON，不覆盖真实网易云客户端、账号、房间、邀请或同步播放。
- 未执行登录、Cookie 导入、播放器启动、深链、辅助功能控制或任何网易账号写操作，因此客户端版本兼容性仍属未知。
