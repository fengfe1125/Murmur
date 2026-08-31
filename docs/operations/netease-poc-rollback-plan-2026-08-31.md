# 网易云陪听 PoC：VPS 回滚计划

> 状态：实施提案｜适用：`codex/feat/music/netease-vps-mobile-poc`｜核验：2026-08-31
>
> 依据：现行 `murmur-update` 事务更新器、29 项隔离更新/回滚测试、网易云 VPS＋手机 PoC 计划
>
> 边界：没有生产操作授权；本文不代表该分支已部署或允许部署

## 1. 当前保护

- 功能分支：`codex/feat/music/netease-vps-mobile-poc`；
- 生产更新器只接受 `main` 的快进提交，并要求生产检出位于 `main`；
- 因此该功能分支不能被现有 `murmur-update` 直接部署到 VPS；
- 更新前对配置解析出的 SQLite 数据库做在线快照；
- 安装、测试、systemd 单元安装、daemon reload 或服务重启失败时，更新器恢复旧 SHA、旧可编辑安装、更新器、单元、logrotate 与所有已尝试重启的服务；
- 回滚不完整时保留事务快照和锁，阻止下一次无人值守更新覆盖证据。

2026-08-31 使用仓库 `.venv/bin/python server/tests/test_updater.py` 验证：29 项通过。直接使用系统 Python 会因缺少 `python-dotenv` 在切换代码前失败，不作为有效测试环境。

## 2. 三层回滚

### 2.1 即时功能回退

网易云能力必须有独立开关，默认关闭：

```dotenv
MURMUR_NETEASE_CATALOG_ENABLED=0
MURMUR_NETEASE_ROOM_EXPERIMENT_ENABLED=0
```

出现异常时，管理员先关闭房间实验开关并重启已有 `murmur-app-worker`。关闭后：

- 不创建新房间；
- 停止现有 heartbeat，并尽力结束测试房间；
- 不接受新的播放控制命令；
- 已有聊天、照片和 Audius 路径继续运行；
- 历史网易歌曲卡仍以元数据和外部链接显示。

这是最快的操作回退，不需要改 Git 或恢复数据库。

### 2.2 部署过程中自动回滚

功能合并到 `main` 后仍只通过现有 `murmur-update` 发布。以下任一失败必须使更新返回非零并自动恢复旧版本：

- 安装依赖；
- 仓库统一测试；
- systemd 单元安装；
- daemon reload；
- `murmur-app-worker`、`murmur-app-api` 或 `murmur-web` 重启/存活检查。

首版房间模块放在已有 `murmur-app-worker` 内，不增加新的 systemd 生命周期。这样更新器现有服务清单已经覆盖它。

### 2.3 成功发布后的行为撤回

现有更新器拒绝非快进和非 `main` 更新，因此不在生产 VPS 上执行 `git reset`、切分支或直接检出旧 SHA。

成功发布后发现问题时：

1. 先关闭两个网易 feature flag，恢复核心体验；
2. 在仓库对问题提交创建正常的 `git revert`，形成新的 `main` 快进提交；
3. 通过正常 CI 和 `murmur-update` 发布该撤回提交；
4. 验证服务、聊天、照片、数据库和已有歌曲卡；
5. 保留更新前数据库快照，不自动覆盖运行中的数据库。

这种方式保留历史并符合生产更新器的快进约束。

## 3. 数据可回滚约束

- PoC 房间状态只做短期内存状态；worker 重启即失效，不新建需要回滚的持久化房间数据库；
- 若确需落盘，只允许独立的可删除实验状态，且必须先把它加入更新器备份清单；
- 现有数据库变化只能是可为空、旧代码可忽略的增量字段/表；
- 不做删除列、重写历史歌曲记录、批量迁移 provider 或不可逆数据转换；
- 旧代码读到 `provider=netease` 卡片时必须文字降级，不因未知 provider 使聊天记录无法加载；
- 机器人 Cookie/token 存在 `/etc/murmur` 下的独立 secret 文件，不随代码更新和数据库快照移动；撤回时只停用，不自动删除，后续由管理员单独撤销。

## 4. 新 systemd 服务闸门

在现有更新器支持以下事项前，不新增或启用 `murmur-netease-room.service`：

1. 服务加入更新器的已管理服务清单；
2. active-but-disabled 预检覆盖新服务；
3. 更新失败能恢复旧 unit 并重启旧进程；
4. 成功发布后撤回功能时能禁用并清理不再需要的 unit；
5. 对部分安装、重启失败、inactive、unit 删除和撤回均有隔离测试；
6. 回滚失败保留新服务的恢复材料和更新锁。

未满足前，逻辑隔离通过 `murmur-app-worker` 内的深模块、feature flag、超时和网络 allowlist 完成。

## 5. 发布前必须增加的检查

- 网易功能关闭时，现有全套测试通过；
- worker 使用无效机器人凭据时失败关闭网易模块，不退出核心 worker；
- 房间命令超时或私有接口变化时，不影响普通聊天作业；
- 更新测试新增“网易功能开启但 worker 重启失败，恢复旧 SHA/旧安装/旧服务”的场景；
- `git revert` 后的新版本能读取带网易卡片的旧聊天数据；
- 日志、数据库、SSE 和模型输入扫描不到 Cookie/token；
- 更新前 SQLite 快照存在且权限为目录 `0700`、文件 `0600`；
- kill switch 操作在隔离 VPS 演练成功并记录耗时。

## 6. 发布与回滚验收记录

每次试用发布必须记录：

- 发布前 SHA、目标 SHA、撤回提交（如有）；
- feature flag 初始值；
- 数据库快照文件名；
- 被重启的服务；
- 房间模块健康检查；
- kill switch 演练结果；
- 自动回滚或撤回后的最终 SHA、服务状态和数据检查。

没有这些证据，只能称为本地实现，不能称为“VPS 可回滚”。

