# 服务端多平台改造：认证抽象与 push 层

> 状态：归档｜适用：历史追溯｜归档核验：2026-08-28｜依据：重组前文件快照。
> 以下保留当时描述、路径和判断，不代表当前能力、部署状态或新开发要求。现行说明见 [文档导航](../README.md)。

目标：让 `murmur.app_api` 同时接受 iOS 与 Android 客户端，且 **iOS 侧行为零变化**。
本文档只覆盖服务端；Android 客户端代码不在范围内。

---

## 0. 一个关键发现

先说结论，它决定了整个改造的规模：**Apple App Attest 的两步，在 Android 上是两个
不同的机制，但只有第一步需要重写。**

| | iOS | Android |
|---|---|---|
| 注册时证明"这是真设备上的真 App" | App Attest attestation（CBOR/WebAuthn，Apple 根证书） | **Key Attestation** 证书链（X.509，Google 硬件认证根）+ 可选 Play Integrity 一次性判定 |
| 每个请求证明"还是那台设备" | App Attest assertion（硬件 P-256 密钥签名） | **同一把 Keystore P-256 密钥签名** |

第二行是一样的。`AppAttestVerifier` Protocol（[app_auth.py:80](../murmur/app_auth.py)）
的两个方法签名基本不用改，改的是**谁来实现它**。

差异只有一处需要设计决定：App Attest 的 assertion 把一个单调计数器塞在
`authenticatorData` 里（[app_auth.py:334](../murmur/app_auth.py)），Android 的
Keystore 签名没有这个东西。但重放保护其实**已经由单次使用的 challenge 提供了**
——`store.consume_challenge` 是一次性的。计数器是第二道锁。所以 Android 侧让
`verify_assertion` 原样返回 `previous_counter`（不推进）即可，counter 列保留但对
Android 恒为 0。

---

## 1. 认证层

### 1.1 Protocol 改名与泛化

```python
# app_auth.py
@dataclass(frozen=True)
class AttestationResult:
    public_key: bytes
    receipt: bytes | None
    counter: int
    environment: str
    platform: str          # 新增："ios" | "android"


class DeviceAttestor(Protocol):        # 原 AppAttestVerifier
    platform: str

    def verify_attestation(
        self, attestation: bytes, *, key_id: str, client_data_hash: bytes
    ) -> AttestationResult: ...

    def verify_assertion(
        self, assertion: bytes, *, public_key: bytes, client_data_hash: bytes,
        previous_counter: int,
    ) -> int: ...
```

`AppleAppAttestVerifier` 只需加一行 `platform = "ios"`，其余 290 行原封不动。
`tests/test_app_auth.py:40` 的 `FakeVerifier` 同理加一行。

### 1.2 AppAuthenticator 变成注册表

现在是单一 `self.verifier`（[app_auth.py:365](../murmur/app_auth.py)），改成
`dict[str, DeviceAttestor]`：

```python
class AppAuthenticator:
    def __init__(self, settings, store, attestors: Mapping[str, DeviceAttestor] | None = None):
        ...
        self.attestors = dict(attestors or {})
        if settings.production and not self.attestors:
            AppleAppAttestVerifier.ensure_available()
            self.attestors["ios"] = AppleAppAttestVerifier(...)
            if settings.android_enabled:
                self.attestors["android"] = AndroidKeyAttestor(...)
```

两个调用点的取用方式不同，这是要点：

- **`enroll()`** — 平台由客户端在请求体里声明（新增 `platform` 字段），服务端据此
  选 attestor。声明错了会因为证书链验不过而 fail closed，不需要额外校验。
- **`authenticate()`** — 平台**不由客户端声明**，从 `store.auth_key(key_id).platform`
  读。这点很重要：per-request 路径上任何客户端可控的平台选择都是降级攻击面。

### 1.3 AndroidKeyAttestor

新文件 `murmur/app_attest_android.py`，与 `AppleAppAttestVerifier` 平级。

