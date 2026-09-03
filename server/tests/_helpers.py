"""测试用的小工具。

`make_config` 存在的理由：测试里手写 `Config(...)` 的全部字段，
每往配置里加一个字段，所有测试都会 TypeError 断掉——加 `auto_enroll`
那次就是这样，两个文件同时红了，但真正的代码一点问题都没有。

这里改成按 dataclass 的字段自动填默认值，只覆盖测试关心的那几个。
"""

from __future__ import annotations

import unittest
from dataclasses import fields
from pathlib import Path
from zoneinfo import ZoneInfo

from murmur.config import Config

# 按类型给的兜底值。写不出来的类型在下面 _EXPLICIT 里点名。
_BY_TYPE = {
    "str | None": None,
    "str": "",
    "bool": False,
    "float | None": None,
    "set[int]": set(),
    "set[str]": set(),
    "list[str]": [],
    "list[list[str]]": [],
}

# 这几个有类型之外的语义，单独给
_EXPLICIT = {
    "api_key": "test-key",
    "model": "test-model",
    "base_url": "https://example.invalid/v1",
    # 引擎测试断言主模型带 response_format；默认走真实生产行为
    "json_schema": True,
    # 日志目录：测试里不该碰真实的 logs/，指到临时目录
    "log_dir": Path("/tmp/murmur-test-logs"),
}


def make_config(db_path: str | Path = "/tmp/murmur-test.db", **overrides) -> Config:
    """造一个测试用的 Config。只写你关心的字段，其余自动填。"""
    values: dict = {}
    for f in fields(Config):
        if f.name in overrides:
            values[f.name] = overrides.pop(f.name)
        elif f.name == "db_path":
            values[f.name] = Path(db_path)
        elif f.name == "tz":
            values[f.name] = ZoneInfo("Asia/Shanghai")
        elif f.name in _EXPLICIT:
            values[f.name] = _EXPLICIT[f.name]
        elif (t := str(f.type)) in _BY_TYPE:
            v = _BY_TYPE[t]
            values[f.name] = type(v)(v) if isinstance(v, (set, list)) else v
        else:
            raise AssertionError(
                f"Config 新增了字段 {f.name}: {f.type}，"
                f"在 tests/_helpers.py 里给它一个测试默认值"
            )
    if overrides:
        raise AssertionError(f"Config 没有这些字段：{sorted(overrides)}")
    return Config(**values)


class EnrolledClient:
    """One development-mode device already through enrolment.

    Every authenticated request needs its own challenge, and a challenge is
    single-use — reusing one reads as `invalid_challenge`, which looks like a
    signing bug rather than the test holding a spent token.  `headers()` mints
    a fresh one per call so a test never has to think about it.
    """

    def __init__(self, client, store, development_token: str,
                 *, key_id: str = "dev-api-phone"):
        self.client = client
        self.development = {"X-Murmur-Development-Token": development_token}
        invite = store.create_invite()
        challenge = client.post(
            "/v1/auth/challenges", json={"purpose": "enrollment"}
        ).json()
        response = client.post(
            "/v1/enrollments", headers=self.development,
            json={
                "challenge_id": challenge["challenge_id"], "invite_code": invite,
                "key_id": key_id, "environment": "development",
                "device_name": "iPhone",
            },
        )
        if response.status_code != 201:
            raise AssertionError(f"enrolment failed: {response.text}")
        self.identity = response.json()

    @property
    def user_id(self) -> str:
        return self.identity["user_id"]

    def headers(self) -> dict:
        response = self.client.post(
            "/v1/auth/challenges", headers=self.development,
            json={"purpose": "request", "key_id": self.identity["key_id"]},
        )
        return {
            **self.development,
            "X-Murmur-Key-ID": self.identity["key_id"],
            "X-Murmur-Challenge-ID": response.json()["challenge_id"],
        }


def run_unittest(*, verbosity: int = 2) -> None:
    """Run the calling script's unittest suite with the repo-standard summary."""
    program = unittest.main(verbosity=verbosity, exit=False)
    result = program.result
    failed = (
        len(result.failures)
        + len(result.errors)
        + len(result.unexpectedSuccesses)
    )
    print(f"\n通过 {result.testsRun - failed}，失败 {failed}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
