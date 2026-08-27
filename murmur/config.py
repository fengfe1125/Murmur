"""集中读配置，别的模块不直接碰 os.environ。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("murmur.config")

# DeepSeek 直连。**注意是 /beta 不是 /v1**：assistant prefix（把回复首
# 字符钉成 "{"）只在 beta 端点上有，而这是 deepseek 肯吐 JSON 的唯一
# 可靠办法——见下面 DEFAULT_JSON_PREFIX。
# 余额接口不在这个路径下，在 API 根上（见 murmur/balance.py）。
DEFAULT_BASE_URL = "https://api.deepseek.com/beta"

# 中文自然、不把思考过程写进正文、便宜。备选见 README 的"换模型"。
DEFAULT_MODEL = "deepseek-v4-flash"

# 降级备案：主模型不可用时自动换的模型。**默认空 = 就一家，不换模型**，
# 同一个模型再来一次（线上失败几乎全是一次性的：网关 5xx，或者这一次
# 没按 JSON 写）。真想要降级就配第二家网关的 MURMUR_FALLBACK_BASE_URL /
# MURMUR_FALLBACK_API_KEY——同一家换个模型挡不住整体 503（2026-08-16）。
DEFAULT_FALLBACK_MODEL = ""

# 图片消息的多模态模型。读图 2 秒级、JSON 纪律好；同样不支持 json_schema。
DEFAULT_IMAGE_MODEL = "deepseek-v4-flash-vision-exp"

# deepseek 传 response_format 要么 400、要么把 token 全烧进思考，所以
# 默认关掉 schema、默认打开 prefix。两个默认值是配套的，别只改一个。
DEFAULT_JSON_SCHEMA = False
DEFAULT_JSON_PREFIX = True

CHANNEL_MODES = frozenset({"transition", "app_only"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _parse_bool(name: str, *, default: bool) -> bool:
    """读取安全相关布尔值；拼错时拒绝启动，而不是猜一个方向。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{name} 只能是 1/0、true/false、yes/no 或 on/off，收到：{raw!r}"
    )


def _parse_channel_mode() -> str:
    value = os.getenv("MURMUR_CHANNEL_MODE", "transition").strip().lower()
    if value not in CHANNEL_MODES:
        choices = " | ".join(sorted(CHANNEL_MODES))
        raise ValueError(
            f"MURMUR_CHANNEL_MODE 只能是 {choices}，收到：{value!r}"
        )
    return value