`verify_attestation(chain_der, *, key_id, client_data_hash)` 要做的事：

1. 解析 DER 证书链，逐级验签，验时间有效性 —— **可直接复用**
   `_verify_certificate_signature` / `_certificate_time_valid` / `_verify_chain`
   的骨架，只把根换成 Google 硬件认证根。
2. 取叶证书的 key attestation 扩展，OID `1.3.6.1.4.1.11129.2.1.17`。
   现有的 `_der_primitive_values`（[app_auth.py:135](../murmur/app_auth.py)）
   是个有界 DER 遍历器，可以用来取 challenge，但这个扩展结构比 Apple 的
   nonce 复杂得多（`KeyDescription` SEQUENCE，含 `attestationChallenge`、
   `softwareEnforced`、`teeEnforced` 两个 AuthorizationList）。建议写一个专门的
   最小解析器，只取四个字段，不做通用 ASN.1。
3. 校验：
   - `attestationChallenge` == `client_data_hash`（对齐 Apple 的 nonce 语义）
   - `attestationSecurityLevel` ∈ {TrustedEnvironment, StrongBox} → 否则视为
     development
   - `teeEnforced.attestationApplicationId` 里的包名 == 配置的包名，签名证书
     SHA-256 ∈ 配置的白名单（**这是 Android 版的 `app_id` 校验**）
   - `teeEnforced.rootOfTrust.verifiedBootState` == Verified → production
   - `purpose` 含 SIGN，`algorithm` == EC，`digest` 含 SHA-256
4. 叶证书公钥必须是 P-256，导出 SPKI DER —— 和 Apple 路径的返回格式完全一致
   （[app_auth.py:313](../murmur/app_auth.py)），下游 `verify_assertion` 直接通用。
5. `key_id`：Android 没有"key_id 是公钥 hash"这个规则。**建议服务端自己定义**
   `key_id = base64url(sha256(SPKI))`，由服务端计算并与客户端声明的比对，保持与
   iOS 相同的不变量（[app_auth.py:293](../murmur/app_auth.py) 那条校验）。

`verify_assertion(signature, *, public_key, client_data_hash, previous_counter)`：
纯 ECDSA/SHA-256 验签，签名对象就是 `client_data_hash`（没有 authenticatorData
前缀），返回 `previous_counter`。约 15 行。

**吊销检查**：Google 在 `https://android.googleapis.com/attestation/status` 发布
被吊销的密钥序列号。只在 enrollment 时查一次，带本地缓存 + 失败策略配置
（`fail_closed` 默认 True）。这是一次网络调用，不在 per-request 路径上。

### 1.4 environment 的语义对齐

iOS 用 AAGUID 区分 development/production。Android 没有对等物，用上面第 3 条
推导：verifiedBoot=Verified **且** 签名证书是 release 证书 → `production`，
否则 `development`。`/v1/enrollments` 里那句 `environment != result.environment`
的一致性检查（[app_api.py:524](../murmur/app_api.py)）逻辑不变。

---

## 2. 数据库

两列新增 + 一列改名，全部走现有 `_migrate()` 的
`PRAGMA table_info` + `ALTER TABLE` 模式（[app_store.py:286](../murmur/app_store.py)）：

```sql
ALTER TABLE app_attest_keys ADD COLUMN platform TEXT NOT NULL DEFAULT 'ios';
ALTER TABLE app_devices     ADD COLUMN platform TEXT NOT NULL DEFAULT 'ios';
ALTER TABLE app_devices     RENAME COLUMN apns_token TO push_token;
```

- 存量行全是 iOS，`DEFAULT 'ios'` 直接回填，无需数据迁移脚本。
- `app_devices.platform` 是对 `app_attest_keys` 的冗余，但 push 投递查询
  （[app_store.py:1156](../murmur/app_store.py) 等）只扫 `app_devices`，
  不做冗余就得每条加一个 JOIN，不划算。enrollment 时一次写入，之后不变。
