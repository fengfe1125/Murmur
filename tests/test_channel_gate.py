"""正式 App / 测试 Bot 渠道门禁。

    python tests/test_channel_gate.py
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _helpers import make_config  # noqa: E402

from murmur import cli  # noqa: E402
from murmur.config import Config  # noqa: E402
from murmur.memory import Memory  # noqa: E402

ok = fail = 0


def check(name: str, condition: bool, extra: str = "") -> None:
    global ok, fail
    if condition:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


def load(**values: str) -> Config:
    with patch.dict(os.environ, values, clear=True):
        return Config.load()


print("\n── 安全默认值 " + "─" * 42)
cfg = load()
check("默认是 transition（给验收期留出显式测试入口）",
      cfg.channel_mode == "transition")
check("测试 Bot 默认关闭", cfg.enable_test_bots is False)
check("新人自动入册默认关闭", cfg.auto_enroll is False)
check("默认门禁拒绝测试 Bot", cfg.test_bots_allowed is False)

print("\n── 配置校验 " + "─" * 44)
cfg = load(MURMUR_CHANNEL_MODE="transition", MURMUR_ENABLE_TEST_BOTS="yes")
check("transition + 显式开关才允许测试 Bot", cfg.test_bots_allowed is True)

cfg = load(MURMUR_CHANNEL_MODE="app_only", MURMUR_ENABLE_TEST_BOTS="1")
check("app_only 永远压过测试开关", cfg.test_bots_allowed is False)
try:
    cfg.require_test_bot("Telegram Bot")
except RuntimeError as exc:
    check("app_only 给出只允许 App 的错误", "只允许 App" in str(exc))
else:
    check("app_only 给出只允许 App 的错误", False)

for key, value in (
    ("MURMUR_CHANNEL_MODE", "production"),
    ("MURMUR_ENABLE_TEST_BOTS", "maybe"),
    ("MURMUR_AUTO_ENROLL", "typo"),
):
    try:
        load(**{key: value})
    except ValueError as exc:
        check(f"{key} 拼错会失败关闭", key in str(exc))
    else:
        check(f"{key} 拼错会失败关闭", False)

print("\n── CLI 启动门禁 " + "─" * 41)
for command in ("bot", "dingtalk", "wechat", "qq"):
    stderr = io.StringIO()
    with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(stderr):
        result = cli.main([command])
    check(f"{command} 未显式开启时拒绝启动",
          result == 1 and "MURMUR_ENABLE_TEST_BOTS=1" in stderr.getvalue())

called: list[str] = []
fake_bot = types.ModuleType("murmur.bot")
fake_bot.run = lambda: called.append("telegram")
with (
    patch.dict(os.environ, {
        "MURMUR_CHANNEL_MODE": "transition",
        "MURMUR_ENABLE_TEST_BOTS": "1",
    }, clear=True),
    patch.dict(sys.modules, {"murmur.bot": fake_bot}),
):
    result = cli.main(["bot"])
check("显式测试环境可以启动 Bot", result == 0 and called == ["telegram"])

stderr = io.StringIO()
with (
    patch.dict(os.environ, {
        "MURMUR_CHANNEL_MODE": "app_only",
        "MURMUR_ENABLE_TEST_BOTS": "1",
    }, clear=True),
    patch.dict(sys.modules, {"murmur.bot": fake_bot}),
    contextlib.redirect_stderr(stderr),
):
    result = cli.main(["bot"])
check("app_only 即使误开测试开关也拒绝启动",
      result == 1 and called == ["telegram"] and "只允许 App" in stderr.getvalue())

with tempfile.TemporaryDirectory() as directory:
    env_root = Path(directory)
    (env_root / ".env").write_text(
        "MURMUR_CHANNEL_MODE=app_only\n"
        "MURMUR_ENABLE_TEST_BOTS=0\n"
        "TELEGRAM_BOT_TOKEN=production-secret-must-not-leak\n"
    )
    (env_root / ".env.test-bots").write_text(
        "MURMUR_CHANNEL_MODE=transition\n"
        "MURMUR_ENABLE_TEST_BOTS=1\n"
        "MURMUR_AUTO_ENROLL=0\n"
    )
    observed: list[str | None] = []
    isolated_bot = types.ModuleType("murmur.bot")
    isolated_bot.run = lambda: observed.append(os.getenv("TELEGRAM_BOT_TOKEN"))
    previous_cwd = Path.cwd()
    try:
        os.chdir(env_root)
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.dict(sys.modules, {"murmur.bot": isolated_bot}),
        ):
            result = cli.main(["bot"])
    finally:
        os.chdir(previous_cwd)
    check("Bot CLI 不从生产 .env 补齐缺失凭据",
          result == 0 and observed == [None])

print("\n── 直接 run() 也有门禁 " + "─" * 34)
blocked = load(MURMUR_CHANNEL_MODE="app_only", MURMUR_ENABLE_TEST_BOTS="1")
from murmur import bot, dingtalk, qq, wechat  # noqa: E402

for label, module, bypass_name in (
    ("Telegram", bot, None),
    ("DingTalk", dingtalk, "_bypass_proxy_for_dingtalk"),
    ("WeChat", wechat, "_bypass_proxy_for_wechat"),
    ("QQ", qq, "_bypass_proxy_for_qq"),
):
    bypass = None
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(Config, "load", return_value=blocked))
            if bypass_name:
                bypass = stack.enter_context(
                    patch.object(module, bypass_name, return_value="test")
                )
            module.run()
    except RuntimeError as exc:
        check(f"{label} run() 无法绕过 app_only", "只允许 App" in str(exc))
        if bypass is not None:
            check(f"{label} 门禁在代理/DNS 副作用之前执行", bypass.call_count == 0)
    else:
        check(f"{label} run() 无法绕过 app_only", False)

print("\n── 空白名单失败关闭 " + "─" * 34)
check("Telegram 空名单 + 禁止自动入册会拒绝",
      bot._allowed(make_config(auto_enroll=False), 123) is False)
check("Telegram 只有显式开自动入册才放行陌生人",
      bot._allowed(make_config(auto_enroll=True), 123) is True)

with tempfile.TemporaryDirectory() as directory:
    root_tmp = Path(directory)

    dt_cfg = make_config(root_tmp / "dt.db", auto_enroll=False)
    dt_mem = Memory(dt_cfg.db_path)
    dt_handler = dingtalk.MurmurHandler(dt_cfg, dt_mem)
    dt_result = dt_handler._handle(SimpleNamespace(data={
        "senderStaffId": "stranger",
        "senderId": "stranger",
        "senderNick": "stranger",
        "conversationType": "1",
        "conversationId": "c1",
        "msgtype": "text",
        "text": {"content": "你好"},
    }))
    check("钉钉空名单 + 禁止自动入册会拒绝",
          isinstance(dt_result, tuple) and dt_result[-1] == "ignored")

    wx_cfg = make_config(root_tmp / "wx.db", auto_enroll=False)
    wx_mem = Memory(wx_cfg.db_path)
    wx_handler = wechat.Handler(wx_cfg, wx_mem, SimpleNamespace())
    wx_handler.handle({
        "from_user_id": "stranger",
        "message_type": wechat.MSG_TYPE_USER,
        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
    })
    check("微信空名单 + 禁止自动入册会拒绝",
          wx_mem.recent(wechat.wechat_thread("stranger")[0]) == [])

    class QuietQQ:
        sent = False

        def send_bubbles(self, *args, **kwargs):
            self.sent = True

    qq_cfg = make_config(root_tmp / "qq.db", auto_enroll=False)
    qq_mem = Memory(qq_cfg.db_path)
    qq_http = QuietQQ()
    qq_handler = qq.Handler(qq_cfg, qq_mem, qq_http)
    qq_handler.handle_c2c(SimpleNamespace(
        id="m1", content="你好", timestamp=None, attachments=[],
        author=SimpleNamespace(user_openid="stranger", member_openid="stranger"),
        group_openid=None,
    ))
    check("QQ 空名单 + 禁止自动入册会拒绝",
          qq_http.sent is False)

print("\n── 部署不会偷偷启用 Bot " + "─" * 32)
root = Path(__file__).resolve().parent.parent
deployment_scripts = "\n".join(
    (root / relative).read_text()
    for relative in (
        "deploy/murmur-update",
        "scripts/link-vps-to-github.sh",
        "scripts/migrate-to-vps.sh",
    )
)
for service in ("telegram", "dingtalk", "wechat", "qq"):
    forbidden = f"systemctl enable --now murmur-{service}"
    check(f"脚本不会自动 enable {service}", forbidden not in deployment_scripts)
    unit = (root / f"deploy/murmur-{service}.service").read_text()
    check(f"{service} unit 只读测试环境",
          "EnvironmentFile=/opt/murmur/.env.test-bots" in unit
          and "ReadWritePaths=/opt/murmur/test" in unit)

api_unit = (root / "deploy/murmur-app-api.service").read_text()
worker_unit = (root / "deploy/murmur-app-worker.service").read_text()
caddy = (root / "deploy/Caddyfile.example").read_text()
check("App API 只监听 loopback:8766",
      "app-api --host 127.0.0.1 --port 8766" in api_unit)
check("App Worker 有独立常驻服务", "murmur app-worker" in worker_unit)
check("Caddy SSE 明确关闭缓冲",
      "reverse_proxy 127.0.0.1:8766" in caddy
      and "flush_interval -1" in caddy)
check("Caddy 不对 SSE 做响应压缩",
      all(
          not line.split("#", 1)[0].strip().startswith("encode")
          for line in caddy.splitlines()
      ))
check("Caddy 不依赖 2.10+ 实验性 request_body 指令",
      "request_body" not in "\n".join(
          line.split("#", 1)[0] for line in caddy.splitlines()
      ))
check("Caddy 不把认证 header 写进 access log",
      "log {" not in caddy and "format json" not in caddy)

production_env = (root / ".env.example").read_text()
test_env = (root / "deploy/test-bots.env.example").read_text()
check("生产 env 模板不夹带平台凭据",
      all(name not in production_env for name in (
          "TELEGRAM_BOT_TOKEN=", "DINGTALK_CLIENT_SECRET=",
          "WECHAT_TOKEN=", "QQ_CLIENT_SECRET=",
      )))
check("测试 env 同时隔离数据库、日志并显式开门",
      "MURMUR_ENABLE_TEST_BOTS=1" in test_env
      and "MURMUR_DB=/opt/murmur/test/murmur.db" in test_env
      and "MURMUR_LOGDIR=/opt/murmur/test/logs" in test_env)

migration = (root / "scripts/migrate-to-vps.sh").read_text()
setup = (root / "scripts/setup-murmur.sh").read_text()
gitignore = (root / ".gitignore").read_text()
check("生产迁移排除测试凭据和 Apple 私钥",
      "--exclude='Murmur/.env.test-bots'" in migration
      and "--exclude='Murmur/*.p8'" in migration
      and "--exclude='Murmur/*.p12'" in migration
      and "--exclude='Murmur/*.mobileprovision'" in migration)
check("原图、App 锁与微信扫码凭据不会进 Git/源码包",
      all(f"{name}/" in gitignore for name in ("app-uploads", "app-locks", "openclaw"))
      and all(f"--exclude='Murmur/{name}'" in migration
              for name in ("app-uploads", "app-locks", "openclaw", "wechat", "test")))
check("设置向导精确加载刚写入的生产 env",
      '.venv/bin/python - "$ENV_FILE"' in setup
      and "load_dotenv(sys.argv[1], override=True)" in setup)
check("迁移按生产 env 解析真实数据库路径",
      '"$PYTHON_BIN" - "$ROOT_DIR/.env"' in migration
      and "load_dotenv(sys.argv[1], override=True)" in migration)
check("迁移必须停止 App API/Worker/看板并确认作业排空",
      "app-api|app-worker|web" in migration
      and migration.count("assert_queue_drained") >= 3)
check("迁移只接受可一致备份的单 SQLite 布局",
      "MURMUR_APP_MEMORY_DB to resolve to the same SQLite file" in migration)
check("迁移不把旧平台凭据放进生产 env",
      'sanitize_production_env.py' in migration
      and 'sanitize_production_env.py' in setup)
check("生产切换前后均验证 Caddy/APNs/公网 HTTPS",
      "caddy validate --config /etc/caddy/Caddyfile" in migration
      and "APNS_KEY_PATH" in migration
      and 'curl --fail --silent --show-error' in migration)
check("迁移后收紧记忆、预览与数据库权限",
      "find \"\\$OLD_DIR/\\$private_dir\" -type f -exec chmod 600" in migration
      and "-name '*.db-wal'" in migration)

print(f"\n{'─' * 60}\n通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
