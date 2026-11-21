# 网易云 Phase 0 中国大陆出口的零软件费方案

> 状态：调研结论｜适用：Murmur 个人隔离网易云 Phase 0 的网络选型｜核验：2026-09-07｜依据：Tailscale、ZeroTier、WireGuard、工业和信息化部及网易云音乐官方资料；不代表网易云、网络运营商或监管机构已授权该方案

## 结论

不存在一个值得用于机器人账号和生产 VPS 的“免费中国大陆 VPN 服务商”方案。公共免费代理、共享
VPN、来源不明的订阅节点无法审计出口运营者、流量保留、账号滥用和 IP 污染，且通常不承诺中国
大陆出口，因此不纳入候选。

如果已经有一台位于中国大陆、可长期联网的自有设备（Linux 小主机、NAS、软路由或树莓派），
可以做到**零软件许可费**：

1. **Tailscale Personal + 自有大陆 Exit Node（推荐先验证）**：配置最省事，官方明确支持出口
   节点和失联 fail-close；个人版当前免费。缺点是依赖 Tailscale 控制面和跨境链路，在中国大陆
   的可达性、直连率和时延没有官方 SLA。
2. **ZeroTier Personal + 自有大陆 NAT 网关**：免费额度足够两台设备，官方有完整的默认路由
   和 Linux NAT 操作说明；比 Tailscale 更手工，故障关闭和 DNS 防泄漏需自行实现并验收。
3. **原生 WireGuard + 自有大陆 NAT 网关**：软件开源免费、控制面最少，并可用 Linux 网络命名
   空间把 Phase 0 做到强隔离；但 NAT、密钥轮换、保活、路由和监控全部由 Murmur 维护，且没有
   中继兜底。

三种方案都只是让网易云看到中国大陆设备的公网出口 IP；它们不会把美国 VPS 本身“搬到中国”，
也不保证解决账号注册地、会员资格、设备指纹、风控或曲目授权造成的限制。**出口变化不等于版权
许可或网易云授权。**

仓库[现行 Phase 0 运行手册](../operations/netease-listen-together-poc-runbook.md)已把“版权限制绕过”
列为禁止事项。本文不修改这条边界，也不构成部署建议；下面的验证顺序只有在网易云/网络运营商
许可和具体合规边界先得到确认后才可执行。若目标只是规避地区版权限制，应保持现有的共同可播
过滤与 `counterpart_rights_unavailable` 回退，不部署中国出口。

## 共同前提与安全边界

### 必须自备中国大陆出口

Tailscale 的出口节点会把公共互联网流量交给指定设备，并让目标网站看到该出口节点的公网地址；
ZeroTier 和 WireGuard 也只是把流量送到自建网关再做 NAT。三者本身都不赠送中国大陆公网出口。

因此“免费”仅指：

- 不付 VPN 软件许可费；
- 复用用户已有的中国大陆宽带、设备和电费；
- 不包含购买大陆云主机、固定公网 IP、跨境专线或运维时间。

若没有现成的中国大陆设备和网络，这三个方案都不是零成本方案，也没有找到可审计、官方承诺
大陆出口的免费服务可替代它们。

### 只导流 Phase 0，不改整台 VPS 默认出口

当前实验服务由专用用户 `murmur-netease-poc` 运行，正式服务和 SSH 不应一起走隧道。目标数据流
应是：

```text
murmur-netease-phase0
  -> 专用 UID/cgroup 策略路由或独立 network namespace
  -> 加密隧道
  -> 用户自有的中国大陆网关
  -> 中国大陆公网 NAT
  -> 网易云

Caddy / SSH / murmur-app-api / murmur-app-worker / murmur-web
  -> 保持美国 VPS 原出口
```

实施时还要保留 worker 到 Phase 0 的本机能力接口。若把 Phase 0 移入独立 network namespace，
`127.0.0.1` 不再与宿主机共享，需要通过仅宿主可达的 veth 地址或 Unix socket 连接；更小的首版
可以按 `murmur-netease-poc` UID/cgroup 标记**非回环**出站流量并使用独立路由表。

无论采用哪一种隧道，都应加两层失败关闭：

- 路由层：该 UID/cgroup 只允许回环、隧道握手和隧道接口出站；隧道消失时不得回落到美国公网；
- 应用层：出口国家、隧道握手或网易云可播探针不满足时，Phase 0 返回不可用，房间保持明确错误，
  不重试到账号风控。

