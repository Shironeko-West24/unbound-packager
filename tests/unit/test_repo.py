import io
import re
import sys
import tokenize
import unittest

import helpers

WORKFLOWS = helpers.ROOT / ".github" / "workflows"


class WorkflowTest(unittest.TestCase):
    def text(self, name):
        return (WORKFLOWS / name).read_text(encoding="utf-8")

    def test_only_pinned_official_artifact_actions(self):
        allowed = re.compile(r"^actions/(upload|download)-artifact@[0-9a-f]{40}$")
        for path in WORKFLOWS.glob("*.yml"):
            for action in re.findall(r"(?m)^\s*-?\s*uses:\s*(\S+)\s*$", path.read_text()):
                self.assertRegex(action, allowed, path.name)
        self.assertIsNone(re.search(r"(?m)^\s*-?\s*uses:", self.text("ci.yml")))

    def test_no_dangerous_triggers(self):
        for path in WORKFLOWS.glob("*.yml"):
            text = path.read_text()
            self.assertNotIn("pull_request_target", text, path.name)
            self.assertNotIn("workflow_run", text, path.name)

    def test_default_permissions_are_read_only(self):
        for path in WORKFLOWS.glob("*.yml"):
            self.assertRegex(path.read_text(), r"(?m)^permissions:\n  contents: read\n", path.name)

    def test_ci_has_no_secrets_no_write_and_skips_forks(self):
        text = self.text("ci.yml")
        self.assertNotIn("secrets.", text)
        self.assertNotIn("github.token", text)
        self.assertNotIn(": write", text)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)

    def test_release_runs_only_when_started_by_hand(self):
        text = self.text("release.yml")
        head = text.split("permissions:")[0]
        self.assertIn("workflow_dispatch:", head)
        self.assertNotIn("schedule:", text)
        self.assertNotIn("pull_request", text)
        self.assertNotIn("push:", text)
        self.assertNotIn("SCHEDULE_ENABLED", text)

    def jobs(self, name):
        body = self.text(name).split("\njobs:\n", 1)[1]
        parts = re.split(r"(?m)^  ([a-z-]+):\n", body)
        return dict(zip(parts[1::2], parts[2::2]))

    def test_release_jobs_have_only_the_permissions_they_need(self):
        jobs = self.jobs("release.yml")
        expected = {
            "detect": {"contents": "read", "issues": "read"},
            "build": {"contents": "read"},
            "publish": {"contents": "write"},
            "record": {"contents": "read", "issues": "write"},
            "keepalive": {"actions": "write"},
        }
        self.assertEqual(set(jobs), set(expected))
        for name, body in jobs.items():
            block = re.search(r"(?m)^    permissions:\n((?:      .*\n)+)", body)
            self.assertIsNotNone(block, name)
            found = dict(re.findall(r"(?m)^      ([a-z-]+): ([a-z]+)$", block.group(1)))
            self.assertEqual(found, expected[name], name)
        self.assertNotIn("secrets.", jobs["build"])
        self.assertNotIn("github.token", jobs["build"])

    def test_expressions_never_appear_inside_run_scripts(self):
        for path in WORKFLOWS.glob("*.yml"):
            lines = path.read_text().splitlines()
            for index, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("run:") and stripped != "run: |":
                    self.assertNotIn("${{", stripped, f"{path.name}:{index + 1}")
            blocks = re.findall(r"run: \|\n((?:\s{10,}.*\n)+)", path.read_text())
            for block in blocks:
                self.assertNotIn("${{", block, path.name)

    def contexts(self, expression):
        return set(re.findall(r"(?<![\w.'])([a-z_]+)\.", expression))

    def test_contexts_are_available_where_used(self):
        allowed = {
            "concurrency": {"github", "inputs", "vars"},
            "job env": {"github", "needs", "strategy", "matrix", "vars", "secrets", "inputs"},
            "job if": {"github", "needs", "vars", "inputs"},
        }
        for path in WORKFLOWS.glob("*.yml"):
            text = path.read_text()
            places = []
            for block in re.findall(r"(?m)^concurrency:\n((?:  .*\n)+)", text):
                places.append(("concurrency", block))
            for block in re.findall(r"(?m)^    env:\n((?:      .*\n)+)", text):
                places.append(("job env", block))
            for value in re.findall(r"(?m)^    if: (.*)$", text):
                places.append(("job if", "${{ " + value + " }}"))
            self.assertTrue(any(kind == "job env" for kind, _ in places), path.name)
            for kind, block in places:
                for expression in re.findall(r"\$\{\{(.*?)\}\}", block):
                    extra = self.contexts(expression) - allowed[kind]
                    self.assertEqual(extra, set(), f"{path.name}: {kind}: {expression.strip()}")

    def test_context_checker_catches_runner_in_job_env(self):
        self.assertEqual(self.contexts(" runner.temp "), {"runner"})
        self.assertEqual(self.contexts(" github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name "), {"github"})

    def test_webhook_is_passed_only_to_notification_steps(self):
        text = self.text("release.yml")
        steps = text.split("      - name: ")[1:]
        with_secret = [step.splitlines()[0] for step in steps if "secrets.DISCORD_WEBHOOK_URL" in step]
        self.assertEqual(with_secret, ["検知の通知", "成功の記録と通知", "失敗の記録と通知", "定期実行の維持"])
        with_key = [step.splitlines()[0] for step in steps if "secrets.RELEASE_SIGNING_KEY" in step]
        self.assertEqual(with_key, ["署名"])
        self.assertEqual(re.findall(r"secrets\.([A-Z_]+)", text), ["DISCORD_WEBHOOK_URL", "RELEASE_SIGNING_KEY", "DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_URL"])
        build = [step for step in steps if step.startswith("ビルドとテスト")][0]
        self.assertNotIn("GH_TOKEN", build)
        self.assertNotIn("secrets.", build)


