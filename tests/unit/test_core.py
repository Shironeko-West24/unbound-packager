import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

import helpers
from ubp import ci, licenses, notify, release, upstream, versions
from ubp.github import BOT, GitHub, ISSUE_PREFIX
from ubp.util import UbpError, redact


def load_tool():
    loader = importlib.machinery.SourceFileLoader("ubp_tool_unit", str(helpers.TOOL))
    spec = importlib.util.spec_from_loader("ubp_tool_unit", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class VersionTest(unittest.TestCase):
    def test_parse_latest_and_index(self):
        self.assertEqual(versions.latest(["1.9.10", "1.26.2", "1.26.10", "1.27.0rc1"]), "1.26.10")
        with self.assertRaises(UbpError):
            versions.parse("1.26.0rc1")
        html = 'unbound-1.26.2.tar.gz unbound-1.26.2.tar.gz.asc unbound-1.26.0rc1.tar.gz unbound-1.25.2.tar.gz.sha256'
        self.assertEqual(versions.versions_in_index(html), ["1.26.2"])

    def test_package_versions(self):
        self.assertEqual(versions.rpm_dist("7.el10_2.6"), ".el10_2")
        self.assertEqual(versions.rpm_evr("1.26.2", 1, ".el10_2"), "1.26.2-1.local.el10_2")
        self.assertEqual(versions.deb_version("1.26.2", 2), "1.26.2-0+local2")

    def test_tags(self):
        self.assertEqual(release.tag_name("1.26.2", 1), "v1.26.2-1")
        self.assertEqual(release.parse_tag("v1.26.2-3"), ("1.26.2", 3))
        self.assertIsNone(release.parse_tag("v1.26.2"))
        self.assertEqual(release.revisions(["v1.26.2-2", "v1.26.1-1", "v1.26.2-1", "junk"], "1.26.2"), [1, 2])


class DecideTest(unittest.TestCase):
    def decide(self, **kw):
        base = dict(requested="", rebuild=False, manual=False, tags=[], attempts=0, maximum=3, latest="1.26.2")
        base.update(kw)
        return ci.decide(**base)

    def test_new_version_is_built(self):
        self.assertEqual(self.decide(tags=["v1.26.1-1"]), {"version": "1.26.2", "revision": 1, "needed": True, "reason": "未公開の版"})

    def test_published_version_is_skipped(self):
        result = self.decide(tags=["v1.26.2-1"])
        self.assertEqual((result["needed"], result["revision"]), (False, 1))

    def test_gives_up_after_three_failures_but_manual_run_still_works(self):
        self.assertFalse(self.decide(attempts=3)["needed"])
        self.assertTrue(self.decide(attempts=2)["needed"])
        self.assertTrue(self.decide(attempts=3, manual=True)["needed"])

    def test_rebuild_bumps_revision(self):
        result = self.decide(rebuild=True, tags=["v1.26.2-1", "v1.26.2-2"])
        self.assertEqual((result["needed"], result["revision"]), (True, 3))

    def test_requested_version_and_bad_input(self):
        self.assertEqual(self.decide(requested="1.25.2")["version"], "1.25.2")
        with self.assertRaises(UbpError):
            self.decide(requested="1.26.2; rm -rf /")


class WorkDirTest(unittest.TestCase):
    def test_work_dir_prefers_explicit_then_runner_temp(self):
        from unittest import mock

        with mock.patch.dict(os.environ, {"UBP_WORK": "/x/work", "RUNNER_TEMP": "/t"}):
            self.assertEqual(ci.work_dir(), Path("/x/work"))
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": "/t"}):
            os.environ.pop("UBP_WORK", None)
            self.assertEqual(ci.work_dir(), Path("/t/ubp"))
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("UBP_WORK", None)
            os.environ.pop("RUNNER_TEMP", None)
            self.assertEqual(ci.work_dir(), helpers.ROOT / "work")


class FakeApi:
    def __init__(self):
        self.issues = []
        self.comments = {}
        self.calls = []
        self.releases = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        tail = path.split("/repos/o/r", 1)[1]
        if method == "GET" and tail.startswith("/releases"):
            return 200, self.releases
        if method == "GET" and tail.startswith("/issues?"):
            return 200, [i for i in self.issues if i["state"] == "open"]
        if method == "GET" and "/comments" in tail:
            return 200, self.comments.get(int(tail.split("/")[2]), [])
        if method == "POST" and tail == "/issues":
            issue = {"number": len(self.issues) + 1, "title": body["title"], "user": {"login": BOT}, "state": "open"}
            self.issues.append(issue)
            return 201, issue
        if method == "POST" and tail.endswith("/comments"):
            self.comments.setdefault(int(tail.split("/")[2]), []).append({"user": {"login": BOT}, "body": body["body"]})
            return 201, {}
        if method == "PATCH":
            self.issues[int(tail.split("/")[2]) - 1]["state"] = body["state"]
            return 200, {}
        if method == "PUT":
            return 204, None
        return 404, None


class GitHubTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.gh = GitHub("o/r", self.api)

    def test_attempts_are_counted_per_version(self):
        self.assertEqual(self.gh.attempts("1.26.2"), 0)
        self.assertEqual(self.gh.record_failure("1.26.2", "一回目"), 1)
        self.assertEqual(self.gh.record_failure("1.26.2", "二回目"), 2)
        self.assertEqual(self.gh.record_failure("1.26.3", "別の版"), 1)
        self.assertEqual(len(self.api.issues), 2)
        self.assertEqual(self.api.issues[0]["title"], ISSUE_PREFIX + "1.26.2")

    def test_issues_and_comments_by_others_are_ignored(self):
        self.api.issues.append({"number": 1, "title": ISSUE_PREFIX + "1.26.2", "user": {"login": "stranger"}, "state": "open"})
        self.assertEqual(self.gh.attempts("1.26.2"), 0)
        self.gh.record_failure("1.26.2", "本物")
        self.api.comments[2] = [{"user": {"login": "stranger"}, "body": "x"}] * 5
        self.assertEqual(self.gh.attempts("1.26.2"), 1)

    def test_close_on_success_and_release_tags(self):
        self.gh.record_failure("1.26.2", "失敗")
        self.assertTrue(self.gh.close_failure("1.26.2", "合格"))
        self.assertEqual(self.gh.attempts("1.26.2"), 0)
        self.assertFalse(self.gh.close_failure("1.26.2", "合格"))
        self.api.releases = [{"tag_name": "v1.26.2-1"}, {"tag_name": "v9.9.9-1", "draft": True}]
        self.assertEqual(self.gh.release_tags(), ["v1.26.2-1"])
        self.assertTrue(self.gh.enable_workflow("release.yml"))

    def test_api_failure_is_reported(self):
        with self.assertRaises(UbpError):
            GitHub("o/r", lambda *a: (500, None)).release_tags()


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_assemble_creates_manifest_sums_tool_and_sources(self):
        for os_name in ("rocky10", "ubuntu2604"):
            helpers.make_out(self.tmp, "1.26.2", 1, os_name)
        target, combined = release.assemble(helpers.ROOT, self.tmp, "1.26.2", 1)
        names = set(os.listdir(target))
        for needed in ("SHA256SUMS", "manifest.json", "unbound-update", "NOTICE.md", "unbound-1.26.2-1.local.el10_2.src.rpm", "unbound_1.26.2-0+local1.dsc"):
            self.assertIn(needed, names)
        sums = dict(reversed(line.split()) for line in open(os.path.join(target, "SHA256SUMS")))
        self.assertEqual(set(sums), names - {"SHA256SUMS"})
        import hashlib

        for name, digest in sums.items():
            self.assertEqual(hashlib.sha256(open(os.path.join(target, name), "rb").read()).hexdigest(), digest)
        self.assertEqual(combined["tag"], "v1.26.2-1")
        self.assertEqual(set(combined["os"]), {"rocky10", "ubuntu2604"})

    def test_untested_or_missing_os_is_not_released(self):
        helpers.make_out(self.tmp, "1.26.2", 1, "rocky10")
        with self.assertRaises(UbpError) as caught:
            release.assemble(helpers.ROOT, self.tmp, "1.26.2", 1)
        self.assertIn("ubuntu2604", str(caught.exception))
        helpers.make_out(self.tmp, "1.26.2", 1, "ubuntu2604", tested=False)
        with self.assertRaises(UbpError) as caught:
            release.assemble(helpers.ROOT, self.tmp, "1.26.2", 1)
        self.assertIn("テストに合格していない", str(caught.exception))

    def test_tampered_artifact_is_not_released(self):
        for os_name in ("rocky10", "ubuntu2604"):
            helpers.make_out(self.tmp, "1.26.2", 1, os_name)
        next(Path(self.tmp, "out", "rocky10").glob("*.rpm")).write_bytes(b"tampered")
        with self.assertRaises(UbpError) as caught:
            release.assemble(helpers.ROOT, self.tmp, "1.26.2", 1)
        self.assertIn("SHA-256", str(caught.exception))

    def test_notes_and_publish_and_prune(self):
        for os_name in ("rocky10", "ubuntu2604"):
            helpers.make_out(self.tmp, "1.26.2", 1, os_name)
        target, combined = release.assemble(helpers.ROOT, self.tmp, "1.26.2", 1)
        text = release.notes(combined, "o/r")
        for needed in ("非公式ビルド", "NOTICE.md", "ソースパッケージ", "| `a.patch` | 除外 | 上流に取り込み済み |", "/usr/share/new", "sudo unbound-update update --version 1.26.2"):
            self.assertIn(needed, text)
        self.assertNotIn("drop", text)
        calls = []
        release.publish(target, combined, "o/r", "abc123", gh=lambda cmd, **kw: calls.append(cmd))
        cmd = calls[0]
        self.assertEqual(cmd[:4], ["gh", "release", "create", "v1.26.2-1"])
        self.assertIn("abc123", cmd)
        self.assertTrue(any(arg.endswith("SHA256SUMS") for arg in cmd))
        removed = release.prune(["v1.26.2-1", "v1.25.2-1", "v1.26.1-2", "v1.26.1-1", "other"], 2, "o/r", gh=lambda cmd, **kw: calls.append(cmd))
        self.assertEqual(removed, ["v1.25.2-1", "v1.26.1-1"])
        self.assertIn("--cleanup-tag", calls[-1])


class NotifyTest(unittest.TestCase):
    def test_messages(self):
        severity, title, body = notify.released("1.26.2", "v1.26.2-1", "rocky10: ok", ["rocky10: /x"], "https://example/rel")
        self.assertEqual(severity, "success")
        self.assertIn("sudo unbound-update update --version 1.26.2", body)
        self.assertIn("1 台で確かめてから", body)
        self.assertIn("/x", body)
        _, title, body = notify.failed("1.26.2", "ビルド（rocky10）", "x" * 5000, 1, 3, "https://example/run")
        self.assertIn("1/3", title)
        self.assertIn("明日", body)
        self.assertLess(len(body), 1500)
        self.assertIn("自動の再試行は終わりました", notify.failed("1.26.2", "テスト", "boom", 3, 3, "u")[2])
        self.assertIn("毎月 1 日", notify.heartbeat("1.26.2", "v1.26.2-1")[2])
        self.assertEqual(len(set(notify.COLORS.values())), 4)

    def test_no_url_and_failure_do_not_raise_or_leak(self):
        sent = []
        self.assertFalse(notify.Notifier("", sender=lambda url, payload: sent.append(url)).send("info", "t", "b"))
        self.assertEqual(sent, [])

        def boom(url, payload):
            raise RuntimeError(url)

        self.assertFalse(notify.Notifier("https://discord.example/api/webhooks/9/TOKEN", sender=boom).send("error", "t", "b"))
        self.assertNotIn("TOKEN", redact("failed https://discord.example/api/webhooks/9/TOKEN"))


class LicenseTest(unittest.TestCase):
    COPYRIGHT = (
        "Format: x\nUpstream-Name: unbound\n\nFiles: *\nCopyright: NLnet Labs\nLicense: BSD-3-NLnetLabs\n text\n\n"
        "Files: debian/*\nCopyright: someone\nLicense: GPL-3\n text\n\n"
        "Files: debian/patches/*\nCopyright: someone\nLicense: ISC\n\nFiles: compat/a.c\n       compat/b.c\nCopyright: x\nLicense: ISC\n"
    )

    def test_packaging_licenses(self):
        self.assertEqual(licenses.packaging_licenses(self.COPYRIGHT), ["GPL-3", "ISC"])
        self.assertEqual(licenses.all_licenses(self.COPYRIGHT), ["BSD-3-NLnetLabs", "GPL-3", "ISC"])
        self.assertEqual(licenses.check_packaging(self.COPYRIGHT, ["GPL-3", "ISC"]), ["GPL-3", "ISC"])

    def test_unexpected_license_stops(self):
        with self.assertRaises(UbpError) as caught:
            licenses.check_packaging(self.COPYRIGHT.replace("License: GPL-3", "License: Proprietary"), ["GPL-3", "ISC"])
        self.assertIn("Proprietary", str(caught.exception))
        with self.assertRaises(UbpError):
            licenses.check_packaging("Files: *\nLicense: BSD\n", ["GPL-3"])
        with self.assertRaises(UbpError):
            licenses.check_spec_license("SSPL", ["BSD-3-Clause"])

    def test_maintainer_is_replaced_once(self):
        control = "Source: unbound\nMaintainer: Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>\nXSBC-Original-Maintainer: Debian <d@example>\nSection: net\n"
        new = licenses.replace_maintainer(control, "me <me@example>")
        self.assertIn("Maintainer: me <me@example>\nXSBC-Original-Maintainer: Debian <d@example>", new)
        self.assertNotIn("Ubuntu Developers", new)
        plain = licenses.replace_maintainer("Source: unbound\nMaintainer: Debian <d@example>\n", "me <me@example>")
        self.assertIn("Maintainer: me <me@example>\nXSBC-Original-Maintainer: Debian <d@example>", plain)
        self.assertEqual(licenses.replace_maintainer(new, "me <me@example>"), new)

    def test_options_carry_no_distribution_marks(self):
        opts = json.loads((helpers.ROOT / "packaging" / "options.json").read_text())
        for value in (opts["maintainer"], opts["vendor"]):
            for mark in ("Ubuntu", "Rocky", "Red Hat", "Debian", "Canonical", "NLnet"):
                self.assertNotIn(mark, value)


@unittest.skipUnless(shutil.which("gpg"), "gpg が必要")
class SignatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.home, mode=0o700)
        base = ["gpg", "--homedir", cls.home, "--batch", "--pinentry-mode", "loopback", "--passphrase", ""]
        subprocess.run(base + ["--quick-generate-key", "ubp test <t@example>", "ed25519", "sign", "never"], check=True, capture_output=True)
        out = subprocess.run(["gpg", "--homedir", cls.home, "--with-colons", "--list-keys"], capture_output=True, text=True).stdout
        cls.fpr = [line.split(":")[9] for line in out.splitlines() if line.startswith("fpr")][0]
        cls.key = os.path.join(cls.tmp, "key.asc")
        subprocess.run(["gpg", "--homedir", cls.home, "--armor", "--output", cls.key, "--export", cls.fpr], check=True, capture_output=True)
        cls.data = os.path.join(cls.tmp, "unbound-1.26.2.tar.gz")
        open(cls.data, "wb").write(b"tarball")
        subprocess.run(base + ["--armor", "--detach-sign", "--output", cls.data + ".asc", cls.data], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["gpgconf", "--homedir", cls.home, "--kill", "all"], capture_output=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_signature_checks(self):
        self.assertTrue(upstream.verify_signature(self.key, self.data + ".asc", self.data, self.fpr))
        with self.assertRaises(UbpError):
            upstream.verify_signature(self.key, self.data + ".asc", self.data, upstream.FINGERPRINT)
        bad = os.path.join(self.tmp, "bad.tar.gz")
        open(bad, "wb").write(b"tampered")
        with self.assertRaises(UbpError):
            upstream.verify_signature(self.key, self.data + ".asc", bad, self.fpr)

    def test_fetched_key_with_wrong_fingerprint_is_refused(self):
        cache = os.path.join(self.tmp, "cache")
        with self.assertRaises(UbpError) as caught:
            upstream.obtain_key(os.path.join(self.tmp, "nocode"), cache, fetcher=lambda url: open(self.key, "rb").read())
        self.assertIn("指紋", str(caught.exception))
        self.assertEqual(os.listdir(cache), [])

    def test_download_rejects_wrong_checksum(self):
        def fetcher(url):
            if url.endswith(".sha256"):
                return b"0" * 64 + b"  unbound-1.26.2.tar.gz\n"
            return open(self.key, "rb").read() if url.endswith("releases-g2.asc") else b"data"

        code = os.path.join(self.tmp, "code")
        os.makedirs(os.path.join(code, "packaging", "keys"))
        with self.assertRaises(UbpError):
            upstream.download(code, os.path.join(self.tmp, "w"), "1.26.2", fetcher)


class DetectTest(unittest.TestCase):
    def test_detect_latest_and_pinned_fingerprint(self):
        html = b'<a href="unbound-1.26.2.tar.gz">x</a><a href="unbound-1.27.0rc1.tar.gz">y</a><a href="unbound-1.26.10.tar.gz">'
        self.assertEqual(upstream.detect_latest(lambda url: html), "1.26.10")
        with self.assertRaises(UbpError):
            upstream.detect_latest(lambda url: b"nothing")
        self.assertEqual(upstream.FINGERPRINT, "2310 1869 0C4D 903E F419 146A A144 323D EAAC DF45".replace(" ", ""))


class ToolUnitTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()

    def test_config_defaults_and_values(self):
        config, errors = self.tool.parse_config("﻿[source]\r\nrepository = o/unbound-packager\r\n[checks]\r\nresolve = a.example b.example\r\nstable_seconds = 30\r\n")
        self.assertEqual(errors, [])
        self.assertEqual(config["checks"]["resolve"], ["a.example", "b.example"])
        self.assertEqual(config["checks"]["stable_seconds"], 30)
        self.assertEqual(config["checks"]["dnssec_valid"], "")
        self.assertEqual(config["packages"]["install"], [])

    def test_config_errors_have_line_numbers_and_are_all_reported(self):
        text = "[source]\nrepository = not a repo\nbase_url = ftp://x\n[bogus]\n[checks]\nresolve = a;b\nunknown = 1\nstable_seconds = 010\nstable_seconds = 5\nlan_address = 999.1.1.1\nno equals sign\n"
        _, errors = self.tool.parse_config(text)
        self.assertEqual([line for line, _ in errors], [2, 3, 4, 6, 7, 8, 9, 10, 11])
        joined = "\n".join(message for _, message in errors)
        for needed in ("所有者/リポジトリ名", "https://", "知らないセクション", "知らないキー", "0 から 600", "IP アドレス", "キー = 値"):
            self.assertIn(needed, joined)

    def test_only_https_or_loopback_sources(self):
        self.assertEqual(self.tool.convert("url", "https://github.com/o/r/releases/")[0], "https://github.com/o/r/releases")
        self.assertEqual(self.tool.convert("url", "http://127.0.0.1:8000")[1], "")
        self.assertNotEqual(self.tool.convert("url", "http://example.com")[1], "")
        self.assertNotEqual(self.tool.convert("url", "http://127.0.0.1.evil.example")[1], "")

    def test_template_is_valid(self):
        config, errors = self.tool.parse_config(self.tool.CONFIG_TEMPLATE.replace("@REPOSITORY@", "o/unbound-packager"))
        self.assertEqual(errors, [])
        self.assertEqual(config["source"]["repository"], "o/unbound-packager")

    def test_tar_safety(self):
        def members(names):
            with tarfile.open(fileobj=io.BytesIO(helpers.make_tar([(n, "x") for n in names]))) as tar:
                return [name for name, _ in self.tool.safe_members(tar)]

        self.assertEqual(members(["./unbound.conf", "./sub/a.conf"]), ["unbound.conf", "sub/a.conf"])
        for bad in ("../evil", "/etc/passwd", "a/../../b"):
            with self.assertRaises(self.tool.StepError):
                members([bad])

    def test_sums_parser_ignores_bad_lines(self):
        good = "a" * 64
        sums = self.tool.parse_sums(f"{good}  ok.rpm\n{good}  ../evil\nshort  x\n{good} *star.deb\n".encode())
        self.assertEqual(sums, {"ok.rpm": good, "star.deb": good})

    def test_tool_uses_only_standard_library(self):
        import ast
        import sys

        names = set()
        for node in ast.walk(ast.parse(helpers.TOOL.read_text())):
            if isinstance(node, ast.Import):
                names |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module.split(".")[0])
        self.assertTrue(names <= set(sys.stdlib_module_names), names - set(sys.stdlib_module_names))


class AptPolicyTest(unittest.TestCase):
    POLICY = (
        "unbound:\n  Installed: 1.26.2-0+local1\n  Candidate: 1.26.2-0+local1\n  Version table:\n"
        " *** 1.26.2-0+local1 100\n        100 /var/lib/dpkg/status\n"
        "     1.24.2-1ubuntu2.4 -1\n        500 http://archive.ubuntu.com/ubuntu resolute-updates/universe amd64 Packages\n"
        "     1.24.2-1ubuntu2 -1\n        500 http://archive.ubuntu.com/ubuntu resolute/universe amd64 Packages\n"
    )

    def test_os_versions_must_all_be_pinned_away(self):
        import sys

        sys.path.insert(0, str(helpers.ROOT / "tests" / "container"))
        import server_test

        self.assertTrue(server_test.os_versions_pinned_away(self.POLICY))
        self.assertFalse(server_test.os_versions_pinned_away(self.POLICY.replace("1ubuntu2 -1", "1ubuntu2 500")))
        self.assertFalse(server_test.os_versions_pinned_away("unbound:\n  Installed: (none)\n  Candidate: (none)\n  Version table:\n"))
        self.assertFalse(server_test.os_versions_pinned_away("N: Unable to locate package unbound\n"))
