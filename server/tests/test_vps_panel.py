"""VPS 面板：解析、命令拼接和授权这些"笨但必须对"的地方。

真正危险的就两处：
1. 邀请码参数要拼进远端 shell 命令——alias 是用户输入，清洗/转义漏了就是注入。
2. 面板有写接口（建邀请码），配了 MURMUR_WEB_TOKEN 时不带 token 必须 401。

    python tests/test_vps_panel.py
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _helpers import make_config  # noqa: E402

from murmur import vps_panel  # noqa: E402
from murmur.vps_panel import RemoteResult, VpsConfig  # noqa: E402
from murmur.web import Handler  # noqa: E402

ok = fail = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


STATUS_SAMPLE = """== services ==
murmur-app-api active
murmur-app-worker active
murmur-web inactive
caddy active
== uptime ==
 09:06:50 up 91 days, 16:12,  1 user,  load average: 0.10, 0.05, 0.01
== mem ==
3907 1174 2733
== disk ==
28G 5.8G 21%
== log app-api ==
INFO: token=sk-abcdef0123456789 in a log line
== log app-worker ==
worker tick
"""

INVITES_SAMPLE = """a1b2c3d4  user  给朋友的  2026-08-23 09:00:00  用量 0/1
e5f6a7b8  device  -  2026-08-16 09:30:00  用量 0/1
"""


def fake_runner(stdout: str = "", ok_: bool = True, detail: str = ""):
    """记录收到的远程命令，返回固定输出。"""
    calls: list[str] = []

    def run(cfg: VpsConfig, cmd: str, timeout: float) -> RemoteResult:
        calls.append(cmd)
        return RemoteResult(ok_, stdout, detail)

    run.calls = calls
    return run


cfg = VpsConfig(gcloud_instance="inst", zone="z", project="p")

print("\n── status：远端文本解析成结构化数据 " + "─" * 22)

d = vps_panel.status(cfg, runner=fake_runner(STATUS_SAMPLE))
check("整体 ok", d["ok"] is True)
check("四个服务都解析出来", d["services"] == {
    "murmur-app-api": "active", "murmur-app-worker": "active",
    "murmur-web": "inactive", "caddy": "active",
}, str(d.get("services")))
check("uptime 只留 up 段", d["uptime"] == "91 days, 16:12", d.get("uptime", ""))
check("load 三元组", d["load"] == [0.10, 0.05, 0.01], str(d.get("load")))
check("内存 MB", d["mem"] == {"total_mb": 3907, "used_mb": 1174, "available_mb": 2733})
check("磁盘百分比是整数", d["disk"] == {"size": "28G", "used": "5.8G", "use_percent": 21})
check("日志尾巴里的 key 被模式脱敏",
      "sk-abcdef0123456789" not in "\n".join(d["logs"]["app-api"]),
      str(d["logs"]["app-api"]))

d = vps_panel.status(cfg, runner=fake_runner(ok_=False, detail="连接超时（45 秒）"))
check("远端失败时不抛异常，回结构化错误",
      d["ok"] is False and "超时" in d["detail"])

print("\n── list_invites：CLI 文本解析 " + "─" * 30)

run = fake_runner(INVITES_SAMPLE)
d = vps_panel.list_invites(cfg, runner=run)
check("两条邀请码", len(d["invites"]) == 2, str(d.get("invites")))
inv = d["invites"][0]
check("字段各就各位", inv["id"] == "a1b2c3d4" and inv["kind"] == "user"
      and inv["alias"] == "给朋友的" and inv["usage"] == "用量 0/1", str(inv))
check("'-' 备注变 None", d["invites"][1]["alias"] is None)
check("两次远程调用：invites + users", len(run.calls) == 2
      and "app-invites" in run.calls[0] and "app-users" in run.calls[1])

d = vps_panel.list_invites(cfg, runner=fake_runner("（没有未使用的邀请码）\n"))
check("空列表不是错误", d["ok"] and d["invites"] == [] and d["users"] == [])

d = vps_panel.list_invites(cfg, runner=fake_runner(ok_=False, detail="sudo 不通"))
check("远端失败回结构化错误", d["ok"] is False and "sudo 不通" in d["detail"])

print("\n── create_invite：参数拼接与注入防护 " + "─" * 22)

run = fake_runner("MUR-ABCD-1234\n")
d = vps_panel.create_invite(cfg, alias="给朋友", days=7, runner=run)
check("拿到明文码", d["ok"] and d["code"] == "MUR-ABCD-1234")
check("以 murmur 用户在 /opt/murmur 里跑",
      "sudo -u murmur" in run.calls[0] and "/opt/murmur" in run.calls[0],
      run.calls[0])


def murmur_argv(remote_cmd: str) -> list[str]:
    """模拟远端两层 shell 的解析：外面一层剥出 bash -c 的脚本，
    里面一层剥出 && 之后的 murmur 命令行。"""
    import shlex

    inner = shlex.split(remote_cmd)[-1]
    return shlex.split(inner.split(" && ", 1)[1])


argv = murmur_argv(run.calls[0])
check("alias 最终是完整的一个参数",
      argv[argv.index("--alias") + 1] == "给朋友", str(argv))
check("默认不带 --reusable", "--reusable" not in run.calls[0])

run = fake_runner("MUR-XXXX-0000\n")
evil = 'x"; rm -rf /; echo "\n$(id)`id`$HOME |&<>()'
d = vps_panel.create_invite(cfg, alias=evil, days=3, reusable=True, runner=run)
sent = run.calls[0]
alias_arg = murmur_argv(sent)[murmur_argv(sent).index("--alias") + 1]
check("危险字符被洗掉", alias_arg == "x rm -rf / echo ididHOME", repr(alias_arg))
check("换行进不了命令", "\n" not in sent, sent)
check("--reusable 带上了", "--reusable" in murmur_argv(sent))

check("天数 0 被拒", not vps_panel.create_invite(cfg, days=0, runner=fake_runner())["ok"])
check("天数 366 被拒",
      not vps_panel.create_invite(cfg, days=366, runner=fake_runner())["ok"])
check("天数不是数字被拒",
      not vps_panel.create_invite(cfg, days="7; id", runner=fake_runner())["ok"])

d = vps_panel.create_invite(cfg, runner=fake_runner(ok_=False, detail="超时"))
check("远端失败不返回空码", d["ok"] is False and "code" not in d)
d = vps_panel.create_invite(cfg, runner=fake_runner("  \n"))
check("远端空输出是错误", d["ok"] is False)
d = vps_panel.create_invite(
    cfg, runner=fake_runner("WARNING: sudo: 有一行警告\nMUR-LAST-9999\n"))
check("邀请码取最后一行非空输出（前面可能是警告）",
      d["ok"] and d["code"] == "MUR-LAST-9999", str(d))

print("\n── status 脱敏：.env 真值先精确擦，模式兜底 " + "─" * 14)

# 这个密钥长得不像任何已知模式，只有按真值精确擦才能擦掉
secret_sample = STATUS_SAMPLE.replace("sk-abcdef0123456789",
                                      "plain-custom-secret-9")
d = vps_panel.status(cfg, runner=fake_runner(secret_sample),
                     secrets=["plain-custom-secret-9"])
check("不像已知模式的密钥也按真值擦掉",
      "plain-custom-secret-9" not in "\n".join(d["logs"]["app-api"]),
      str(d["logs"]["app-api"]))
d = vps_panel.status(cfg, runner=fake_runner(secret_sample))
check("不传 secrets 时只剩模式层（对照组，证明是真值层在起作用）",
      "plain-custom-secret-9" in "\n".join(d["logs"]["app-api"]))

print("\n── VpsConfig / ssh 参数拼接 " + "─" * 30)

import os  # noqa: E402

saved = {k: os.environ.get(k) for k in
         ("MURMUR_VPS_SSH", "MURMUR_VPS_GCLOUD_INSTANCE", "MURMUR_VPS_ZONE")}
try:
    os.environ.pop("MURMUR_VPS_SSH", None)
    os.environ.pop("MURMUR_VPS_GCLOUD_INSTANCE", None)
    os.environ.pop("MURMUR_VPS_ZONE", None)
    c = VpsConfig.resolve()
    check("零配置落到现网默认实例",
          c.gcloud_instance == vps_panel.DEFAULT_INSTANCE
          and c.zone == vps_panel.DEFAULT_ZONE)
    os.environ["MURMUR_VPS_GCLOUD_INSTANCE"] = "other-inst"
    check("环境变量覆盖默认实例",
          VpsConfig.resolve().gcloud_instance == "other-inst")
    os.environ["MURMUR_VPS_SSH"] = "me@example.com"
    check("给了 ssh 就不用 gcloud",
          VpsConfig.resolve().ssh_target == "me@example.com"
          and VpsConfig.resolve().gcloud_instance is None)
finally:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

argv = vps_panel._ssh_argv(VpsConfig(ssh_target="me@example.com"), "uptime")
check("ssh 模式批处理不交互",
      argv[:2] == ["ssh", "-o"] and "BatchMode=yes" in argv and argv[-1] == "uptime")
argv = vps_panel._ssh_argv(
    VpsConfig(ssh_target="me@example.com", ssh_key="/tmp/k"), "uptime")
check("带了 key 就指定 identities",
      "-i" in argv and "/tmp/k" in argv and "IdentitiesOnly=yes" in argv)
argv = vps_panel._ssh_argv(cfg, "uptime")
check("gcloud 模式带实例/区/项目",
      argv[:3] == ["gcloud", "compute", "ssh"] and "inst" in argv
      and "--zone" in argv and "--project" in argv)

print("\n── resolve_direct_ssh：gcloud 只用来拿 IP " + "─" * 22)

import subprocess  # noqa: E402

real_run = subprocess.run
real_key = vps_panel.GCLOUD_SSH_KEY


class FakeProc:
    def __init__(self, out: str, code: int = 0):
        self.stdout, self.returncode = out, code


class FakeKey:
    """Path 实例不让挂属性，整个换掉模块常量。"""

    def __init__(self, present: bool):
        self.present = present

    def exists(self) -> bool:
        return self.present

    def __str__(self) -> str:
        return "/home/me/.ssh/google_compute_engine"


try:
    vps_panel.GCLOUD_SSH_KEY = FakeKey(True)
    subprocess.run = lambda *a, **kw: FakeProc("34.82.10.20\n")
    d = vps_panel.resolve_direct_ssh(cfg)
    import getpass
    check("解析成 用户@IP 直连",
          d is not None and d.ssh_target == f"{getpass.getuser()}@34.82.10.20"
          and d.ssh_key.endswith("google_compute_engine"), str(d))

    subprocess.run = lambda *a, **kw: FakeProc("", 1)
    check("describe 失败回 None（退回 gcloud 慢速模式）",
          vps_panel.resolve_direct_ssh(cfg) is None)

    subprocess.run = lambda *a, **kw: FakeProc("INSTANCE_GROUPS\nnot-an-ip\n")
    check("输出不是 IP 回 None", vps_panel.resolve_direct_ssh(cfg) is None)

    vps_panel.GCLOUD_SSH_KEY = FakeKey(False)
    check("本机没有 gcloud key 回 None", vps_panel.resolve_direct_ssh(cfg) is None)
finally:
    subprocess.run = real_run
    vps_panel.GCLOUD_SSH_KEY = real_key

print("\n── web 层：写接口必须过鉴权 " + "─" * 30)

real_create = vps_panel.create_invite
calls = []


def stub_create(vcfg, alias=None, days=7, reusable=False, runner=None):
    calls.append({"alias": alias, "days": days, "reusable": reusable})
    return {"ok": True, "code": "MUR-TEST-CODE"}


vps_panel.create_invite = stub_create
httpd = ThreadingHTTPServer(
    ("127.0.0.1", 0),
    partial(Handler, make_config(web_token="s3cret"), cfg),
)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"
body = json.dumps({"alias": "测试", "days": 3, "reusable": True}).encode()
try:
    req = urllib.request.Request(f"{base}/api/vps/invite", data=body)
    try:
        urllib.request.urlopen(req, timeout=5)
        check("不带 token 建邀请码被拒", False, "居然 200 了")
    except urllib.error.HTTPError as e:
        check("不带 token 建邀请码被拒", e.code == 401, str(e.code))

    req = urllib.request.Request(
        f"{base}/api/vps/invite", data=body,
        headers={"X-Murmur-Token": "s3cret",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        d = json.loads(r.read())
    check("带 token 能建，返回明文码", d.get("ok") and d.get("code") == "MUR-TEST-CODE",
          str(d))
    check("参数原样透传",
          calls == [{"alias": "测试", "days": 3, "reusable": True}], str(calls))
finally:
    httpd.shutdown()
    httpd.server_close()
    vps_panel.create_invite = real_create


print("\n── web 层：/api/vps/status 有缓存，不叠加 ssh " + "─" * 14)

real_status = vps_panel.status
status_calls = []


def stub_status(vcfg, runner=None, secrets=()):
    status_calls.append({"secrets": list(secrets)})
    return {"ok": True, "target": "stub"}


vps_panel.status = stub_status
Handler._vps_status_cache = None
httpd = ThreadingHTTPServer(
    ("127.0.0.1", 0),
    partial(Handler,
            make_config(web_token="s3cret", api_key="sk-live-secret-1"), cfg),
)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"
try:
    req = urllib.request.Request(f"{base}/api/vps/status",
                                 headers={"X-Murmur-Token": "s3cret"})
    with urllib.request.urlopen(req, timeout=5) as r:
        d1 = json.loads(r.read())
    with urllib.request.urlopen(req, timeout=5) as r:
        d2 = json.loads(r.read())
    check("两次请求只打一次 ssh（20 秒内走缓存）",
          len(status_calls) == 1, str(len(status_calls)))
    check("缓存返回同样的内容", d1 == d2 == {"ok": True, "target": "stub"})
    check(".env 里的密钥真值传给了远端日志脱敏",
          "sk-live-secret-1" in status_calls[0]["secrets"],
          str(status_calls))
finally:
    httpd.shutdown()
    httpd.server_close()
    vps_panel.status = real_status
    Handler._vps_status_cache = None

print("\n── web 层：/api/balance 的 hours 要校验、要有上限 " + "─" * 10)

import sqlite3  # noqa: E402
import tempfile  # noqa: E402

from murmur.web import BALANCE_SCHEMA  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    dbp = Path(tmp) / "q.db"
    conn = sqlite3.connect(dbp)
    conn.executescript(BALANCE_SCHEMA)
    conn.execute(
        "INSERT INTO balance_snapshots (at, ok) VALUES (?, 1)",
        (("2020-01-01T00:00:00+00:00"),))  # 远超任何合理窗口的旧点
    conn.commit()
    conn.close()

    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(Handler, make_config(dbp, web_token="s3cret"), cfg),
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get_balance(qs: str):
        req = urllib.request.Request(f"{base}/api/balance{qs}",
                                     headers={"X-Murmur-Token": "s3cret"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        code, d = get_balance("?hours=abc")
        check("hours 不是整数返回 400 而不是 500", code == 400, str(code))
        # 2020 年的点在 90 天上限之外：能查到它说明 hours 没被 clamp
        code, d = get_balance("?hours=99999999")
        check("hours 再大也被 clamp 到 90 天，不会全表扫",
              code == 200 and d["points"] == [], str(d.get("points")))
        code, d = get_balance("?hours=-5")
        check("负数被抬到下限，不炸",
              code == 200 and d["points"] == [], str(code))
    finally:
        httpd.shutdown()
        httpd.server_close()


print(f"\n{'─' * 60}\n通过 {ok}，失败 {fail}")
sys.exit(1 if fail else 0)
