"""Exercise the deployed bridge against real isolated Git repositories.

Run with an interpreter containing the project's python-dotenv dependency.
No sudo, network, real systemd, host installation, or user repository mutation.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def updater_source() -> Path:
    here = Path(__file__).resolve()
    for candidate in (
        here.with_name("murmur-update"),
        here.parent.parent / "deploy/murmur-update",
        here.parent.parent.parent / "infra/deploy/murmur-update",
    ):
        if candidate.is_file():
            return candidate
    raise RuntimeError("Cannot locate bridge updater")


UPDATER = updater_source()
SERVICES = ("murmur-app-worker", "murmur-app-api", "murmur-web")


class UpdaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="murmur-updater-test.", dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.seed = self.root / "seed"
        self.origin = self.root / "origin.git"
        self.host = self.root / "host"
        self.units = self.host / "units"
        self.log = self.root / "events.jsonl"
        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("GIT_", "MURMUR_"))
        }
        self.env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        self.write(self.root / ".murmur-updater-test", "isolated-murmur-updater-test-v1\n")
        for path in (self.root / "bin", self.units):
            path.mkdir(parents=True, exist_ok=True)
        self.git(self.root, "init", "--bare", "--initial-branch=main", str(self.origin))
        self.git(self.root, "init", "--initial-branch=main", str(self.seed))
        self.git(self.seed, "config", "user.name", "Updater Fixture")
        self.git(self.seed, "config", "user.email", "fixture@example.invalid")
        self.git(self.seed, "config", "commit.gpgsign", "false")
        self.git(self.seed, "remote", "add", "origin", str(self.origin))
        self.layout = "old"
        self.write_layout("one", "old")
        self.commit("one")
        self.git(self.seed, "push", "origin", "main")
        self.git(self.root, "clone", str(self.origin), str(self.repo))
        self.old_head = self.git(self.repo, "rev-parse", "HEAD")
        self.write(self.host / "murmur-update", UPDATER.read_text() + "\n# Installed bridge\n", 0o751)
        self.write(self.host / "logrotate", "# Operator's installed logrotate\n", 0o640)
        self.write(self.units / "murmur-update.service", "# Operator's installed updater unit\n", 0o600)
        for service in SERVICES:
            self.write(self.units / f"{service}.service", f"# installed {service}\n", 0o640)
        self.write(self.units / "murmur-telegram.service", "# isolated test bot stays configured as-is\n")
        self.write(self.repo / ".env", 'export MURMUR_DB="runtime/user data.db" # quoted relative path\n')
        self.write(self.repo / "logs/preserved.log", "live-log\n")
        self.write(self.repo / "photos/preserved.jpg", "photo-bytes\n")
        self.write(self.repo / "runtime/key", "not-a-real-secret\n")
        self.database = self.repo / "runtime/user data.db"
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE memories (message TEXT)")
        self.connection.execute("INSERT INTO memories VALUES ('preserve committed WAL data')")
        self.connection.commit()
        self.write(self.root / "enabled", "\n".join(SERVICES) + "\n")
        self.install_fakes()
        self.initial_host = self.host_snapshot()
        self.initial_data = self.data_snapshot()
        self.initial_git_config = self.git_config_snapshot()

    @staticmethod
    def write(path: Path, contents: str, mode: int = 0o644) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        path.chmod(mode)

    def command(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, cwd=cwd, env=self.env, text=True, capture_output=True, timeout=30)

    def git(self, cwd: Path, *args: str) -> str:
        result = self.command(["git", *args], cwd)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def commit(self, message: str) -> str:
        self.git(self.seed, "add", "-A")
        self.git(self.seed, "commit", "-m", message)
        return self.git(self.seed, "rev-parse", "HEAD")

    def write_layout(self, version: str, layout: str) -> None:
        if layout != self.layout:
            if layout == "new":
                (self.seed / "server").mkdir(exist_ok=True)
                for name in ("pyproject.toml", "murmur", "tests"):
                    shutil.move(str(self.seed / name), str(self.seed / "server" / name))
                (self.seed / "infra").mkdir(exist_ok=True)
                shutil.move(str(self.seed / "deploy"), str(self.seed / "infra/deploy"))
                (self.seed / "scripts/check").mkdir(exist_ok=True)
                shutil.move(str(self.seed / "scripts/run_tests.py"), str(self.seed / "scripts/check/run_tests.py"))
            else:
                raise AssertionError("Fixture supports old-to-new migration only")
        self.layout = layout
        server = self.seed / ("server" if layout == "new" else ".")
        deploy = self.seed / ("infra/deploy" if layout == "new" else "deploy")
        runner = self.seed / ("scripts/check/run_tests.py" if layout == "new" else "scripts/run_tests.py")
        self.write(self.seed / ".gitignore", ".env\n.venv/\nbackups/\nruntime/\nlogs/\nphotos/\n*.egg-info/\n")
        self.write(self.seed / "VERSION", version + "\n")
        self.write(self.seed / "apps/ios/source.swift", "// client excluded from VPS\n")
        self.write(self.seed / "apps/android/source.kt", "// client excluded from VPS\n")
        self.write(self.seed / "assets/brand/logo", "brand excluded from VPS\n")
        self.write(server / "pyproject.toml", '[project]\nname = "murmur-fixture"\nversion = "1.0"\n')
        self.write(server / "murmur/__init__.py", f'VERSION = "{version}"\n')
        self.write(server / "tests/test_fixture.py", "print('fixture assertions passed')\n")
        self.write(self.seed / "scripts/ops/safety.sh", "#!/usr/bin/env bash\nexit 0\n")
        self.write(self.seed / "scripts/dev/setup-murmur.sh", "#!/usr/bin/env bash\nexit 0\n")
        self.write(runner, f"from pathlib import Path\nassert Path({str(server.relative_to(self.seed) / 'tests/test_fixture.py')!r}).is_file()\nprint('fixture release checks passed')\n")
        self.write(deploy / "murmur-update", UPDATER.read_text() + f"\n# Release {version}\n", 0o755)
        self.write(deploy / "murmur-update.service", f"# update unit {version}\n[Service]\nType=oneshot\n")
        self.write(deploy / "murmur-logrotate", f"# logrotate {version}\n")
        for service in SERVICES:
            self.write(deploy / f"{service}.service", f"# {version}\n[Service]\nWorkingDirectory={self.repo}\nEnvironmentFile={self.repo}/.env\nReadWritePaths={self.repo}/runtime\nStandardOutput=append:{self.repo}/logs/service.log\n")
        self.write(deploy / "murmur-telegram.service", f"# {version}\n[Service]\nEnvironmentFile={self.repo}/.env.test-bots\nConditionPathExists={self.repo}/test-state\n")
        self.write(deploy / "murmur-optional.service", f"# {version}\n[Service]\nEnvironmentFile=-{self.repo}/does-not-exist\nReadWritePaths=-{self.repo}/optional\n")

    def publish(self, version: str = "two", layout: str = "new") -> str:
        self.write_layout(version, layout)
        revision = self.commit(version)
        self.git(self.seed, "push", "origin", "main")
        return revision

    def install_fakes(self) -> None:
        common = f"""#!{sys.executable}
