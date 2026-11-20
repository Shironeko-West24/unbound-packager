import os
import shutil
import subprocess
import tempfile
import unittest

import helpers
from ubp import patches

OLD = {"a.c": "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n", "doc/Changelog": "old\n"}


def write(tree, files):
    for name, text in files.items():
        path = os.path.join(tree, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)


def diff(base, files, out, header=""):
    work = tempfile.mkdtemp()
    try:
        shutil.copytree(base, os.path.join(work, "a"))
        shutil.copytree(base, os.path.join(work, "b"))
        write(os.path.join(work, "b"), files)
        proc = subprocess.run(["diff", "-ruN", "a", "b"], cwd=work, capture_output=True, text=True)
        with open(out, "w") as handle:
            handle.write(header + proc.stdout)
    finally:
        shutil.rmtree(work)


@unittest.skipUnless(shutil.which("patch") and shutil.which("git") and shutil.which("diff"), "patch、git、diff が必要")
class PatchDecisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = os.path.join(self.tmp, "old")
        self.new = os.path.join(self.tmp, "new")
        write(self.old, OLD)
        self.addCleanup(shutil.rmtree, self.tmp)

    def decide(self, new_files, patch_files, name="fix.patch", header="", checker=None):
        write(self.new, dict(OLD, **new_files))
        path = os.path.join(self.tmp, name)
        diff(self.old, patch_files, path, header)
        return patches.decide([(name, path)], self.old, self.new, "1.26.2", ["*system-tls*"], os.path.join(self.tmp, "w"), checker)[0]

    def test_adopt_when_applies(self):
        decision = self.decide({}, {"a.c": OLD["a.c"].replace("five", "FIVE")})
        self.assertEqual(decision.action, "adopt")

    def test_drop_when_already_upstream(self):
        changed = OLD["a.c"].replace("five", "FIVE")
        decision = self.decide({"a.c": changed}, {"a.c": changed})
        self.assertEqual(decision.action, "drop")
        self.assertIn("ソースにパッチの変更があることを確認", decision.reason)

    def test_drop_by_policy(self):
        decision = self.decide({}, {"a.c": OLD["a.c"].replace("five", "FIVE")}, name="unbound-system-tls.patch")
        self.assertEqual((decision.action, "このビルドでは適用しない種類のパッチ" in decision.reason), ("drop", True))

    def test_drop_when_cve_in_changelog(self):
        decision = self.decide(
            {"doc/Changelog": "- Fix CVE-2026-1234 thing\n", "a.c": "totally\ndifferent\n"},
            {"a.c": OLD["a.c"].replace("five", "FIVE")},
            header="Subject: fix CVE-2026-1234\n\n",
        )
        self.assertEqual(decision.action, "drop")
        self.assertIn("CVE-2026-1234", decision.reason)

    def test_drop_when_commit_is_included(self):
        commit = "0123456789abcdef0123456789abcdef01234567"
        decision = self.decide(
            {"a.c": "totally\ndifferent\n"},
            {"a.c": OLD["a.c"].replace("five", "FIVE")},
            header=f"From {commit} Mon Sep 17 00:00:00 2001\n\n",
            checker=lambda c, v: c == commit and v == "1.26.2",
        )
        self.assertEqual(decision.action, "drop")
        self.assertIn(commit[:12], decision.reason)

    def test_rebase_with_three_way_merge(self):
        new_text = OLD["a.c"].replace("three", "THREE").replace("seven", "SEVEN")
        decision = self.decide({"a.c": new_text}, {"a.c": OLD["a.c"].replace("five", "FIVE")})
        self.assertEqual(decision.action, "rebased")
        check = os.path.join(self.tmp, "check")
        shutil.copytree(self.new, check)
        self.assertTrue(patches.applies(check, decision.new_path))
        patches.apply(check, decision.new_path)
        self.assertEqual(open(os.path.join(check, "a.c")).read(), new_text.replace("five", "FIVE"))

    def test_conflict_stops(self):
        with self.assertRaises(patches.PatchError):
            self.decide({"a.c": OLD["a.c"].replace("five", "CINQ")}, {"a.c": OLD["a.c"].replace("five", "FIVE")})

    def test_later_patch_sees_earlier_one(self):
        write(self.new, OLD)
        first = os.path.join(self.tmp, "1.patch")
        second = os.path.join(self.tmp, "2.patch")
        step1 = OLD["a.c"].replace("five", "FIVE")
        diff(self.old, {"a.c": step1}, first)
        mid = os.path.join(self.tmp, "mid")
        write(mid, dict(OLD, **{"a.c": step1}))
        diff(mid, {"a.c": step1.replace("FIVE", "FIVE!")}, second)
        result = patches.decide([("1.patch", first), ("2.patch", second)], self.old, self.new, "1.26.2", [], os.path.join(self.tmp, "w"))
        self.assertEqual([d.action for d in result], ["adopt", "adopt"])

    def prefixed(self, files, out, prefix="unbound-1.24.2"):
        work = tempfile.mkdtemp()
        try:
            for side, content in (("a", OLD), ("b", dict(OLD, **files))):
                write(os.path.join(work, side, prefix), content)
            proc = subprocess.run(["diff", "-ruN", "a", "b"], cwd=work, capture_output=True, text=True)
            with open(out, "w") as handle:
                handle.write(proc.stdout)
        finally:
            shutil.rmtree(work)

    def test_strip_level_two_is_used_and_kept_when_rebasing(self):
        write(self.new, dict(OLD, **{"a.c": OLD["a.c"].replace("three", "THREE").replace("seven", "SEVEN")}))
        path = os.path.join(self.tmp, "p2.patch")
        self.prefixed({"a.c": OLD["a.c"].replace("five", "FIVE")}, path)
        with self.assertRaises(patches.PatchError):
            patches.decide([("p2.patch", path)], self.old, self.new, "1.26.2", [], os.path.join(self.tmp, "w1"))
        decision = patches.decide([("p2.patch", path, 2)], self.old, self.new, "1.26.2", [], os.path.join(self.tmp, "w2"))[0]
        self.assertEqual(decision.action, "rebased")
        text = open(decision.new_path).read()
        self.assertIn("--- a/unbound-1.24.2/a.c", text)
        self.assertIn("+++ b/unbound-1.24.2/a.c", text)
        check = os.path.join(self.tmp, "check")
        shutil.copytree(self.new, check)
        self.assertTrue(patches.applies(check, decision.new_path, 2))

    def test_new_file_already_upstream_is_dropped_not_adopted(self):
        added = {"testdata/t.rpl": "one\ntwo\n"}
        decision = self.decide(added, added)
        self.assertEqual(decision.action, "drop")
        self.assertIn("ソースにパッチの変更があることを確認", decision.reason)

    def test_unapplied_patch_is_not_taken_as_reversed(self):
        path = os.path.join(self.tmp, "y.patch")
        diff(self.old, {"a.c": OLD["a.c"].replace("five", "FIVE")}, path)
        write(self.new, OLD)
        self.assertFalse(patches.applies_reversed(self.new, path))
        self.assertEqual(open(os.path.join(self.new, "a.c")).read(), OLD["a.c"])

    def test_already_applied_change_is_not_adopted(self):
        changed = OLD["a.c"].replace("five", "FIVE")
        path = os.path.join(self.tmp, "x.patch")
        diff(self.old, {"a.c": changed}, path)
        write(self.new, dict(OLD, **{"a.c": changed}))
        self.assertFalse(patches.applies(self.new, path))

    def test_drop_when_advisory_says_new_version_is_not_affected(self):
        page = (
            "<h2>Heap overflow</h2><p>CVE: CVE-2026-81634</p><p>Affects: Unbound up to and including version 1.26.0</p>"
            "<p>Not affected: Other versions</p><p>CVE: CVE-2026-99999</p><p>Affects: Unbound 1.20.0 up to and including version 1.26.2</p>"
        )
        self.assertEqual(patches.parse_advisories(page), {"CVE-2026-81634": "1.26.0", "CVE-2026-99999": "1.26.2"})
        checker = patches.advisory_checker(lambda url: page.encode())
        conflict = {"a.c": "totally\ndifferent\n"}
        change = {"a.c": OLD["a.c"].replace("five", "FIVE")}
        write(self.new, dict(OLD, **conflict))
        fixed = os.path.join(self.tmp, "fixed.patch")
        diff(self.old, change, fixed, "Subject: Fix CVE-2026-81634\n\n")
        decision = patches.decide([("fixed.patch", fixed)], self.old, self.new, "1.26.2", [], os.path.join(self.tmp, "w"), advisory=checker)[0]
        self.assertEqual(decision.action, "drop")
        self.assertIn("1.26.0", decision.reason)
        for cve in ("CVE-2026-99999", "CVE-2026-12345"):
            other = os.path.join(self.tmp, cve + ".patch")
            diff(self.old, change, other, f"Subject: Fix {cve}\n\n")
            with self.assertRaises(patches.PatchError):
                patches.decide([(cve + ".patch", other)], self.old, self.new, "1.26.2", [], os.path.join(self.tmp, "w"), advisory=checker)

    def test_unreachable_advisory_page_drops_nothing(self):
        def boom(url):
            raise OSError("down")

        self.assertIsNone(patches.advisory_checker(boom)("CVE-2026-81634"))

    def test_references(self):
        cves, commits = patches.references("x-CVE-2025-11411.patch", "From: a\nSubject: b\n\n--- a/x\n+++ b/x\n CVE-2000-0001\n")
        self.assertEqual(cves, ["CVE-2025-11411"])
        self.assertEqual(commits, [])