- 改名影响 `app_store.py` 内约 15 处 SQL，全部集中在该文件。SQLite ≥3.25 支持
  `RENAME COLUMN`；`_migrate()` 里按列名存在性做幂等判断。
- `push_token` 上的 `UNIQUE` 保留 —— 跨平台 token 不可能碰撞。

---

## 3. Push 层

### 3.1 Provider Protocol

`APNsProvider.send` 的签名（[app_push.py:162](../murmur/app_push.py)）已经是
平台无关的，直接提取：

```python
class PushProvider(Protocol):
    platform: str
    def send(self, device_token: str, *, moment_id: str, preview: str) -> PushResult: ...
```

`APNsProvider` 加 `platform = "ios"`，其余不动。

### 3.2 ProactiveScheduler 按平台路由

```python
class ProactiveScheduler:
    def __init__(self, store, providers: Mapping[str, PushProvider], generator, *, lock_root=None):
        self.providers = dict(providers)
```

`deliver_pending` 里那次调用（[app_push.py:325](../murmur/app_push.py)）改成：

```python
provider = self.providers.get(item["platform"])
if provider is None:
    self.store.retry_push(item["moment_id"], item["device_id"], now=now)
    continue
result = provider.send(item["push_token"], ...)
```

没有对应 provider 时走 **retry 而不是 mark_push_dead**：那是运维配置缺失，不是设备
失效，判死会让补上配置后的历史投递永远发不出去。`retry_push` 的退避封顶 1 小时，
且 24 小时的 proactive 过期会兜底清理，所以重试是有界的。

store 的三个查询要把 `platform` 和 `push_token` 一起 SELECT 出来
（`due_push_deliveries` / `pending_push_delivery` / `push_devices`）。
重试、dead、`on_invalid_token` 回调这套持久化状态机完全不用改 —— 它已经是
按 `(moment_id, device_id)` 而不是按平台组织的。

### 3.3 FCMProvider

新文件 `murmur/app_push_fcm.py`。

- 端点：`POST https://fcm.googleapis.com/v1/projects/{project_id}/messages:send`
- 鉴权：service account → OAuth2 access token。**不引入 `google-auth`**：
  用 `cryptography` 自己签 RS256 JWT 断言，POST 到
  `https://oauth2.googleapis.com/token`（`grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`，
  scope `https://www.googleapis.com/auth/firebase.messaging`）。这与
  `_provider_token()` 现在自签 ES256 JWT 的做法（[app_push.py:127](../murmur/app_push.py)）
  是同一个套路，README 里"依赖表刻意维持得很短"的约束得以保持。
  token 有效期 1 小时 → 沿用 50 分钟缓存 + 到期刷新一次的模式。
- payload：

```python
{"message": {
    "token": device_token,
    "notification": {"body": text},
    "data": {"moment_id": moment_id},
    "android": {"priority": "HIGH", "collapse_key": moment_id[:64]},
}}
```

  4096 字节上限与 APNs 相同，`payload()` 里那个截断循环
  （[app_push.py:148](../murmur/app_push.py)）原样照搬。
  **同样不要把完整对话放进 push** —— 那条注释的约束对两个平台一致。
- 错误映射到现有 `PushResult`：

| FCM | 现有分支 |
|---|---|
| 200 | `PushResult(True, 200)` |
| 401 | 刷新 access token，重试一次（对应 `ExpiredProviderToken`） |
| 404 `UNREGISTERED` / 400 `INVALID_ARGUMENT` | `on_invalid_token` + `mark_push_dead` |
| 429 / 503 | `retry_push` |

- `HttpxAPNsTransport` 里的 `httpx.Client(http2=True)` 可以共用，Transport
  Protocol 提出来叫 `PushTransport` 即可。
