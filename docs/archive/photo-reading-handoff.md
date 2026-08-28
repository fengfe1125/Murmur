# 「当年今日」读图房间 · 对接文档

> 状态：归档｜适用：历史追溯｜归档核验：2026-08-28｜依据：重组前文件快照。
> 以下保留当时描述、路径和判断，不代表当前能力、部署状态或新开发要求。现行说明见 [文档导航](../README.md)。

> 给下一位开发者（或 Claude）的交接：这一屏是什么、代码在哪、怎么配、
> 什么验证过了什么没有、接下来做什么。2026-08-22 状态。

## 这是什么

聊天窗口上滑一张旧照片 → 卡片碎成粒子 → 同一批粒子在独立界面顶部聚拢，
照片钉在上方 → 服务端视觉模型读图，给出一句「猜你想说的」+ 三个话头 →
用户就着照片聊，它接住并反问 → 这一轮和照片写进服务端记忆。

设计红线：**不能把普通日子包装成纪念日**。卡片必须说清是「去年的今天」
还是「相册里翻到的」（随机回落）。唯一死路：相册一张照片都没有。

## 线上契约（SSE）

`POST /v1/moments` 带 `intent=photo_reading` + 一张不带字的图
（带字或没图都 400）。房间事件序列：

```
accepted → bubble(guess) → angles(三个话头) → done
```

读图失败**静默降级**成普通 `respond()`：只有 bubble，没有 angles 事件——
房间宁可少三个话头也不能开门空白。客户端必须容忍 angles 缺席。
房间里之后的每一轮都是不带图的普通 moment，落在同一条记忆线
（`entries.thread = app:direct:<user_id>`，`app_memory_links` 挂接）。

## 代码地图

后端：
- `murmur/app_api.py:610` 收 moment、校验 intent；`:707` SSE 事件流
- `murmur/app_worker.py:112` 按 intent 分流；`:123` 读图失败退回 respond
- `murmur/engine.py:330` `read_photo()`——只走 `cfg.image_model`，
  没有纯文字降级档；`:262` 截断抢救 `_salvage_reading`；`:191` `_extract_json`
  （含全角引号兜底）
- `murmur/persona.py:422` `READING_SYSTEM`——这一屏的生死线，
  猜得具体而冒险比安全的废话好
- `murmur/dossier.py:202` 记忆整理，攒满 `REFRESH_EVERY=12` 条重写
- `murmur/config.py` `json_prefix` 开关（DeepSeek 直连配方的一部分）

iOS：
- `MurmurApp/MurmurOnThisDay.swift` 选片与卡片（「去年的今天」/「相册里翻到的」标注）
- `MurmurApp/MurmurPhotoRoom.swift` 读图房间（照片钉顶 + guess + 话头）
- `MurmurApp/OnThisDay.metal` 粒子碎裂/聚拢着色器
- `MurmurApp/MurmurSessionModel.swift`——AnglesRow 已删，普通带图 moment
  不再带 angles

测试：
- `tests/test_photo_reading.py`（21 条：解析/截断抢救/worker 契约/降级）
- `tests/test_engine_fallback.py`（json_prefix、全角引号）
- `tests/test_dossier.py`（prefix 分支）
- `tests/test_app_api.py` / `test_app_store.py`（intent 契约、旧库 ALTER TABLE 迁移）

## VPS 配置：DeepSeek 直连配方（已实测）

```
MURMUR_BASE_URL=https://api.deepseek.com/beta   # 必须是 /beta，prefix 只在 beta 上有
MURMUR_API_KEY=sk-...                            # DeepSeek 后台的 key
MURMUR_MODEL=deepseek-v4-flash
MURMUR_IMAGE_MODEL=deepseek-v4-flash-vision-exp
MURMUR_FALLBACK_MODEL=
MURMUR_JSON_SCHEMA=0
MURMUR_JSON_PREFIX=1
```

实测延迟：读图整屏 2-3 秒（mimo-v2.5 要 20-30 秒），文字轮 1-3 秒。
prompt caching 生效（system+历史命中缓存），成本极低。