class HygieneTest(unittest.TestCase):
    def python_files(self):
        files = [p for p in helpers.ROOT.rglob("*.py") if "__pycache__" not in p.parts and "work" not in p.parts]
        return files + [helpers.TOOL]

    def test_no_module_shadows_the_standard_library(self):
        clash = [p for p in self.python_files() if p.stem in sys.stdlib_module_names and p.parent.name != "ubp"]
        self.assertEqual(clash, [])

    def test_entry_points_run_isolated(self):
        for rel in ("server/unbound-update", "tools/ci.py", "packaging/rpm_build.py", "packaging/deb_build.py", "packaging/intest.py", "tests/run_all.py"):
            self.assertEqual((helpers.ROOT / rel).read_text().splitlines()[0], "#!/usr/bin/python3 -I", rel)

    def test_entry_points_start_when_isolated(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as elsewhere:
            for rel in ("server/unbound-update", "tools/ci.py", "packaging/rpm_build.py", "packaging/deb_build.py", "packaging/intest.py"):
                proc = subprocess.run(
                    [sys.executable, "-I", str(helpers.ROOT / rel), "--help"],
                    cwd=elsewhere, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(proc.returncode, 0, f"{rel}\n{proc.stderr[-800:]}")

    def test_code_has_no_comments(self):
        offenders = []
        for path in self.python_files():
            for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
                if token.type == tokenize.COMMENT and not token.string.startswith(("#!", "# noqa")):
                    offenders.append(f"{path.name}:{token.start[0]}")
        self.assertEqual(offenders, [])

    def test_no_secrets_or_home_details_in_repository(self):
        pattern = re.compile(r"discord(app)?\.com/api/webhooks/\d|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d+\.\d+\b")
        offenders = []
        for path in helpers.ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts or "tests" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if pattern.search(text):
                offenders.append(str(path.relative_to(helpers.ROOT)))
        self.assertEqual(offenders, [])

    def test_no_session_links_in_repository(self):
        session = re.compile(r"claude\.ai/code/session|session_[0-9A-Za-z]{20,}")
        offenders = []
        for path in helpers.ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if session.search(text):
                offenders.append(str(path.relative_to(helpers.ROOT)))
        self.assertEqual(offenders, [])

    def test_required_documents_exist(self):
        for name in ("README.md", "LICENSE", "NOTICE.md", "SECURITY.md", "CLAUDE.md", "docs/usage.md", "docs/maintenance.md", "docs/verification.md"):
            self.assertTrue((helpers.ROOT / name).is_file(), name)
        self.assertIn("MIT License", (helpers.ROOT / "LICENSE").read_text())
        readme = (helpers.ROOT / "README.md").read_text()
        self.assertFalse((helpers.ROOT / "owner").exists())
        self.assertLess(len(readme.splitlines()), 90)
        for needed in ("非公式", "docs/usage.md", "NOTICE.md", "SECURITY.md"):
            self.assertIn(needed, readme)
        security = (helpers.ROOT / "SECURITY.md").read_text()
        for needed in ("非公式", "https://nlnetlabs.nl/security-report/", "Report a vulnerability", "Rocky Linux 10", "Ubuntu 26.04"):
            self.assertIn(needed, security)
        notice = (helpers.ROOT / "NOTICE.md").read_text()
        for needed in ("NLnet Labs", "BSD", "GPL-3", "MIT", "非公式"):
            self.assertIn(needed, notice)