- **日志**：[app_push.py:331](../murmur/app_push.py) 那条"异常里可能带含 token 的
  URL"的教训对 FCM 同样成立 —— FCM 的 token 在 body 里而不是 URL 里，但
  httpx 的异常仍可能带上请求内容，保持只记 `type(error).__name__`。

---

## 4. 配置

`AppSettings` 新增（[app_settings.py](../murmur/app_settings.py)）：

```
MURMUR_APP_ANDROID_ENABLED
MURMUR_APP_ANDROID_PACKAGE              # com.sakura.murmur
MURMUR_APP_ANDROID_SIGNING_DIGESTS      # release 签名证书 SHA-256，逗号分隔
MURMUR_APP_ATTEST_GOOGLE_ROOT_CA        # 路径，缺省用内嵌根证书
MURMUR_APP_ATTEST_REVOCATION_FAIL_OPEN  # 默认 false
MURMUR_APP_FCM_PROJECT_ID
MURMUR_APP_FCM_SERVICE_ACCOUNT_PATH
```

`validate()` 扩展：`android_enabled` 且 `production` 时，上述 package / digests /
FCM 两项必须齐全，缺一报错。`validate_apns()` 旁边加一个对称的 `validate_fcm()`。

注意 `apns_environment` 这个字段现在既表示 APNs 环境又参与 production 校验
（[app_settings.py:143](../murmur/app_settings.py)）——Android 侧不要复用它，
单独判定。

---

## 5. API 表面

| 端点 | 改动 |
|---|---|
| `POST /v1/auth/challenges` | 无。challenge 是平台无关的随机数 |
| `POST /v1/enrollments` | 请求体加 `platform`（缺省 `ios`，保持 iOS 客户端不用改）；`attestation` 字段对 Android 装证书链 |
| `PUT /v1/device` | `apns_token` → `push_token`（保留 `apns_token` 作为别名，iOS 客户端零改动）；**那条 hex 正则校验必须按平台分支** —— FCM token 不是 hex（[app_api.py:714](../murmur/app_api.py)） |
| 其余 9 个端点 | 无 |

请求头 `X-Murmur-Key-Id` / `X-Murmur-Challenge-Id` / `X-Murmur-Assertion` 名字
不变，Android 复用同一组头，只是 `X-Murmur-Assertion` 里装的是裸 ECDSA 签名。
服务端从 key_id 查平台，客户端不声明。

---

## 6. 实施顺序

### P0 · 先修一个定时炸弹测试（阻塞项）

在**未改动的 origin/main** 上，`tests/test_app_push.py` 已有两条红：

```
test_scheduler_creates_one_current_message_and_pushes_each_device   1 != 0
test_transient_push_failure_is_durable_and_replayed_after_restart   0 != 1
```

不是代码坏了，是测试写死了日期。根因：

- `ProactiveScheduler.run_once` 拿到注入的 `now`，转手传给 `deliver_pending(now)`、
  `ensure_schedules(now)`、`due_slots(now)`，**唯独 `expire_stale_proactive()` 没传**
  （[app_push.py:363](../murmur/app_push.py)），它内部用真实墙上时钟
  （[app_store.py:1273](../murmur/app_store.py)）。
- 测试的 fixture 时间硬编码为 `2026-08-14 12:00 UTC`，`create_proactive` 按注入的
  `now` 写 `created_at`。真实时间越过 `2026-08-15 12:00 UTC` 后，24 小时窗口把这条
  moment 判成过期 → `acked=1` + 待发 push 置 `dead`。于是抑制逻辑失效（第一条）、
  重启后没有可重放的投递（第二条）。
- 也就是说这两条测试**在 2026-08-15 12:00 UTC 前后自己变红的**，与 Android 改造无关。

修法（一行，且顺带让 scheduler 的时钟注入自洽）：

```python
# app_store.py
def expire_stale_proactive(self, max_age=timedelta(hours=24), *, now=None) -> int:
    cutoff = _iso((now or _now()) - max_age)

# app_push.py, run_once
self.store.expire_stale_proactive(now=now)
```

