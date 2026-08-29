"""Deployment contracts for service logs managed by Murmur logrotate."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "infra" / "deploy"
RUN_LOGGED = ROOT / "scripts" / "ops" / "run_logged_service.py"

SERVICE_LOGS = {
    "murmur-app-api.service": "/opt/murmur/logs/murmur_app_api.log",
    "murmur-app-worker.service": "/opt/murmur/logs/murmur_app_worker.log",
    "murmur-web.service": "/opt/murmur/logs/murmur_web.log",
    "murmur-telegram.service": "/opt/murmur/test/logs/murmur_bot.log",
    "murmur-dingtalk.service": "/opt/murmur/test/logs/murmur_dingtalk.log",
    "murmur-wechat.service": "/opt/murmur/test/logs/murmur_wechat.log",
    "murmur-qq.service": "/opt/murmur/test/logs/murmur_qq.log",
}


class DeployLoggingTests(unittest.TestCase):
    def test_services_open_logs_as_murmur_without_truncating_or_following_links(self) -> None:
        logrotate = (DEPLOY / "murmur-logrotate").read_text(encoding="utf-8")
        self.assertRegex(logrotate, r"(?m)^\s*su murmur murmur\s*$")

        for unit_name, log_path in SERVICE_LOGS.items():
            unit = DEPLOY / unit_name
            body = unit.read_text(encoding="utf-8")
            self.assertIn("User=murmur", body)
            self.assertNotIn("StandardOutput=append:", body)
            self.assertIn(f"ReadWritePaths={Path(log_path).parent}", body)
            self.assertIn(
                "ExecStart=/opt/murmur/.venv/bin/python "
                "/opt/murmur/scripts/ops/run_logged_service.py "
                f"{log_path} ",
                body,
            )

        with tempfile.TemporaryDirectory(prefix="murmur-service-log.") as directory:
            root = Path(directory)
            log = root / "service.log"
            log.write_text("before\n", encoding="utf-8")
            log.chmod(0o666)
            result = subprocess.run(
                [
                    sys.executable,
                    str(RUN_LOGGED),
                    str(log),
                    sys.executable,
                    "-c",
                    "import sys; print('stdout', flush=True); "
                    "print('stderr', file=sys.stderr, flush=True)",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text(encoding="utf-8"), "before\nstdout\nstderr\n")
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)

            target = root / "must-not-change"
            target.write_text("safe\n", encoding="utf-8")
            link = root / "linked.log"
            os.symlink(target, link)
            refused = subprocess.run(
                [sys.executable, str(RUN_LOGGED), str(link), "/usr/bin/true"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "safe\n")

            fifo = root / "fifo.log"
            os.mkfifo(fifo)
            refused_fifo = subprocess.run(
                [sys.executable, str(RUN_LOGGED), str(fifo), "/usr/bin/true"],
                check=False,
                capture_output=True,
                text=True,
                timeout=1,
            )
            self.assertNotEqual(refused_fifo.returncode, 0)


if __name__ == "__main__":
    unittest.main()