import json, os, subprocess, sys
from pathlib import Path
root = Path({str(self.root)!r})
repo = root / 'repo'
version = (repo / 'VERSION').read_text().strip()
kind = Path(__file__).name
args = sys.argv[1:]
event = dict(kind=kind, args=args, cwd=os.getcwd(), version=version)
with (root / 'events.jsonl').open('a') as stream:
    stream.write(json.dumps(event) + '\\n')
failure = (root / 'failure').read_text().strip() if (root / 'failure').exists() else ''
def fails(stage):
    failing_version = (root / 'failure-version').read_text().strip() if (root / 'failure-version').exists() else 'two'
    return version == failing_version and failure == stage
"""
        self.write(self.repo / ".venv/bin/pip", common + """
assert Path.cwd() == repo
assert args[:2] == ['install', '-e'] and len(args) == 3
project = args[2]
assert (repo / project / 'pyproject.toml').is_file()
if fails('install'):
    raise SystemExit(41)
(root / 'installed-target').write_text(project)
""", 0o755)
        self.write(self.repo / ".venv/bin/python", common + f"""
assert Path.cwd() == repo
if args and args[0].endswith('run_tests.py') and fails('checks'):
    raise SystemExit(42)
os.execv({sys.executable!r}, [{sys.executable!r}, *args])
""", 0o755)
        self.write(self.root / "bin/install", common + f"""