生产行为不变（生产路径上 `now` 本来就是真实时间），但测试重新变成确定性的。
**这一步必须先做**，否则下面每一步的"测试全绿"都没有基线。

---

后续四步，每步结束时测试全绿、iOS 行为不变。

**P1 · 纯重构，零新功能** — 已完成（commit `beed935`）

platform 列 + `push_token` 改名 + `DeviceAttestor`/`PushProvider` Protocol 提取 +
scheduler 收 providers 映射。注册表里仍只有 iOS 一个实现，wire 协议逐字未变。

实测下来与原计划的三处出入：

- `FakeVerifier` **不用改**。`AppAuthenticator` 用
  `getattr(verifier, "platform", "ios")` 归类传入的 verifier，老测试原样通过。
- `AuthKey` 多一个 `platform` 字段，`authenticate()` 用它选 attestor；
  `advance_counter` 加了 `counter > key.counter` 的前置判断，好让 Android 侧
  "不推进计数器"的实现不会写回一个没变的值。
- 补了两类原本没有的测试：`SchemaMigrationTests` 建一个旧 schema 的库、灌入数据、
  用新 `AppStore` 打开，断言 platform 回填为 `ios`、`push_token` 保住原值、
  **counter 不丢**（丢了等于放行一次重放），以及重开库是幂等的；
  `test_app_push.py` 两条覆盖平台路由和"缺 provider 走重试"。

验收：16 个测试文件全部通过（`python tests/test_xxx.py` 逐个跑 —— 见 pyproject
第 28 行，测试是自包含脚本，`unittest discover` 会因为 `_helpers` 的导入方式
报 9 个 loader 错，那是跑法不对，不是回归）。

**P2 · FCM provider** — 已完成（commit `b42ab3c`）

`app_push_fcm.py`（`FCMProvider`，`platform = "android"`）+ 14 条测试，全部用假
transport，不碰网络。计划外多做的一件事：

- **把"设备已死"的判定从 scheduler 移进 provider。** 原来 scheduler 靠
  `result.status in {400, 410}` 嗅探 APNs 的方言，FCM 用 404 `UNREGISTERED`
  表达同一件事，会被漏判成"可重试"而无限重投。现在 `PushResult` 多一个
  `permanent` 字段由各 provider 自己填，scheduler 只看这个布尔量。
- `APNsTransport`/`HttpxAPNsTransport` 改名 `PushTransport`/`HttpxPushTransport`，
  两个 provider 共用。
- access token 走 RS256 自签断言换 OAuth2，未引入 `google-auth`。缓存按响应里的
  `expires_in` 提前 60 秒续期，测试覆盖了短寿命 token 的续期路径。
- `.gitignore` 补了 `*service-account*.json` / `*firebase-adminsdk*.json`
  ——那文件里是 RSA 私钥，等同于 APNs 的 `.p8`，原本没被挡住。
- worker 里 FCM 是 opt-in：没配就只跑 iOS，Android 投递挂起重试而不判死。

**P3 · Android attestor** — 已完成（commit `da9cf75`）

`app_attest_android.py`（`AndroidKeyAttestor`）+ 21 条测试，全部用合成证书链，
不需要真设备也不碰网络。与计划的出入：

- **不内嵌 Google 根证书。** 计划里写的是"缺省用内嵌根证书"，实现改成必须由
  `MURMUR_APP_ATTEST_GOOGLE_ROOT_CA` 提供，缺了就拒绝构造。烧进源码里的信任锚
  等于部署方从没核对过的信任锚。
- `_der_primitive_values` **不能复用**。它是个拍平的遍历器，而
  `AuthorizationList` 的字段靠 context tag 编号区分，且 702/704/709 都超过 30、
  必须走 high-tag-number 编码。写了个约 80 行的最小 DER 解析器，只认
  SEQUENCE/INTEGER/ENUMERATED/OCTET STRING/BOOLEAN/SET 和 EXPLICIT context tag。