def _parse_float(name: str, *, low: float, high: float) -> float | None:
    """可选的采样参数。留空 = 请求里不带这个参数，用服务商自己的默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} 必须是一个数字，收到：{raw!r}") from None
    if not low <= value <= high:
        raise ValueError(f"{name} 必须在 {low} 到 {high} 之间，收到：{value!r}")
    return value


def _legacy_opencode_key() -> str | None:
    """迁移垫片：还在用 OPENCODE_API_KEY 的旧 .env 别直接断服。

    改名成 DEEPSEEK_API_KEY 是对的，但**换 key 的变量名是会停服的改动**：
    线上 .env 不跟着改，下一次部署整个机器人就没 key 了，而且只在有人
    发消息时才炸出来。所以旧名字继续认，同时吼一嗓子。
    等 VPS 和本机的 .env 都改完，这个函数和它的调用可以一起删掉。
    """
    value = os.getenv("OPENCODE_API_KEY", "").strip()
    if not value:
        return None
    log.warning(
        "OPENCODE_API_KEY 已改名为 DEEPSEEK_API_KEY，本次仍按旧名字读取。"
        "请把 .env 里这一行改掉——这个兼容以后会删。"
    )
    return value


def _parse_fallback_gateway() -> tuple[str | None, str | None]:
    """第二家网关的端点和 key：**要么都配，要么都别配**。

    只配 URL 不配 key 的话，`_client` 会拿主网关的 key 去打第二家的域名
    （`api_key or cfg.api_key`），等于把主网关的凭据静默地发给了
    另一家——降级路径上才会触发，日志里只看得到「尝试降级」。只配 key
    不配 URL 同理，是把第二家的 key 发给主网关。两种都是配置写了一半，
    照 _parse_bool 的规矩：拒绝启动，不猜一个方向。
    """
    base_url = os.getenv("MURMUR_FALLBACK_BASE_URL", "").strip() or None
    api_key = os.getenv("MURMUR_FALLBACK_API_KEY", "").strip() or None
    if bool(base_url) != bool(api_key):
        missing, present = (
            ("MURMUR_FALLBACK_API_KEY", "MURMUR_FALLBACK_BASE_URL")
            if base_url else
            ("MURMUR_FALLBACK_BASE_URL", "MURMUR_FALLBACK_API_KEY")
        )
        raise ValueError(
            f"配了 {present} 就必须同时配 {missing}——"
            f"只配一半会把另一家的 API key 发给不该收到它的那一方。"
            f"要维持「降级仍走主网关」的旧行为，两个都留空。"
        )
    return base_url, api_key


def _parse_identities(raw: str) -> list[list[str]]:
    """解析 "a|b, c" → [[a, b], [c]]。| 表示同一个人的多个身份。"""
    out: list[list[str]] = []
    for chunk in raw.replace(",", " ").split():
        ids = [x for x in chunk.split("|") if x]
        if ids:
            out.append(ids)
    return out


@dataclass(frozen=True)
class Config:
    api_key: str | None
    base_url: str
    model: str
    # 记忆整理用的模型。整理是"重写三块摘要"，便宜模型完全够，
    # 而且它是调用量最大的 prompt——默认跟随主模型，配了就分开算。
    memory_model: str | None
    # 主模型报错时的降级模型（空字符串 = 不降级，直接失败）。
    fallback_model: str
    # 降级走第二家网关时的端点和 key。默认空 = 和主网关同一家——
    # 2026-08-16 的教训是网关整体 503 时"同一家换个模型"不算降级，
    # 整条链一起挂。配上第二家才算真降级。两个必须成对，见
    # _parse_fallback_gateway。
    fallback_base_url: str | None
    fallback_api_key: str | None
    # 带图消息走这个多模态模型；空字符串 = 沿用主模型。
    image_model: str
    # 主模型是否支持 json_schema 响应格式。deepseek 传 response_format
    # 要么 400 要么把 token 全烧进思考，所以默认关；换成吃这套的模型
    # （qwen / kimi 系）时再打开。关掉后靠 SYSTEM 里的「只返回 JSON」+
    # _extract_json 兜底。
    json_schema: bool
    # 关掉 json_schema 之后模型还是不肯吐 JSON（deepseek 直连实测十次有
    # 九次直接回聊天正文）时打开：用 beta 端点的 assistant prefix 把回复
    # 的第一个字符钉死成 "{"，模型只能续写 JSON。默认开，配套 /beta。
    json_prefix: bool
    # 对话采样参数（只作用于 respond/initiate，读图和记忆整理不用）。
    # None = 请求里不带，用服务商默认值。复读明显时把 presence_penalty
    # 调到 0.3 左右比堆提示词管用。
    temperature: float | None
    presence_penalty: float | None
    # 回复质量 P1 分阶段开关。默认全关，先完成 P0 线上结构化验收，
    # 再一次只开一个，任何一项退化都能独立回滚。
    open_loops: bool
    reply_directives: bool
    proactive_materials: bool
    affect: bool
    telegram_token: str | None
    allowed_chat_ids: set[int]
    dingtalk_client_id: str | None
    dingtalk_client_secret: str | None
    # 每个元素是"同一个人"的所有 userId。钉钉的 staffId 按组织分配，
    # 同一个人在不同企业里 id 不同——单聊和群聊拿到的可能不是一个。
    # 用 | 分隔表示同一人，第一个是主 id（主动发消息用它）。
    dingtalk_identities: list[list[str]]
    # 谁会收到主动消息。白名单是"允许使用"，这个是"允许被打扰"，
    # 两回事——把同事加进白名单不等于要每天给他发十几条。
    dingtalk_initiative: list[str]
    # 微信（腾讯官方 ClawBot 通道）。token 由 OpenClaw 扫码登录后落盘，
    # 这里留空就自动去 ~/.openclaw 里找，一般不用填。
    wechat_token: str | None
    wechat_account_id: str | None
    wechat_allowed_users: set[str]
    # 微信主动消息默认**关闭**：官方 sendMessage 的设计前提是被动回复，
    # 主动发要复用存下来的 context_token，属于灰区。要开自己开，风险自负。
    wechat_initiative: bool
    # QQ（腾讯官方机器人，q.qq.com 创建）。这是独立的机器人账号，
    # 不是扫码挂个人 QQ。
    qq_app_id: str | None
    qq_client_secret: str | None
    qq_allowed_users: set[str]
    qq_initiative: bool
    qq_sandbox: bool
    # 正式渠道切换：transition 允许显式启动测试 bot；app_only 永久拒绝它们。
    channel_mode: str
    # Telegram / 钉钉 / 微信 / QQ 都是测试通道，必须明确打开才可启动。
    enable_test_bots: bool
    # 新人自动入册：第一次说话就建上下文、建记忆文件、打招呼、排上主动消息，
    # 默认关闭；只有明确打开才允许陌生平台账号进入。
    auto_enroll: bool
    # 看板访问令牌。看板默认只绑 127.0.0.1，但 --host 0.0.0.0 时
    # 零认证 = 聊天原文和记忆文件裸奔。配了这个就要求
    # Header X-Murmur-Token（或 ?token=），不配则维持原行为。
    web_token: str | None
    db_path: Path
    # 日志目录。默认跟着数据库走（db 旁边建 logs/），
    # run.sh 和看板读的是同一个值，改 .env 里 MURMUR_LOGDIR 一起生效。
    log_dir: Path
    tz: ZoneInfo

    @property
    def allowed_dingtalk_users(self) -> set[str]:
        return {i for group in self.dingtalk_identities for i in group}

    def canonical_dingtalk_id(self, user_id: str) -> str:
        """把任意一个身份映射到主 id，保证同一个人共用一条记忆。"""
        for group in self.dingtalk_identities:
            if user_id in group:
                return group[0]
        return user_id

    @property
    def test_bots_allowed(self) -> bool:
        return self.channel_mode == "transition" and self.enable_test_bots

    def require_test_bot(self, label: str) -> None:
        """旧平台只作为隔离测试通道存在；所有入口统一从这里失败关闭。"""
        if self.channel_mode == "app_only":
            raise RuntimeError(
                f"{label} 已停用：MURMUR_CHANNEL_MODE=app_only，正式渠道只允许 App"
            )
        if not self.enable_test_bots:
            raise RuntimeError(
                f"{label} 是测试通道；请在隔离的测试环境中显式设置 "
                "MURMUR_ENABLE_TEST_BOTS=1"
            )

    @classmethod
    def load(cls) -> Config:
        raw_ids = os.getenv("MURMUR_ALLOWED_CHAT_IDS", "").strip()
        allowed = {
            int(x) for x in raw_ids.replace(",", " ").split() if x.lstrip("-").isdigit()
        }
        # 多家都是 OpenAI 兼容接口，只是 base_url + key 不同。
        # 按顺序找第一个有值的，方便随时切：
        #   DeepSeek  → DEEPSEEK_API_KEY（默认）
        #   xAI Grok  → MURMUR_API_KEY + MURMUR_BASE_URL=https://api.x.ai/v1
        key = (
            os.getenv("MURMUR_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("XAI_API_KEY")
            or _legacy_opencode_key()
            or None
        )
        fallback_base_url, fallback_api_key = _parse_fallback_gateway()
        db_path = Path(os.getenv("MURMUR_DB", "./murmur.db")).expanduser()
        raw_log = os.getenv("MURMUR_LOGDIR", "").strip()
        log_dir = Path(raw_log).expanduser() if raw_log else db_path.parent / "logs"
        return cls(
            api_key=key,
            base_url=os.getenv("MURMUR_BASE_URL", DEFAULT_BASE_URL),
            model=os.getenv("MURMUR_MODEL", DEFAULT_MODEL),
            memory_model=os.getenv("MURMUR_MEMORY_MODEL") or None,
            fallback_model=os.getenv(
                "MURMUR_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL
            ),
            fallback_base_url=fallback_base_url,
            fallback_api_key=fallback_api_key,
            image_model=os.getenv("MURMUR_IMAGE_MODEL", DEFAULT_IMAGE_MODEL),
            json_schema=_parse_bool(
                "MURMUR_JSON_SCHEMA", default=DEFAULT_JSON_SCHEMA
            ),
            json_prefix=_parse_bool(
                "MURMUR_JSON_PREFIX", default=DEFAULT_JSON_PREFIX
            ),
            temperature=_parse_float("MURMUR_TEMPERATURE", low=0.0, high=2.0),
            presence_penalty=_parse_float(
                "MURMUR_PRESENCE_PENALTY", low=-2.0, high=2.0
            ),
            open_loops=_parse_bool("MURMUR_OPEN_LOOPS", default=False),
            reply_directives=_parse_bool("MURMUR_REPLY_DIRECTIVES", default=False),
            proactive_materials=_parse_bool(
                "MURMUR_PROACTIVE_MATERIALS", default=False
            ),
            affect=_parse_bool("MURMUR_AFFECT", default=False),
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            allowed_chat_ids=allowed,
            dingtalk_client_id=os.getenv("DINGTALK_CLIENT_ID") or None,
            dingtalk_client_secret=os.getenv("DINGTALK_CLIENT_SECRET") or None,
            dingtalk_identities=(ids := _parse_identities(
                os.getenv("DINGTALK_ALLOWED_USERS", "")
            )),
            # 没配就只给白名单里第一个人发——默认不打扰别人
            dingtalk_initiative=[
                x for x in os.getenv("DINGTALK_INITIATIVE_USERS", "")
                .replace(",", " ").split() if x
            ] or ([ids[0][0]] if ids else []),
            wechat_token=os.getenv("WECHAT_TOKEN") or None,
            wechat_account_id=os.getenv("WECHAT_ACCOUNT_ID") or None,
            wechat_allowed_users={
                x for x in os.getenv("WECHAT_ALLOWED_USERS", "")
                .replace(",", " ").split() if x
            },
            wechat_initiative=os.getenv("WECHAT_INITIATIVE", "").strip().lower()
            in ("1", "true", "yes", "on"),
            qq_app_id=os.getenv("QQ_APP_ID") or None,
            qq_client_secret=os.getenv("QQ_CLIENT_SECRET") or None,
            qq_allowed_users={
                x for x in os.getenv("QQ_ALLOWED_USERS", "")
                .replace(",", " ").split() if x
            },
            qq_initiative=os.getenv("QQ_INITIATIVE", "1").strip().lower()
            not in ("0", "false", "no", "off"),
            qq_sandbox=os.getenv("QQ_SANDBOX", "").strip().lower()
            in ("1", "true", "yes", "on"),
            channel_mode=_parse_channel_mode(),
            enable_test_bots=_parse_bool(
                "MURMUR_ENABLE_TEST_BOTS", default=False
            ),
            auto_enroll=_parse_bool("MURMUR_AUTO_ENROLL", default=False),
            web_token=os.getenv("MURMUR_WEB_TOKEN") or None,
            db_path=db_path,
            log_dir=log_dir,
            tz=ZoneInfo(os.getenv("MURMUR_TZ", "Asia/Shanghai")),
        )