destination = Path(args[-1])
assert destination.is_relative_to(root / 'host')
if fails('unit-install') and destination.name.startswith('murmur-app-api.service.'):
    raise SystemExit(43)
if fails('self-install') and destination.name.startswith('murmur-update.murmur-new.'):
    raise SystemExit(44)
if fails('logrotate-install') and destination.name.startswith('logrotate.'):
    raise SystemExit(45)
if fails('restore-file') and destination.name.startswith('logrotate.'):
    damaged = root / 'host/murmur-update'
    damaged.unlink()
    damaged.mkdir()
    raise SystemExit(49)
raise SystemExit(subprocess.call([{shutil.which('install')!r}, *args]))
""", 0o755)
        self.write(self.root / "bin/systemctl", common + """
operation = args[0]
assert operation in ('daemon-reload', 'is-enabled', 'restart', 'is-active')
if operation == 'is-enabled':
    enabled = (root / 'enabled').read_text().splitlines()
    raise SystemExit(0 if args[-1] in enabled else 1)
if operation == 'daemon-reload' and fails('daemon-reload'):
    raise SystemExit(46)
if operation == 'restart':
    if args[-1] == 'murmur-app-api' and fails('restart'):
        raise SystemExit(47)
    (root / ('running-' + args[-1])).write_text(version)
if operation == 'is-active' and args[-1] == 'murmur-app-api' and fails('inactive'):
    raise SystemExit(48)
if operation == 'is-active':
    raise SystemExit(0 if (root / ('running-' + args[-1])).is_file() else 3)