- attestation 的 wire 格式定为 **SEQUENCE OF OCTET STRING**（leaf 在前），
  正好是 Android 端 `KeyStore.getCertificateChain()` 每项 `getEncoded()` 的结果。
- `environment` 的判定改成 **fail closed**：production 模式下硬件级别不是
  TEE/StrongBox、或 verifiedBootState 不是 Verified、或 bootloader 未锁，
  直接拒绝，而不是降级成 `development` 放进库里。这与
  `AppleAppAttestVerifier` 拒绝 AAGUID 不匹配的行为一致。
- 吊销列表（`GoogleAttestationStatus`）带 1 小时缓存，只在 enrollment 查；
  取不到时默认拒绝注册，`MURMUR_APP_ATTEST_REVOCATION_FAIL_OPEN=1` 才放行。

**P4 · API 与配置接线** — 已完成（commit `89994e5`）

- `/v1/enrollments` 收 `platform`，**缺省 `ios`** —— 已发布的 iOS 客户端不带这个
  字段，必须不重新编译就继续能注册。取值不在 `{ios, android}` 里直接 400。
- `/v1/device` 的 token 校验按平台分流：iOS 仍是 hex 32–256；Android 是
  `[A-Za-z0-9_:.-]` 64–512。字段名接受 `push_token`，同时保留 `apns_token`
  作为别名 —— 同样是为了不动已发布的客户端。
- **判定用哪套规则的平台，取自注册时存的 key，不取自请求体。** 请求里塞
  `"platform": "android"` 不会放宽 iOS 设备的 token 规则，有测试盯着这条。
- `app_devices` 的两个查询把 `platform` 一起返回，客户端能确认自己被认成了什么。
- `AppSettings.validate()` 在 production + `android_enabled` 时同时要求
  `validate_android()` 和 `validate_fcm()` 通过：只配一半会变成"能注册但永远收不到
  推送"或者"能收推送但验不了注册"。

新增 7 条 wire 契约测试（`PlatformWireTests`），其中两条是防回归的：不带
`platform` 的注册仍然记成 iOS，`apns_token` 这个老字段名仍然能用。

---

四步全部完成。服务端现在可以接受 Android 客户端注册、逐请求验签、并通过 FCM 投递。
剩下的是 Android 客户端本身（不在本文档范围）。客户端需要对上的契约：

| 项 | 值 |
|---|---|
| 注册 attestation | `SEQUENCE OF OCTET STRING`，leaf 在前，每项是 `Certificate.getEncoded()` |
| `key_id` | `base64url(sha256(SPKI))`，去掉 padding |
| 每请求 assertion | 裸 ECDSA/SHA-256 签名，签的是 `client_data_hash` |
| `client_data_hash` | `sha256(challenge ‖ METHOD ‖ path ‖ sha256(body))` |
| 请求头 | `X-Murmur-Key-Id` / `X-Murmur-Challenge-Id` / `X-Murmur-Assertion`（与 iOS 同名） |
| Keystore 密钥要求 | P-256、purpose 含 SIGN、digest 含 SHA-256、非导入 |

---

## 7. 需要你拍板的两处

1. **Play Integrity 要不要上。** Key Attestation 证明"硬件密钥是真的、App 包名和
   签名对得上"，但它不证明"这个 APK 是从 Play 装的、没被改过"。Play Integrity 补
   这一块，代价是绑定 Google Play 服务（国内设备是个现实问题）和额外配额。
   建议：**先不上**，Key Attestation + 邀请码已经够了；把接口留出来。

2. **`app_devices.platform` 的冗余**。见 §2，我倾向冗余以避免 push 热路径加 JOIN。
   如果你更在意 schema 正规化，就删掉这列、三个查询各加一个
   `JOIN app_attest_keys USING(key_id)`。
