"""VPS 面板的后端：用本机已有的 gcloud / ssh 凭据在 VPS 上跑只读状态命令和邀请码 CLI。

为什么不给 VPS 的 app-api 加管理端点：那要动生产服务、新增鉴权、重新部署。
SSH 凭据本机本来就有，走 SSH 的攻击面是零增量——面板只绑 127.0.0.1，
鉴权复用看板自己的 MURMUR_WEB_TOKEN。

邀请码明文只在 create_invite 这一次响应里出现：不写日志、不落盘、不进
浏览历史以外的任何地方（前端也只展示，不存储）。
"""

from __future__ import annotations

import getpass
import logging
import os
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("murmur.vps")

REMOTE_DIR = "/opt/murmur"
SERVICES = ["murmur-app-api", "murmur-app-worker", "murmur-web", "caddy"]
LOG_FILES = {
    "app-api": "murmur_app_api.log",
    "app-worker": "murmur_app_worker.log",
}

# 现网实例。README 的迁移命令里写的就是这三个值，面板零配置默认同一份。
DEFAULT_INSTANCE = "instance-20260516-162140"
DEFAULT_ZONE = "us-west1-b"

# 与 web.py 的 _SECRET_PATTERNS 同款：日志尾巴要上页面，token 不能跟着上去。
_SECRET_PATTERNS = [
    re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(access_?token|client_secret|api_?key)\"?\s*[:=]\s*\"?[A-Za-z0-9_\-\.]{8,}"),
]


def _redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: m.group(0)[:6] + "***", text)
    return text


@dataclass
class VpsConfig:
    """连接方式二选一：gcloud 实例，或普通 ssh user@host。

    gcloud compute ssh 每次调用要 60 秒往上（刷新 token、检查 key），
    面板一次刷新要跑好几条命令，根本没法用。所以 gcloud 只在启动时用一次
    （describe 拿外网 IP），之后都走直连 ssh + ~/.ssh/google_compute_engine。
    """

    gcloud_instance: str | None = None
    zone: str | None = None
    project: str | None = None
    ssh_target: str | None = None
    ssh_key: str | None = None

    @classmethod
    def resolve(
        cls,
        gcloud_instance: str | None = None,
        zone: str | None = None,
        project: str | None = None,
        ssh_target: str | None = None,
    ) -> VpsConfig:
        """CLI 参数 > 环境变量 > 现网默认值。"""
        env = os.environ
        ssh = ssh_target or env.get("MURMUR_VPS_SSH") or None
        if ssh:
            return cls(ssh_target=ssh)
        return cls(
            gcloud_instance=gcloud_instance
            or env.get("MURMUR_VPS_GCLOUD_INSTANCE")
            or DEFAULT_INSTANCE,
            zone=zone or env.get("MURMUR_VPS_ZONE") or DEFAULT_ZONE,
            project=project or env.get("MURMUR_VPS_PROJECT") or None,
        )

    def describe(self) -> str:
        if self.ssh_target:
            return f"ssh {self.ssh_target}"
        return f"gcloud {self.gcloud_instance} ({self.zone})"


GCLOUD_SSH_KEY = Path.home() / ".ssh" / "google_compute_engine"


def resolve_direct_ssh(cfg: VpsConfig, timeout: float = 25.0) -> VpsConfig | None:
    """把 gcloud 实例解析成直连 ssh 目标（用户@外网 IP）。失败返回 None，
    调用方退回慢速的 gcloud compute ssh。"""
    if not cfg.gcloud_instance or not GCLOUD_SSH_KEY.exists():
        return None
    argv = [
        "gcloud", "compute", "instances", "describe", cfg.gcloud_instance,
        "--format=get(networkInterfaces[0].accessConfigs[0].natIP)",
    ]
    if cfg.zone:
        argv += ["--zone", cfg.zone]
    if cfg.project:
        argv += ["--project", cfg.project]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    ip = proc.stdout.strip()
    if proc.returncode != 0 or not re.fullmatch(r"[\d.]{7,15}", ip):
        return None
    # gcloud compute ssh 默认用本机用户名登录（metadata key 的属主）
    return VpsConfig(
        ssh_target=f"{getpass.getuser()}@{ip}", ssh_key=str(GCLOUD_SSH_KEY),
    )


@dataclass
class RemoteResult:
    ok: bool
    stdout: str = ""
    detail: str = ""


Runner = Callable[[VpsConfig, str, float], RemoteResult]


def _ssh_argv(cfg: VpsConfig, remote_cmd: str) -> list[str]:
    if cfg.ssh_target:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        if cfg.ssh_key:
            argv += ["-i", cfg.ssh_key, "-o", "IdentitiesOnly=yes"]
        return argv + [cfg.ssh_target, remote_cmd]
    argv = ["gcloud", "compute", "ssh", cfg.gcloud_instance or ""]
    if cfg.zone:
        argv += ["--zone", cfg.zone]
    if cfg.project:
        argv += ["--project", cfg.project]
    argv += ["--command", remote_cmd]
    return argv


def _run_remote(cfg: VpsConfig, remote_cmd: str, timeout: float = 30.0) -> RemoteResult:
    """远程命令是 ssh/gcloud 的单个 argv 元素，本机不过 shell；远端 shell
    只解析我们拼好的固定模板（变量一律 shlex.quote 或白名单整形）。"""
    argv = _ssh_argv(cfg, remote_cmd)
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
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
    """gcloud compute ssh 每次要一分钟上下，直连 ssh 几秒就够。"""
    return fast * 3 if cfg.gcloud_instance else fast


def status(cfg: VpsConfig, runner: Runner | None = None) -> dict:
    """一条 SSH 拿全：服务状态、负载、内存、磁盘、两个日志尾巴。"""
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
        logs[name] = [_redact(x) for x in sec.get(f"log {name}", "").splitlines()]

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
    code = next((ln.strip() for ln in res.stdout.splitlines() if ln.strip()), "")
    if not code:
        return {"ok": False, "detail": "远端没有输出邀请码"}
    return {"ok": True, "code": code, "days": days, "reusable": reusable}