""", 0o755)

    def run_update(self, *, success: bool = True, incomplete: bool = False) -> subprocess.CompletedProcess[str]:
        result = self.command(["/bin/bash", str(self.host / "murmur-update"), "--test-sandbox", str(self.root)], self.root)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        self.assertEqual((self.repo / ".git/murmur-update.lock").exists(), incomplete, result.stdout + result.stderr)
        self.assertEqual(bool(list((self.repo / ".git").glob("murmur-update-txn.*"))), incomplete)
        self.assertFalse(list(self.host.rglob("*.murmur-new.*")))
        self.assertFalse(list(self.host.rglob("*.murmur-restore.*")))
        self.assertEqual(self.initial_data, self.data_snapshot())
        self.assertFalse(any(event["args"][0] == "enable" for event in self.events("systemctl")))
        return result

    def events(self, kind: str) -> list[dict]:
        if not self.log.exists():
            return []
        return [event for line in self.log.read_text().splitlines() if (event := json.loads(line))["kind"] == kind]

    def host_mutations(self) -> list[dict]:
        return [event for event in self.events("systemctl") if event["args"][0] in ("restart", "daemon-reload")]

    def host_snapshot(self) -> dict:
        return {
            str(path.relative_to(self.host)): (path.read_bytes(), path.stat().st_mode & 0o777)
            for path in self.host.rglob("*") if path.is_file()
        }

    def git_config_snapshot(self) -> dict:
        return {
            name: (self.repo / ".git" / name).read_bytes()
            if (self.repo / ".git" / name).is_file() else None
            for name in ("config", "config.worktree", "info/sparse-checkout")
        }

    def data_snapshot(self) -> dict:
        return {
            str(path.relative_to(self.repo)): path.read_bytes()
            for pattern in (".env", "runtime/*", "photos/*", "logs/*")
            for path in self.repo.glob(pattern) if path.is_file() and not path.name.endswith("-shm")
        }

    def assert_rollback(self, project: str = ".") -> None:
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.old_head)
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(self.host_snapshot(), self.initial_host)
        self.assertEqual(self.git_config_snapshot(), self.initial_git_config)
        self.assertEqual((self.root / "installed-target").read_text(), project)

    def assert_backup(self) -> None:
        backups = list((self.repo / "backups").glob("*.db"))
        self.assertTrue(backups)
        self.assertEqual((self.repo / "backups").stat().st_mode & 0o777, 0o700)
        for backup in backups:
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(backups[-1]) as snapshot:
            self.assertEqual(snapshot.execute("SELECT message FROM memories").fetchall(), [("preserve committed WAL data",)])

    def test_legacy_to_new_then_next_update(self) -> None:
        self.git(self.repo, "sparse-checkout", "set", "--cone", "murmur", "deploy", "tests", "scripts")
        revision = self.publish()
        self.run_update()
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), revision)
        self.assertEqual((self.root / "installed-target").read_text(), "./server")
        self.assertEqual(self.git(self.repo, "sparse-checkout", "list"), "infra\nscripts\nserver")
        self.assertFalse((self.repo / "apps").exists())
        self.assertFalse((self.repo / "assets").exists())
        self.assertEqual((self.units / "murmur-telegram.service").read_bytes(), self.initial_host["units/murmur-telegram.service"][0])
        self.assertTrue((self.units / "murmur-optional.service").exists())
        self.assertEqual((self.host / "murmur-update").stat().st_mode & 0o777, 0o755)
        self.assert_backup()
        self.assertEqual(len(list((self.repo / "backups").glob("*.db"))), 1)
        next_revision = self.publish("three")
        self.run_update()
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), next_revision)
        self.assertEqual([event["version"] for event in self.events("pip")], ["two", "three"])
        for service in SERVICES:
            self.assertEqual((self.root / f"running-{service}").read_text(), "three")

    def test_legacy_to_legacy_supported(self) -> None:
        revision = self.publish(layout="old")
        self.run_update()
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), revision)
        self.assertEqual((self.root / "installed-target").read_text(), ".")

    def test_install_failure_restores_full_checkout(self) -> None:
        self.publish()
        self.write(self.root / "failure", "install")
        self.run_update(success=False)
        self.assert_rollback()
        self.assertTrue((self.repo / "apps/ios/source.swift").exists())
        self.assertEqual(self.host_mutations(), [])
        self.assert_backup()

    def test_test_failure_restores_sparse_checkout(self) -> None:
        self.git(self.repo, "sparse-checkout", "set", "--cone", "murmur", "deploy", "tests", "scripts")
        self.initial_git_config = self.git_config_snapshot()
        self.publish()
        self.write(self.root / "failure", "checks")
        self.run_update(success=False)
        self.assert_rollback()
        self.assertFalse((self.repo / "apps").exists())
        self.assertFalse((self.repo / "server").exists())
        self.assertEqual(self.host_mutations(), [])

    def test_new_layout_failure_restores_new_editable_target(self) -> None:
        self.publish()
        self.run_update()
        self.old_head = self.git(self.repo, "rev-parse", "HEAD")
        self.initial_git_config = self.git_config_snapshot()
        self.initial_host = self.host_snapshot()
        self.publish("three")
        self.write(self.root / "failure-version", "three")
        self.write(self.root / "failure", "checks")
        self.run_update(success=False)
        self.assert_rollback("./server")
        self.assertFalse((self.repo / "apps").exists())

    def test_backup_failure_prevents_source_changes(self) -> None:
        self.publish()
        self.write(self.repo / "runtime/broken.db", "not a database\n")
        self.write(self.repo / ".env", "MURMUR_DB=runtime/broken.db\n")
        self.initial_data = self.data_snapshot()
        self.run_update(success=False)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.old_head)
        self.assertEqual(self.host_snapshot(), self.initial_host)
        self.assertEqual(self.git_config_snapshot(), self.initial_git_config)
        self.assertEqual(self.events("pip"), [])
        self.assertFalse(list((self.repo / "backups").glob("*.db")))

    def test_all_configured_unique_databases_are_backed_up(self) -> None:
        self.publish()
        for filename, message in (("app.db", "app conversation data"), ("memory.db", "app memory data")):
            with sqlite3.connect(self.repo / "runtime" / filename) as database:
                database.execute("CREATE TABLE audit (message TEXT)")
                database.execute("INSERT INTO audit VALUES (?)", (message,))
        self.write(self.repo / ".env", 'MURMUR_DB="runtime/user data.db"\nMURMUR_APP_DB=runtime/not-selected.db\nMURMUR_APP_MEMORY_DB=runtime/memory.db\n')
        # Explicit environment wins over .env just as in the App CLI.
        self.env["MURMUR_APP_DB"] = "runtime/app.db"
        self.initial_data = self.data_snapshot()
        self.run_update()
        backups = list((self.repo / "backups").glob("*.db"))
        self.assertEqual(len(backups), 3)
        for name, message in (("murmur_app_db", "app conversation data"), ("murmur_app_memory_db", "app memory data")):
            backup = next(path for path in backups if path.name.startswith(name + "-"))
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(backup) as snapshot:
                self.assertEqual(snapshot.execute("SELECT message FROM audit").fetchall(), [(message,)])
        self.assertEqual((self.repo / "backups").stat().st_mode & 0o777, 0o700)

    def test_empty_app_database_overrides_use_base_database(self) -> None:
        self.publish()
        self.write(self.repo / ".env", 'MURMUR_DB="runtime/user data.db"\nMURMUR_APP_DB=\nMURMUR_APP_MEMORY_DB=\n')
        self.initial_data = self.data_snapshot()
        self.run_update()
        self.assert_backup()
        self.assertEqual(len(list((self.repo / "backups").glob("*.db"))), 1)

    def test_incomplete_restore_retains_snapshots_and_lock(self) -> None:
        self.publish()
        self.write(self.root / "failure", "restore-file")
        result = self.run_update(success=False, incomplete=True)
        self.assertIn("ROLLBACK INCOMPLETE", result.stderr)
        state, = (self.repo / ".git").glob("murmur-update-txn.*")
        self.assertIn(str(state), result.stderr)
        self.assertEqual(state.stat().st_mode & 0o777, 0o700)
        self.assertEqual((state / "old-revision").read_text().strip(), self.old_head)
        self.assertEqual((state / "old-project").read_text().strip(), ".")
        saved_path = next(path for path in state.glob("*.path") if path.read_text().strip() == str(self.host / "murmur-update"))
        self.assertEqual(saved_path.with_suffix(".saved").read_bytes(), self.initial_host["murmur-update"][0])
        self.assertEqual(saved_path.stat().st_mode & 0o777, 0o600)
        retry = self.command(["/bin/bash", str(UPDATER), "--test-sandbox", str(self.root)], self.root)
        self.assertNotEqual(retry.returncode, 0)
        self.assertIn("already running", retry.stderr)
        self.assertTrue(state.exists())
        self.assertTrue((self.repo / ".git/murmur-update.lock").exists())

    def test_active_disabled_service_refuses_update(self) -> None:
        self.publish()
        self.write(self.root / "enabled", "\n".join(SERVICES[:2]) + "\n")
        self.write(self.root / "running-murmur-web", "one")
        result = self.run_update(success=False)
        self.assertIn("murmur-web is active but disabled", result.stderr)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.old_head)
        self.assertEqual(self.host_snapshot(), self.initial_host)
        self.assertEqual(self.git_config_snapshot(), self.initial_git_config)
        self.assertEqual(self.events("pip"), [])
        self.assertEqual(self.host_mutations(), [])
        self.assertFalse((self.repo / "backups").exists())

    def test_restart_failure_rolls_back_every_attempted_service(self) -> None:
        self.publish()
        self.write(self.root / "failure", "restart")
        self.run_update(success=False)
        self.assert_rollback()
        restarts = [(e["args"][-1], e["version"]) for e in self.events("systemctl") if e["args"][0] == "restart"]
        self.assertEqual(restarts, [(SERVICES[0], "two"), (SERVICES[1], "two"), (SERVICES[0], "one"), (SERVICES[1], "one")])
        self.assertFalse((self.root / "running-murmur-web").exists())
        self.assertEqual((self.root / "running-murmur-app-worker").read_text(), "one")
        self.assertEqual((self.root / "running-murmur-app-api").read_text(), "one")

    def test_inactive_service_is_release_failure(self) -> None:
        self.publish()
        self.write(self.root / "failure", "inactive")
        self.run_update(success=False)
        self.assert_rollback()
        for service in SERVICES[:2]:
            self.assertEqual((self.root / f"running-{service}").read_text(), "one")

    def test_partial_unit_install_is_rolled_back(self) -> None:
        self.publish()
        self.write(self.root / "failure", "unit-install")
        self.run_update(success=False)
        self.assert_rollback()
        self.assertFalse(any(e["args"][0] == "restart" for e in self.events("systemctl")))

    def test_updater_install_failure_is_rolled_back(self) -> None:
        self.publish()
        self.write(self.root / "failure", "self-install")
        self.run_update(success=False)
        self.assert_rollback()

    def test_logrotate_install_failure_is_rolled_back(self) -> None:
        self.publish()
        self.write(self.root / "failure", "logrotate-install")
        self.run_update(success=False)
        self.assert_rollback()

    def test_daemon_reload_failure_is_rolled_back(self) -> None:
        self.publish()
        self.write(self.root / "failure", "daemon-reload")
        self.run_update(success=False)
        self.assert_rollback()

    def test_dirty_tracked_source_is_untouched(self) -> None:
        self.publish()
        self.write(self.repo / "VERSION", "dirty\n")
        self.run_update(success=False)
        self.assertEqual((self.repo / "VERSION").read_text(), "dirty\n")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.old_head)
        self.assertEqual(self.host_snapshot(), self.initial_host)
        self.assertEqual(self.git_config_snapshot(), self.initial_git_config)
        self.assertEqual(self.events("python"), [])

    def test_untracked_source_is_untouched(self) -> None:
        self.publish()
        self.write(self.repo / "local-notes.md", "preserve me\n")
        self.run_update(success=False)
        self.assertEqual((self.repo / "local-notes.md").read_text(), "preserve me\n")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.old_head)

    def test_up_to_date_does_not_install_or_backup(self) -> None:
        self.run_update()
        self.assertEqual(self.host_snapshot(), self.initial_host)
        self.assertEqual(self.events("pip"), [])
        self.assertFalse((self.repo / "backups").exists())

    def test_non_fast_forward_refused(self) -> None:
        self.git(self.repo, "config", "user.name", "Local")
        self.git(self.repo, "config", "user.email", "local@example.invalid")
        self.write(self.repo / "local.txt", "local commit\n")
        self.git(self.repo, "add", "local.txt")
        self.git(self.repo, "commit", "-m", "unique-local")
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.publish()
        self.run_update(success=False)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(self.events("pip"), [])

    def test_test_mode_requires_marker(self) -> None:
        (self.root / ".murmur-updater-test").unlink()
        self.run_update(success=False)
        self.assertFalse(self.log.exists())

    def test_test_mode_refuses_nonlocal_origin(self) -> None:
        self.git(self.repo, "remote", "set-url", "origin", "https://example.invalid/never-contact")
        self.run_update(success=False)
        self.assertFalse(self.log.exists())

    def test_test_mode_refuses_unsafe_root(self) -> None:
        result = self.command(["/bin/bash", str(self.host / "murmur-update"), "--test-sandbox", "/opt"], self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_test_mode_refuses_symlinked_env(self) -> None:
        (self.repo / ".env").unlink()
        (self.repo / ".env").symlink_to(self.repo / "runtime/key")
        self.initial_data = self.data_snapshot()
        self.run_update(success=False)
        self.assertFalse(self.log.exists())

    def test_test_mode_refuses_database_outside_fixture(self) -> None:
        self.publish()
        self.write(self.repo / ".env", "MURMUR_DB=/opt/murmur/murmur.db\n")
        self.initial_data = self.data_snapshot()
        self.run_update(success=False)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.old_head)
        self.assertEqual(self.events("pip"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
