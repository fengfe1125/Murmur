"""集中读配置，别的模块不直接碰 os.environ。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

# OpenCode 的两个目录：Go 订阅走 /zen/go/v1，完整 Zen 目录走 /zen/v1
# （后者是按量计费，Go 的 key 调它会返回 CreditsError）。
DEFAULT_BASE_URL = "https://opencode.ai/zen/go/v1"

# 实测下来最合适的：中文自然、不把思考过程写进正文、便宜。
# 备选见 README 的"换模型"。
DEFAULT_MODEL = "qwen3.7-plus"


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
    # 新人自动入册：第一次说话就建上下文、建记忆文件、打招呼、排上主动消息，
    # 不用改 .env 也不用重启。关掉就退回"只有白名单里的人能用"。
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

    @classmethod
    def load(cls) -> Config:
        raw_ids = os.getenv("MURMUR_ALLOWED_CHAT_IDS", "").strip()
        allowed = {
            int(x) for x in raw_ids.replace(",", " ").split() if x.lstrip("-").isdigit()
        }
        # 多家都是 OpenAI 兼容接口，只是 base_url + key 不同。
        # 按顺序找第一个有值的，方便随时切：
        #   xAI Grok  → MURMUR_API_KEY + MURMUR_BASE_URL=https://api.x.ai/v1
        #   OpenCode  → OPENCODE_API_KEY（默认）
        key = (
            os.getenv("MURMUR_API_KEY")
            or os.getenv("OPENCODE_API_KEY")
            or os.getenv("XAI_API_KEY")
            or None
        )
        db_path = Path(os.getenv("MURMUR_DB", "./murmur.db")).expanduser()
        raw_log = os.getenv("MURMUR_LOGDIR", "").strip()
        log_dir = Path(raw_log).expanduser() if raw_log else db_path.parent / "logs"
        return cls(
            api_key=key,
            base_url=os.getenv("MURMUR_BASE_URL", DEFAULT_BASE_URL),
            model=os.getenv("MURMUR_MODEL", DEFAULT_MODEL),
            memory_model=os.getenv("MURMUR_MEMORY_MODEL") or None,
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
            auto_enroll=os.getenv("MURMUR_AUTO_ENROLL", "1").strip().lower()
            not in ("0", "false", "no", "off"),
            web_token=os.getenv("MURMUR_WEB_TOKEN") or None,
            db_path=db_path,
            log_dir=log_dir,
            tz=ZoneInfo(os.getenv("MURMUR_TZ", "Asia/Shanghai")),
        )
