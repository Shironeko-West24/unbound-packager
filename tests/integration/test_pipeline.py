import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from ubp import ci, containers, notify, upstream, util
from ubp.util import UbpError

WEBHOOK = "https://discord.example/api/webhooks/123/VERY-SECRET-TOKEN"


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.work = Path(self.tmp) / "work"
        self.fake = Path(self.tmp) / "fake"
        self.fake.mkdir()
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        for name in helpers.FAKE_NAMES:
            os.symlink(helpers.ROOT / "tests" / "fakes" / "fakecmd.py", self.bin / name)
        self.source = Path(self.tmp) / "src"
        self.source.mkdir()
        for name in ("unbound-1.26.2.tar.gz", "unbound-1.26.2.tar.gz.asc", "key.asc"):
            (self.source / name).write_bytes(b"x")
        self.src = {"tarball": str(self.source / "unbound-1.26.2.tar.gz"), "signature": str(self.source / "unbound-1.26.2.tar.gz.asc"), "key": str(self.source / "key.asc")}
        self.output = Path(self.tmp) / "github-output"
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}", "UBP_FAKE_DIR": str(self.fake), "UBP_WORK": str(self.work),
            "UBP_ENGINE": "podman", "GITHUB_OUTPUT": str(self.output), "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "77",
            "GITHUB_SHA": "abc123", "DISCORD_WEBHOOK_URL": WEBHOOK, "GH_TOKEN": "TOKEN-VALUE",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.posted = []
        patcher = mock.patch.object(notify.Notifier, "_post", lambda inner, url, payload: self.posted.append(payload))
        patcher.start()
        self.addCleanup(patcher.stop)
        util.SECRETS.clear()
        util.LOG.echo = False
        self.addCleanup(setattr, util.LOG, "echo", True)

    def calls(self, cmd):
        path = self.fake / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line)["args"] for line in path.read_text().splitlines() if json.loads(line)["cmd"] == cmd]

    def prepared_out(self, os_name="rocky10"):
        stage = Path(self.tmp) / "prepared"
        helpers.make_out(stage, "1.26.2", 1, os_name)
        return stage / "out" / os_name

    def test_build_moves_verified_artifacts(self):
        os.environ["UBP_FAKE_PODMAN_OUT"] = str(self.prepared_out())
        manifest = containers.build_packages(helpers.ROOT, self.work, "rocky10", "1.26.2", 1, self.src)
        self.assertEqual(manifest["pkg_version"], "1.26.2-1.local.el10_2")
        self.assertTrue((self.work / "out" / "rocky10" / "manifest.json").exists())
        self.assertFalse((self.work / "build" / "rocky10").exists())
        run = [args for args in self.calls("podman") if args[:1] == ["run"]][0]
        self.assertIn("--rm", run)
        self.assertIn("/work/src/packaging/rpm_build.py", run)
        self.assertEqual(run[run.index("--revision") + 1], "1")

    def test_missing_or_tampered_output_fails(self):
        with self.assertRaises(UbpError):
            containers.build_packages(helpers.ROOT, self.work, "ubuntu2604", "1.26.2", 1, self.src)
        out = self.prepared_out()
        next(out.glob("*.rpm")).write_bytes(b"tampered")
        os.environ["UBP_FAKE_PODMAN_OUT"] = str(out)
        with self.assertRaises(UbpError) as caught:
            containers.build_packages(helpers.ROOT, self.work, "rocky10", "1.26.2", 1, self.src)
        self.assertIn("SHA-256", str(caught.exception))

    def test_sudo_engine_is_split_into_arguments(self):
        with mock.patch.dict(os.environ, {"UBP_ENGINE": "sudo podman"}):
            self.assertEqual(containers.engine(), ["sudo", "podman"])

    def test_pipeline_failure_records_stage(self):
        with mock.patch.object(upstream, "download", lambda *a, **k: self.src):
            rc = ci.main(["pipeline", "--version", "1.26.2", "--revision", "1"])
        self.assertEqual(rc, 1)
        record = json.loads((self.work / "failure.json").read_text())
        self.assertEqual(record["stage"], "ビルド（rocky10）")
        self.assertIn("ビルドに失敗", record["detail"])

    def test_package_tests_run_for_every_os_before_failing(self):
        tested = []

        def fail_test(code, work, os_name, checks):
            tested.append(os_name)
            raise UbpError(f"{os_name}: パッケージのテストに失敗しました。")

        with mock.patch.object(upstream, "download", lambda *a, **k: self.src), \
                mock.patch.object(containers, "build_packages", lambda *a, **k: {}), \
                mock.patch.object(containers, "test_packages", fail_test):
            rc = ci.main(["pipeline", "--version", "1.26.2", "--revision", "1"])
        self.assertEqual(rc, 1)
        self.assertEqual(tested, ["rocky10", "ubuntu2604"])
        record = json.loads((self.work / "failure.json").read_text())
        self.assertEqual(record["stage"], "パッケージのテスト")
        self.assertIn("rocky10", record["detail"])
        self.assertIn("ubuntu2604", record["detail"])
        self.assertFalse((self.work / "release").exists())

    def test_publish_uses_gh_and_reports_tag(self):
        for os_name in ("rocky10", "ubuntu2604"):
            helpers.make_out(self.work, "1.26.2", 1, os_name)
        from ubp import release

        release.assemble(helpers.ROOT, self.work, "1.26.2", 1)
        helpers.sign_release(self.work / "release")
        api = mock.Mock(release_tags=lambda: ["v1.26.2-1", "v1.20.0-1", "v1.21.0-1", "v1.22.0-1", "v1.23.0-1", "v1.24.0-1"])
        with mock.patch.object(ci, "github", lambda: api):
            self.assertEqual(ci.main(["publish"]), 0)
        gh = self.calls("gh")
        self.assertEqual(len(gh), 1)
        self.assertEqual(gh[0][:3], ["release", "create", "v1.26.2-1"])
        self.assertEqual(gh[0][gh[0].index("--target") + 1], "abc123")
        self.assertIn("tag=v1.26.2-1", self.output.read_text())

    def test_sign_command_uses_secret_and_repository_key(self):
        from ubp import release, signing

        for os_name in ("rocky10", "ubuntu2604"):
            helpers.make_out(self.work, "1.26.2", 1, os_name)
        release.assemble(helpers.ROOT, self.work, "1.26.2", 1)
        key = helpers.signing_key()
        with mock.patch.dict(os.environ, {"RELEASE_SIGNING_KEY": open(key["file"]).read()}), \
                mock.patch.object(signing, "public_key", lambda code: key["public"]):
            self.assertEqual(ci.main(["sign"]), 0)
        signature = self.work / "release" / "SHA256SUMS.sig"
        self.assertTrue(signing.verify(key["public"], str(self.work / "release" / "SHA256SUMS"), str(signature)))
        with mock.patch.dict(os.environ, {"RELEASE_SIGNING_KEY": ""}), \
                mock.patch.object(signing, "public_key", lambda code: key["public"]):
            self.assertEqual(ci.main(["sign"]), 1)

    def test_publish_without_pipeline_result_fails(self):
        self.assertEqual(ci.main(["publish"]), 1)
        self.assertEqual(self.calls("gh"), [])

    def test_failure_is_recorded_notified_and_gives_up_at_three(self):
        self.work.mkdir()
        (self.work / "failure.json").write_text(json.dumps({"stage": "ビルド（rocky10）", "detail": f"boom {WEBHOOK}"}))
        api = mock.Mock()
        api.record_failure.return_value = 3
        with mock.patch.dict(os.environ, {"UBP_VERSION": "1.26.2"}), mock.patch.object(ci, "github", lambda: api):
            self.assertEqual(ci.main(["on-failure"]), 0)
        body = api.record_failure.call_args[0][1]
        self.assertIn("actions/runs/77", body)
        titles = [p["embeds"][0]["title"] for p in self.posted]
        self.assertIn("3/3", titles[0])
        self.assertIn("再試行を終了", titles[1])

    def test_success_closes_issue_and_notifies_with_command(self):
        for os_name in ("rocky10", "ubuntu2604"):
            helpers.make_out(self.work, "1.26.2", 1, os_name)
        from ubp import release

        release.assemble(helpers.ROOT, self.work, "1.26.2", 1)
        api = mock.Mock()
        with mock.patch.object(ci, "github", lambda: api):
            self.assertEqual(ci.main(["on-success"]), 0)
        api.close_failure.assert_called_once()
        description = self.posted[0]["embeds"][0]["description"]
        self.assertIn("sudo unbound-update update --version 1.26.2", description)
        self.assertIn("/usr/share/new", description)
        self.assertIn("releases/tag/v1.26.2-1", description)

    def test_detect_writes_outputs(self):
        api = mock.Mock(release_tags=lambda: ["v1.26.1-1"], attempts=lambda v: 0)
        with mock.patch.object(ci, "github", lambda: api), mock.patch.object(upstream, "detect_latest", lambda *a: "1.26.2"):
            self.assertEqual(ci.main(["detect"]), 0)
        text = self.output.read_text()
        for needed in ("version=1.26.2", "revision=1", "needed=true", "first=true", "published=v1.26.1-1"):
            self.assertIn(needed, text)

    def test_heartbeat_only_on_first_day_of_month_schedule(self):
        import datetime

        api = mock.Mock()
        real = datetime.datetime

        class Day1(real):
            @classmethod
            def now(cls, tz=None):
                return real(2026, 12, 1, 4, 20, tzinfo=tz)

        class Day2(real):
            @classmethod
            def now(cls, tz=None):
                return real(2026, 12, 2, 4, 20, tzinfo=tz)

        with mock.patch.object(ci, "github", lambda: api), mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule"}):
            with mock.patch.object(ci.datetime, "datetime", Day2):
                ci.main(["keepalive"])
            self.assertEqual(self.posted, [])
            with mock.patch.object(ci.datetime, "datetime", Day1):
                ci.main(["keepalive"])
        self.assertIn("定期実行は動いています", self.posted[0]["embeds"][0]["title"])
        self.assertEqual(api.enable_workflow.call_count, 2)

    def test_github_token_is_masked_in_logs(self):
        import contextlib
        import io

        ci.github()
        buffer = io.StringIO()
        util.LOG.echo = True
        with contextlib.redirect_stdout(buffer):
            util.log("Authorization: Bearer TOKEN-VALUE")
        self.assertNotIn("TOKEN-VALUE", buffer.getvalue())
        self.assertIn("［秘密］", buffer.getvalue())

    def test_secrets_are_not_printed(self):
        import contextlib
        import io

        def boom(inner, url, payload):
            raise RuntimeError(f"cannot reach {url}")

        buffer = io.StringIO()
        util.LOG.echo = True
        with mock.patch.object(notify.Notifier, "_post", boom), mock.patch.dict(os.environ, {"UBP_VERSION": "1.26.2"}):
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                ci.main(["notify-new"])
                util.log(f"取得できませんでした: {WEBHOOK}")
        self.assertNotIn("VERY-SECRET-TOKEN", buffer.getvalue())
        self.assertIn("通知を送れませんでした", buffer.getvalue())