## 方案一：Tailscale Personal + 大陆 Exit Node

### 官方事实

- [Tailscale 当前定价页](https://tailscale.com/pricing)显示 Personal 为 `$0`、长期免费，最多
  6 个用户，并包含几乎全部功能；本场景只需 VPS 和一台大陆设备。
- [出口节点文档](https://tailscale.com/kb/1103/exit-nodes)明确写明 Exit Nodes 对所有套餐开放，
  可让设备通过指定节点的 `0.0.0.0/0`、`::/0` 路由公共互联网流量；出口设备可为 Linux、
  macOS、Windows、Android、iOS 或 tvOS。
- 同一文档明确：连接器密钥过期时，路由继续保留但变为不可达，即 fail-close，以避免流量泄漏
  到不可信网络。
- [DERP 官方文档](https://tailscale.com/docs/reference/derp-servers)显示亚洲公共中继包含香港、东京、
  新加坡，但未列中国大陆；通常先尝试端到端直连，无法直连时才使用 Peer Relay 或 DERP。

### 建议拓扑

- 大陆侧：优先用长期通电的 Linux 小主机、NAS 或软路由作为 Exit Node；不建议把 Android 当长期
  网关，因为 Tailscale 官方说明 Android 出口节点仍是 userspace routing，性能可能不足。
- VPS 侧：仅让 Phase 0 的隔离网络环境选择该 Exit Node；不要在宿主机全局执行默认出口切换。
- 访问控制：ACL 只允许 VPS 使用该指定出口节点；大陆设备不开放到 Murmur 正式目录或数据库。

### 免费、固定出口与故障行为

| 项目 | 判断 |
|---|---|
| 软件/套餐费 | Personal 当前为 0 美元，额度足够 |
| Linux 支持 | 官方支持 Linux 客户端和 Linux Exit Node |
| 是否需要大陆设备 | 是；Tailscale 不提供大陆出口 IP |
| 是否固定出口 | 取决于大陆宽带；普通家庭宽带公网 IP 变化后，网易云看到的出口也会变化 |
| 隧道故障 | Tailscale 连接器路由有官方 fail-close；仍须为 Phase 0 增加宿主机级防泄漏规则 |
| 中国大陆可用性 | 官方无大陆节点或可用性 SLA，必须在目标宽带实测直连/DERP、抖动和掉线 |

### 适用判断

这是最适合先做 24 小时小流量验证的方案：上线和撤回成本最低，故障行为也最清楚。它适合验证
“大陆出口是否真的改变可播集合”，不应直接作为长期生产结论。

## 方案二：ZeroTier Personal + 大陆 NAT 网关

### 官方事实

- [ZeroTier 当前定价页](https://www.zerotier.com/pricing/)的 Personal 套餐为 Free Forever，
  当前表格列出 10 台设备、1 个网络、1 个管理员，足够本场景的 VPS 和一台大陆网关。
- [VPN Exit Node 文档](https://docs.zerotier.com/exitnode/)给出了完整全隧道模式：在 Linux
  出口节点启用 IPv4 forwarding 和 iptables MASQUERADE，在 ZeroTier Central 添加
  `0.0.0.0/0 via <gateway>`，客户端再开启 `allowDefault=1`。
- 同一文档区分动态和固定公网地址：动态地址使用 MASQUERADE；若拥有固定公网 IP，可用 SNAT。
- [物理网络路由文档](https://docs.zerotier.com/route-between-phys-and-virt/)说明一台运行 Linux
  和 iptables 的 PC、VM 或树莓派即可承担 NAT/masquerade 网关。

### 建议拓扑

- 大陆 Linux 设备加入一个只包含它和 VPS 的 ZeroTier 网络，并作为默认路由/NAT 网关。
- VPS 只在 Phase 0 的独立路由域中开启 `allowDefault`，宿主其他服务不接受 ZeroTier 默认路由。
- 使用 Flow Rules/宿主防火墙限制两端只传 Phase 0 出站所需流量，不把大陆 LAN 暴露给 VPS。

### 免费、固定出口与故障行为

| 项目 | 判断 |
|---|---|
| 软件/套餐费 | Personal 当前永久免费；10 设备/1 网络足够 |
| Linux 支持 | 官方 Exit Node 示例就是 Linux + iptables |
| 是否需要大陆设备 | 是；ZeroTier 不提供大陆出口 IP |
| 是否固定出口 | MASQUERADE 跟随大陆 WAN 地址；只有自有固定公网 IP + SNAT 才是固定出口 |
| 隧道故障 | 官方教程没有承诺 fail-close；必须自行保持默认路由、加 nftables kill switch 并做拔线验收 |
| 中国大陆可用性 | 官方没有大陆连接质量或免费 SLA；必须实测直连、根节点/中继可达性和重连时间 |

### 适用判断

当 Tailscale 在目标中国宽带无法稳定直连，或希望更直接地控制默认路由和 NAT 时，可把 ZeroTier
作为第二个 A/B 方案。它并不天然比 Tailscale 更稳定，主要价值是另一套 NAT 穿透和控制路径。

## 方案三：原生 WireGuard + 大陆 NAT 网关

### 官方事实

- [WireGuard 官方说明](https://www.wireguard.com/)将其定义为通用、跨平台 VPN；最初进入 Linux
  内核，核心组件使用 GPLv2，其他组件按 MIT、BSD、Apache 2.0 或 GPL 等开源许可证发布，
  没有软件订阅费。
- [安装页](https://www.wireguard.com/install/)列出 Ubuntu/Debian 的 `apt install wireguard`
  及多个 Linux 发行版的官方安装方式。
- 官方概念示例使用 `AllowedIPs = 0.0.0.0/0` 作为全流量路由；[Quick Start](https://www.wireguard.com/quickstart/)
  说明 NAT/防火墙后的节点可用 `PersistentKeepalive` 保持映射，常见建议间隔为 25 秒。
- [网络命名空间文档](https://www.wireguard.com/netns/)说明 WireGuard 接口可以成为容器/namespace
  的唯一网络接口；在这种布局下，该空间访问网络的唯一途径就是 `wg0`，非常适合 Phase 0 的
  强制失败关闭。

### 建议拓扑

- 美国 VPS 作为有稳定公网地址的 WireGuard 端点。
- 中国大陆网关主动连接 VPS，并使用 keepalive 保持 NAT 映射；网关对隧道流量做大陆 WAN
  MASQUERADE。这样大陆侧通常不要求可被公网主动连接，但仍依赖当地网络允许稳定 UDP 出站。
- Phase 0 进入只含 WireGuard 接口和私有 veth/IPC 的 namespace；主机其他服务保持原网络。
- 单独处理 DNS，禁止 Phase 0 通过宿主美国出口解析或连接；密钥只放在 root 可读配置中，不进入
  Murmur 仓库或 Phase 0 会话目录。

### 免费、固定出口与故障行为

| 项目 | 判断 |
|---|---|
| 软件/套餐费 | 开源软件，无订阅费 |
| Linux 支持 | 原生支持，Linux 内核路径成熟 |
| 是否需要大陆设备 | 是；WireGuard 不提供服务器或大陆出口 |
| 是否固定出口 | 取决于大陆 WAN；动态公网 IP 不影响由大陆端主动连 VPS，但网易云看到的 IP 会变化 |
| 隧道故障 | WireGuard 本身不负责策略回退；namespace 仅有 `wg0` 时可结构性 fail-close，否则必须加防火墙 |
| 中国大陆可用性 | 没有厂商中继；UDP 被限速/阻断时会直接不可用，需要实测并准备停用而非切回美国出口 |

### 适用判断

WireGuard 是长期可控性最好、同时也是运维责任最大的方案。只有在 Tailscale/ZeroTier 验证大陆
出口确实解决目标歌曲、并且用户愿意维护网关后，才值得把它做成可审计的正式实验网络。

## 三方案比较

| 维度 | Tailscale Personal | ZeroTier Personal | 原生 WireGuard |
|---|---|---|---|
| 增量软件费 | 0 | 0 | 0 |
| 当前免费额度 | 6 用户；设备额度足够 | 10 设备、1 网络、1 管理员 | 无厂商额度 |
| 大陆出口来源 | 自有设备/宽带 | 自有设备/宽带 | 自有设备/宽带 |
| NAT 穿透/中继 | 自动；有 DERP/Peer Relay | 自动；依赖 ZeroTier 基础设施 | 自管；无中继 |
| 默认故障关闭 | 官方明确连接器 fail-close | 需自建并测试 | namespace/防火墙自建 |
| 部署复杂度 | 低 | 中 | 高 |
| 第三方控制面依赖 | 有 | 有 | 无 |
| 推荐顺序 | 1 | 2 | 3 |

## 合规与平台条款边界

### 中国跨境联网规定

[工业和信息化部《工业和信息化部关于清理规范互联网网络接入服务市场的通知》](https://www.miit.gov.cn/jgsj/xgj/wjfb/art/2020/art_ac2095b32d054e22a03e8154c3a44d50.html)
规定，未经电信主管部门批准，不得自行建立或租用专线（含 VPN）等信道开展跨境经营活动；基础
电信企业出租的国际专线仅供用户内部办公专用，不得用于连接境内外数据中心或业务平台开展电信
业务经营活动。

[工信部答记者问（网信办转载）](https://www.cac.gov.cn/2017-01/26/c_1120381529.htm)说明，通知
主要规范未经许可、无国际通信业务经营资质的企业或个人利用专线/VPN 私自开展跨境电信业务经营；
外贸、跨国企业办公自用可以向依法设置国际通信出入口局的电信经营者租用专线。

Murmur 不是在销售 VPN/专线，但本方案也不是典型“企业办公自用”，而是把境外数据中心的应用
流量通过境内设备访问第三方业务平台。仅凭上述两份公开文件无法确认它是否合规；生产化之前应
向大陆宽带运营商和熟悉电信监管的律师确认，不应把个人 PoC 推断为可商业部署。

### 网易云服务条款

[网易云音乐服务条款](https://y.music.163.com/g/yida/36a81250504747a19283b29e4e9ff38c)
当前页面标注更新、生效日期为 2025-09-03。条款要求：

- 未经网易同意，不得将网易云音乐软件或服务用于销售或其他商业用途；
- 不得通过未经网易开发、授权或认可的第三方兼容软件、系统或插件登录/使用服务；
- 不得利用规则漏洞、系统漏洞或其他违反诚实信用原则的方式使用服务；
- 违反规则时，网易可暂停或终止部分/全部服务并封禁、注销账号。

当前 Phase 0 使用的是非官方协议研究工具。因此，即使中国大陆出口技术上减少了“地区版权限制”，
也没有改变未经授权集成的性质，不能据此扩大灰度、公测、App Store 分发或商业用途。

## 推荐的验证顺序

以下步骤是**合规与平台许可确认后的条件性技术验证**，不是当前生产操作授权。

1. **先证明问题确实由出口 IP 造成。** 用 Tailscale Personal 和一台自有大陆 Linux 网关，仅对
   一次性隔离测试进程导流；对同一机器人账号比较美国/大陆出口下的 `check_music` 结果。只记录
   成功率和耗时，不记录歌曲 ID、账号、房间号或公网 IP。
2. **再做 24 小时链路基准。** 记录隧道握手新鲜度、直连/中继、RTT、丢包、Phase 0 请求
   P50/P95、断线恢复时间；拔掉大陆网关，确认 Phase 0 失败关闭且正式服务/SSH 不受影响。
3. **只有可播集合显著改善且 P95 仍满足产品目标，才比较 ZeroTier。** 若两者都不稳定，不用
   公共免费代理兜底，直接保持现有“双方版权不一致”产品行为。
4. **WireGuard 只作为长期自管候选。** 在合规确认、网关可长期维护、密钥轮换和故障演练都具备后，
   才考虑替换托管控制面。

## 未确定事项

- Tailscale、ZeroTier 均未在上述官方资料中承诺中国大陆免费用户的可用性、直连率或 SLA；必须
  用实际大陆宽带和当前 VPS 实测。
- 中国大陆宽带是否给公网 IPv4、是否为 CGNAT、IPv6/UDP 策略、出口 IP 变化频率取决于用户线路；
  三个厂商文档不能替代现场检查。
- 网易云是否只按出口 IP 判断曲目可播，还是同时采用账号地区、手机号、会员、设备、Cookie、
  风控等信号，官方服务条款没有披露；只能用专用可丢弃账号做最小化 A/B 验证。
- 公开监管文件不足以对本具体拓扑给出法律结论；调研内容不是法律意见。
