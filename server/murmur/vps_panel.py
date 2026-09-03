"""VPS 面板的后端：用本机已有的 SSH 别名在 VPS 上跑状态命令和邀请码 CLI。

为什么不给 VPS 的 app-api 加管理端点：那要动生产服务、新增鉴权、重新部署。
SSH 凭据本机本来就有，走 SSH 的攻击面是零增量——面板只绑 127.0.0.1，
鉴权复用看板自己的 MURMUR_WEB_TOKEN。

邀请码明文只在 create_invite 这一次响应里出现：不写日志、不落盘、不进
浏览历史以外的任何地方（前端也只展示，不存储）。
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass

log = logging.getLogger("murmur.vps")

REMOTE_DIR = "/opt/murmur"
SERVICES = ["murmur-app-api", "murmur-app-worker", "murmur-web", "caddy"]
LOG_FILES = {
    "app-api": "murmur_app_api.log",
    "app-worker": "murmur_app_worker.log",
}

# 生产连接只认本机 SSH config 中的别名；主机、用户和密钥位置由该别名维护。
DEFAULT_SSH_TARGET = "murmur-new-vps"

# 与 web.py 的 _SECRET_PATTERNS 同款：日志尾巴要上页面，token 不能跟着上去。
_SECRET_PATTERNS = [
    re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(access_?token|client_secret|api_?key)\"?\s*[:=]\s*\"?[A-Za-z0-9_\-\.]{8,}"),
]


def _redact(text: str, secrets: Iterable[str | None] = ()) -> str:
    """先按 .env 里的真值精确擦，再按模式兜底——和 web.py 的 _redact 同一套。
    只有模式层的话，日志里出现的密钥只要长得不走样（比如自定义的
    api key）就原样上页面了。"""
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "***")
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: m.group(0)[:6] + "***", text)
    return text


@dataclass
class VpsConfig:
    """面板使用普通 SSH 目标，默认解析本机 ``murmur-new-vps`` 别名。"""

    ssh_target: str = DEFAULT_SSH_TARGET
    ssh_key: str | None = None

    @classmethod
    def resolve(
        cls,
        ssh_target: str | None = None,
    ) -> VpsConfig:
        """CLI 参数 > 环境变量 > 当前生产 SSH 别名。"""
        env = os.environ
        return cls(ssh_target=ssh_target or env.get("MURMUR_VPS_SSH") or DEFAULT_SSH_TARGET)

    def describe(self) -> str:
        return f"ssh {self.ssh_target}"


@dataclass
class RemoteResult:
    ok: bool
    stdout: str = ""
    detail: str = ""


Runner = Callable[[VpsConfig, str, float], RemoteResult]


def _ssh_argv(cfg: VpsConfig, remote_cmd: str) -> list[str]:
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if cfg.ssh_key:
        argv += ["-i", cfg.ssh_key, "-o", "IdentitiesOnly=yes"]
    return argv + [cfg.ssh_target, remote_cmd]


def _run_remote(cfg: VpsConfig, remote_cmd: str, timeout: float = 30.0) -> RemoteResult:
    """远程命令是 ssh/gcloud 的单个 argv 元素，本机不过 shell；远端 shell
    只解析我们拼好的固定模板（变量一律 shlex.quote 或白名单整形）。"""
    argv = _ssh_argv(cfg, remote_cmd)
    try:
        # 远端输出是 UTF-8（CLI 结果是中文）。text=True 在 Windows 上会拿
        # 本机 ANSI 代码页（GBK）去解码，遇到中文直接炸在 reader 线程里，
        # stdout 变成 None——必须显式指定 utf-8。
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except FileNotFoundError:
        return RemoteResult(False, detail=f"本机没有 {argv[0]}，先装好再试")
    except subprocess.TimeoutExpired:
        return RemoteResult(False, detail=f"连接超时（{timeout:.0f} 秒）")
    except OSError as e:
        return RemoteResult(False, detail=f"{type(e).__name__}: {e}")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        return RemoteResult(
            False, detail=_redact(tail[-1]) if tail else f"退出码 {proc.returncode}"
        )
    return RemoteResult(True, proc.stdout)


def _murmur_cli(sub: str) -> str:
    """以 murmur 用户在 /opt/murmur 里跑 CLI——.env 靠 cwd 自动加载，
    和 scripts/dev-app.sh 是同一个套路。两层 shell，内层整串 quote。"""
    inner = f"cd {REMOTE_DIR} && ./.venv/bin/murmur {sub}"
    return f"sudo -u murmur bash -c {shlex.quote(inner)}"


# ---------------------------------------------------------------------------
# 状态

_STATUS_CMD = """
echo '== services =='
for s in {services}; do printf '%s %s\\n' "$s" "$(systemctl is-active $s 2>&1)"; done
echo '== uptime =='; uptime
echo '== mem =='; free -m | awk '/^Mem:/{{print $2, $3, $7}}'
echo '== disk =='; df -h / | awk 'NR==2{{print $2, $3, $5}}'
{logs}
""".strip()


def _status_cmd() -> str:
    logs = []
    for name, fname in LOG_FILES.items():
        logs.append(
            f"echo '== log {name} =='; "
            f"sudo tail -n 30 {REMOTE_DIR}/logs/{fname} 2>&1 | cut -c1-200"
        )
    return _STATUS_CMD.format(services=" ".join(SERVICES), logs="\n".join(logs))


def _sections(text: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    cur: str | None = None
    for line in text.splitlines():
        m = re.match(r"^== (.+) ==$", line.strip())
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v).strip("\n") for k, v in out.items()}


def _timeout(cfg: VpsConfig, fast: float) -> float:
    """保留统一的 timeout 入口，当前直连 SSH 直接使用调用方预算。"""
    del cfg
    return fast


def status(
    cfg: VpsConfig,
    runner: Runner | None = None,
    secrets: Iterable[str | None] = (),
) -> dict:
    """一条 SSH 拿全：服务状态、负载、内存、磁盘、两个日志尾巴。

    secrets 是 .env 里的密钥真值（web.py 的 _secrets），日志尾巴脱敏
    先按它们精确擦、再按模式兜底。"""
    run = runner or _run_remote
    res = run(cfg, _status_cmd(), _timeout(cfg, 45.0))
    if not res.ok:
        return {"ok": False, "detail": res.detail, "target": cfg.describe()}

    sec = _sections(res.stdout)
    services = {}
    for line in sec.get("services", "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            services[parts[0]] = parts[1].strip()

    uptime_raw = sec.get("uptime", "").strip()
    load = None
    if m := re.search(r"load averages?:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)", uptime_raw):
        load = [float(x) for x in m.groups()]
    up = ""
    if m := re.search(r"\bup\s+(.+?),\s*\d+ user", uptime_raw):
        up = m.group(1).strip()

    mem = None
    if m := re.match(r"^(\d+)\s+(\d+)\s+(\d+)", sec.get("mem", "")):
        mem = {"total_mb": int(m.group(1)), "used_mb": int(m.group(2)),
               "available_mb": int(m.group(3))}

    disk = None
    if m := re.match(r"^(\S+)\s+(\S+)\s+(\d+)%", sec.get("disk", "")):
        disk = {"size": m.group(1), "used": m.group(2), "use_percent": int(m.group(3))}

    logs = {}
    for name in LOG_FILES:
        logs[name] = [_redact(x, secrets)
                      for x in sec.get(f"log {name}", "").splitlines()]

    return {
        "ok": True,
        "target": cfg.describe(),
        "services": services,
        "uptime": up,
        "load": load,
        "mem": mem,
        "disk": disk,
        "logs": logs,
    }


# ---------------------------------------------------------------------------
# 邀请码 / 用户

def list_invites(cfg: VpsConfig, runner: Runner | None = None) -> dict:
    """app-invites + app-users。输出是给人看的文本，按行解析回结构化。"""
    run = runner or _run_remote
    res = run(cfg, _murmur_cli("app-invites"), _timeout(cfg, 30.0))
    if not res.ok:
        return {"ok": False, "detail": res.detail}
    invites = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("（"):
            continue
        parts = re.split(r"\s{2,}", line)
        if len(parts) < 5:
            continue
        invites.append({
            "id": parts[0], "kind": parts[1],
            "alias": None if parts[2] == "-" else parts[2],
            "expiry": parts[3], "usage": parts[4],
        })

    res = run(cfg, _murmur_cli("app-users"), _timeout(cfg, 30.0))
    users: list[str] = []
    if res.ok:
        users = [
            ln.strip() for ln in res.stdout.splitlines()
            if ln.strip() and not ln.strip().startswith("（")
        ]
    return {"ok": True, "invites": invites, "users": users}


_ALIAS_BAD = re.compile(r"[\x00-\x1f\"'`\\$;|&<>()]")


def _clean_alias(alias: str | None) -> str | None:
    """备注只供管理员辨认。清洗完之后还要过 shlex.quote，双保险。"""
    if not alias:
        return None
    cleaned = _ALIAS_BAD.sub("", alias).strip()[:40]
    return cleaned or None


def create_invite(
    cfg: VpsConfig,
    alias: str | None = None,
    days: int = 7,
    reusable: bool = False,
    runner: Runner | None = None,
) -> dict:
    """返回的 code 是明文，只在这一次响应里出现。"""
    try:
        days = int(days)
    except (TypeError, ValueError):
        return {"ok": False, "detail": "天数必须是整数"}
    if not 1 <= days <= 365:
        return {"ok": False, "detail": "天数要在 1–365 之间"}

    sub = f"app-invite --days {days}"
    if cleaned := _clean_alias(alias):
        sub += f" --alias {shlex.quote(cleaned)}"
    if reusable:
        sub += " --reusable"

    run = runner or _run_remote
    res = run(cfg, _murmur_cli(sub), _timeout(cfg, 30.0))
    if not res.ok:
        return {"ok": False, "detail": res.detail}
    # 邀请码约定为最后一行非空输出：CLI 万一先打印一行警告，
    # 取第一行就会把警告当邀请码展示出去。
    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    if not lines:
        return {"ok": False, "detail": "远端没有输出邀请码"}
    return {"ok": True, "code": lines[-1], "days": days, "reusable": reusable}