## 踩过的坑（别再踩）

1. **mimo-v2.5 的思考会烧光 max_tokens**：`READING_MAX_TOKENS` 500/900 档
   实测 100% 截断、正文为空，读图静默降级。现在 2500 + 90s 超时 +
   截断抢救（guess 写在最前，多半能捞出来）。
2. **deepseek 直连不吃「只返回 JSON」提示词**：十次有九次直接回聊天正文。
   json_object 返回空白，json_schema 直接 400。唯一可靠的是 beta 端点的
   assistant prefix（首字符钉成 `{`），所以有了 `MURMUR_JSON_PREFIX`。
3. **deepseek 输出风格漂移**：偶尔全角引号/全角冒号当 JSON 定界符。
   `_extract_json` 和 `dossier._loads_tolerant` 做了逐层兜底，
   只在解析失败后才启用，不会弄坏正常输出。
4. **dossier 整理也会思考烧光 token**（正文空 → 写成空壳 dossier）。
   现改走 prefix + 失败重试一次，两轮都不成放弃这轮，下批 12 条再来。
5. **OpenCode Go 网关传图统一 500**（官方 issue 未修），别用它做读图通道。

## 验证状态

已验证（本地隔离 DB + 真实模型，非打桩）：
- 读图契约 bubble→angles→done，两张真照片 guess/话头质量过关
- 带字/无图 400；读图失败静默降级（假模型名实测）；记忆同线、dossier 真实重写
- 后端 182 条 unittest + 全部脚本式测试文件绿，ruff 干净
- iOS：模拟器 28 条 UI 测试 + 单元测试绿，无签名真机构建成功，
  模拟器手动走通全链路（粒子聚拢、点话头填输入框、发送有回应）

没验证：
- **App Attest / APNs**：模拟器不存在这两样，必须真机
- **VPS 实机部署**：本 PR 的代码还没上过 VPS。注意线上旧码的读图
  必截断（500 token），不部署就一直在静默降级

## 待决事项（按优先级）

1. **部署本 PR 到 VPS**：`murmur-update` 走 `MURMUR_GIT_BRANCH`（默认 main）。
   本 PR 目标是 vps 分支——要么把 VPS 的 `MURMUR_GIT_BRANCH` 指到 vps，
   要么把 vps 合进 main 再更。
2. **DeepSeek key 轮换**：验证用的 key 在聊天记录里出现过，去后台换一个新的
   填进 VPS `.env`。**任何文档和代码里都不要写 key 真值。**
3. **专用读图 worker**（可选，半天）：现在 worker 单进程全局串行，A 的读图
   会堵 B 的普通消息。`app_store.claim_job` 加 intent 过滤 + 复制一个
   systemd unit 即可，两类 worker 必须同时加条件。详见调研结论。
4. **App 端预读**（可选，产品决策）：iOS 后台任务夜间预传「明天当年今日」
   候选图、服务端预读入库，上滑=查库秒开。注意隐私语义变化：
   照片在用户主动分享前就会上传。
5. **vision-exp 是实验模型**：DeepSeek 可能随时改行为。上线头几天看
   worker 日志里的 `fell back` / `抢救` 关键字。

## 本地开发回路

```bash
./scripts/dev-app.sh            # 本地 API + 构建 + 装进已启动的模拟器
./scripts/dev-app.sh --api-only # 只起 API
python -m unittest discover tests   # 后端全量（11 个「import error」是
                                    # 脚本式测试的自执行特性，单独跑都过）
python -m ruff check murmur/ tests/
```

真实模型端到端验证的搭法：隔离 DB（`MURMUR_DB`/`MURMUR_APP_*` 指到 /tmp）
+ `MURMUR_APP_ATTEST_MODE=development` + dev token，起 app-api 和
app-worker 两个进程，邀请码注册 dev- 开头的 key_id，multipart 发 moment，
SSE 收事件。注意 httpx 纯字段要用 `(None, value)` 元组才走 multipart。
