# 邮箱身份、账号额度与历史同步

> 状态：开发验收中｜适用：邮箱身份、账号额度与历史同步｜核验：2026-09-21｜依据：本任务本地实现；未部署生产、未执行真实邮件或真机验收。

用户已确认 [Figma 原型](../design/motion-email-sync-2026-09-21.md)。本次增加邮箱身份、每日 50 次主动 AI 请求额度和用户主动开启的独立历史同步；一起听开关与白名单保持现有行为。

## 本地配置与上线边界

邮箱注册默认关闭。测试环境通过本地私有环境配置提供：

```dotenv
MURMUR_EMAIL_REGISTRATION_ENABLED=true
RESEND_API_KEY=<本地配置，不提交>
MURMUR_EMAIL_OTP_SECRET=<至少32字符的独立随机秘密，不提交>
```

发件人为 `Murmur <noreply@auth.sakuramu.edu.kg>`。OTP 摘要使用独立秘密做 HMAC，不能复用开发设备 token。Resend 使用 HTTPS API、纯文本验证码和发送幂等键；域名验证时需关闭打开/点击追踪。接入依据：[Resend Send Email](https://resend.com/docs/api-reference/emails/send-email)。

真实联调需用户本地 Resend 配置、Cloudflare 对 `auth.sakuramu.edu.kg` 的 DNS 操作条件、QQ/163/Gmail/Outlook 测试邮箱，以及两台可签名安装的 iOS 设备。DNS 记录必须来自实际 Resend 验证结果，只追加指定子域名记录。本轮不购买套餐、不部署生产。

## 身份接口

| 接口 | 用途 |
| --- | --- |
| `POST /v1/email/codes` | 邮箱与待注册设备 key 绑定的验证码 |
| `POST /v1/email/verify` | 兑换 5 分钟、单次使用的邮箱凭证 |
| `POST /v1/enrollments` | 支持 `email_grant` 或旧 `invite_code`，仍先验证设备证明 |
| `POST /v1/enrollments/recover` | 注册响应丢失后，以已注册密钥签名恢复身份 |
| `GET /v1/account/email` | 当前账号绑定邮箱 |
| `POST /v1/account/email/codes`、`verify` | 已登录设备发起的绑定验证 |
| `POST /v1/account/email` | 绑定邮箱，保留原 user_id；冲突不合并 |

iOS 使用发送响应中的 `expires_in` 和 `resend_after` 呈现过期与重发状态；重新发送成功后清空旧验证码和旧错误。

验证码 6 位、10 分钟有效、最多 5 次验证、60 秒重发。每邮箱每小时 10 封、每来源每小时 30 封、全局每天滚动 24 小时 1000 封作为初始发送保护。来源采用服务器看到的请求地址，不自行信任客户端转发头；反向代理需按可信代理边界传递来源，避免所有用户共用代理出口额度。

绑定验证码不能通过匿名验证接口兑换。失败尝试必须提交事务后返回错误；成功邮箱凭证的消费与用户/设备写入在同一事务。三个有效设备上限与 Release App Attest/HTTPS 要求保持。

## 每日额度

`GET /v1/account/quota` 返回 `limit`、`used`、`remaining`、`resets_at` 和时区。新旧账号相同，北京时间零点重置。

入站 moment 在创建事务中原子预占，幂等命中不重复占用。失败状态通过数据库触发器释放，覆盖普通失败与 worker 崩溃重领失败；成功的安静响应仍计数。主动推送、认证、查询与同步不计数。额度耗尽返回 HTTP 429 / `daily_quota_exhausted`。额度账本独立于 moment 外键级联，清理 moment 不能退还成功请求；删除账号会级联删除账本。

## 同步契约

独立表 `app_sync_settings`、`app_sync_records` 保存账号历史，未复用 AI 记忆。压缩预览图以账号记录内的 BLOB 保存，便于与记录删除、账号擦除原子级联。原图不进入同步表。

| 接口 | 用途 |
| --- | --- |
| `GET/PUT /v1/sync/settings` | 开关、聊天和照片房间删除版本 |
| `GET /v1/sync/{chat|archive}/records?cursor=N` | 每页最多 100 条的增量记录 |
| `POST /v1/sync/{chat|archive}/records` | 携带 generation 的幂等上传 |
| `PUT/GET /v1/sync/{kind}/preview/{record_id}` | 受设备鉴权保护的压缩预览 |
| `DELETE /v1/sync/{kind}/records` | 携带已见 generation 的全设备删除 |

上传字段仅接受稳定 ID、作者、文字、发送日期、存档日期、moment/reply 标识和歌曲卡片元数据。不同记录 ID 不按文字相似度合并。相同服务端回复 ID 在不同设备观察到的时间可能不同，保留第一次同步日期，不生成第二条记录。

新客户端先拉取后上传，100 条一页读取本机 SQLite；只上传已发送内容。预览图在后台线程缩小、重编码，服务器再重编码去元数据。上传记录后才上传关联预览，完成预览后才将该本机记录视为可清理缓存的副本。下载保留本机已有的重试状态和额外本地关联。

每种历史独立 generation。全设备删除递增 generation，旧版本上传被拒绝；重复删除旧版本不会删除之后的新记录。本机同步删除留下 ID 标记，拒绝迟到的旧内存快照。清理缓存另有可恢复的逐 ID 标记，只有实际同步下载才能恢复副本；未同步内容不会删除。关闭同步保留云端记录，后台仍可读取删除版本执行已授权的全设备删除。

## 迁移与验证

数据库新增表为幂等初始化，不重命名现有数据位置。iOS 延用本机聊天与照片房间两个 SQLite/图片目录，另写受完整文件保护的 `sync-state.json` 保存游标、删除版本和已同步 ID；不改旧 JSON 迁移来源和消息日期。历史迁移可中断重试。正式上线前应对测试数据库副本做迁移回滚演练，回滚时保留新增表与媒体，不删除新数据。

测试入口：`scripts/check/run_tests.py` 的 `test_app_email.py`、`test_app_quota.py`、`test_app_sync.py`、`test_app_account_api.py`，以及原有 AppStore/API 回归；iOS 的同步缓存/删除测试与新增邮箱、同步确认 UI 测试。最终结果与真机未验收项记录在设计文档的开发验收记录中。

隐私清单已补充邮箱地址：关联账号、仅用于 App 功能、不用于追踪。键名依据 [Apple Collected Data Type](https://developer.apple.com/documentation/bundleresources/app-privacy-configuration/nsprivacycollecteddatatypes/nsprivacycollecteddatatype)。
