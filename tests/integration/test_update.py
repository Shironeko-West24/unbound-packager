import json
import os
import shutil
import unittest

import helpers


class UpdateTest(helpers.Sandbox):
    def test_update_to_latest(self):
        self.seed()
        rc, out = self.tool("update")
        self.assertEqual(rc, 0, out)
        self.assertIn("更新と確認が終わりました", out)
        self.assertEqual(self.installed(), self.pkg("1.26.2"))
        state = self.state()
        self.assertEqual((state["current"], state["previous"]), (self.pkg("1.26.2"), self.pkg("1.26.1")))
        installs = [c["args"] for c in self.calls("dnf") if "install" in c["args"] and "--downloadonly" not in c["args"]]
        self.assertTrue(all("--disableexcludes=main" in args for args in installs))
        self.assertFalse(any("devel" in " ".join(args) for args in installs))
        order = [c["cmd"] + (" restart" if "restart" in c["args"] else "") for c in self.calls() if c["cmd"] in ("dnf", "unbound-checkconf") or "restart" in c["args"]]
        self.assertEqual(order[-3:], ["dnf", "unbound-checkconf", "systemctl restart"])
        self.assertEqual(sorted(os.listdir(self.root / "var/lib/unbound-packager/packages")), sorted([self.pkg("1.26.1"), self.pkg("1.26.2")]))

    def test_no_subcommand_means_update_and_version_can_be_chosen(self):
        self.seed()
        self.publish("1.26.3")
        rc, out = self.tool("--version", "1.26.2")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.installed(), self.pkg("1.26.2"))
        rc, out = self.tool("update", "--tag", "v1.26.3-1")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.installed(), self.pkg("1.26.3"))

    def test_highest_revision_of_a_version_is_used(self):
        self.seed()
        self.publish("1.26.2", revision=2, latest=False)
        rc, out = self.tool("update", "--version", "1.26.2")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.installed(), self.pkg("1.26.2", 2))

    def test_already_current_changes_nothing(self):
        self.seed()
        self.assertEqual(self.tool("update")[0], 0)
        self.clear_calls()
        rc, out = self.tool("update")
        self.assertEqual(rc, 0)
        self.assertIn("すでに対象の版です", out)
        self.assertEqual([c for c in self.calls("dnf") if "install" in c["args"]], [])

    def test_start_failure_rolls_back(self):
        self.seed()
        self.rules([{"cmd": "systemctl", "contains": "restart", "rc": 1, "stderr": "Job failed", "times": 1}])
        rc, out = self.tool("update")
        self.assertEqual(rc, 1, out)
        self.assertIn("切り戻しました", out)
        self.assertEqual(self.installed(), self.pkg("1.26.1"))
        self.assertEqual(self.state()["current"], self.pkg("1.26.1"))

    def test_checkconf_failure_restores_config(self):
        self.seed()
        conf = self.root / "etc/unbound/unbound.conf"
        before = conf.read_text()
        self.rules([{"cmd": "unbound-checkconf", "rc": 1, "stderr": "syntax error", "times": 1}])
        rc, out = self.tool("update")
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.installed(), self.pkg("1.26.1"))
        self.assertEqual(conf.read_text(), before)

    def test_restart_loop_is_detected(self):
        self.seed()
        self.write_config(stable=2)
        show = "ActiveState=active\nSubState=running\nNRestarts={n}\nMainPID=100\n"
        self.rules([
            {"cmd": "systemctl", "contains": "show", "stdout": show.format(n=0), "times": 1},
            {"cmd": "systemctl", "contains": "show", "stdout": show.format(n=3), "times": 1},
        ])
        rc, out = self.tool("update")
        self.assertEqual(rc, 1, out)
        self.assertIn("再起動を繰り返", out)
        self.assertEqual(self.installed(), self.pkg("1.26.1"))

    def test_name_resolution_failure_rolls_back(self):
        self.seed()
        self.dns.fail.add("example.com")
        rc, out = self.tool("update")
        self.assertEqual(rc, 1, out)
        self.assertIn("名前解決の確認に失敗", out)
        self.assertEqual(self.installed(), self.pkg("1.26.1"))

    def test_persistent_resolution_failure_still_rolls_back_after_flushing(self):
        self.seed()
        self.dns.fail.add("example.com")
        rc, out = self.tool("update")
        self.assertEqual(rc, 1, out)
        flushes = [c for c in self.calls("unbound-control") if c["args"] == ["flush_bogus"]]
        self.assertEqual(len(flushes), 5)
        self.assertIn("名前解決の確認に失敗しました。検証に失敗した応答のキャッシュを消して 5 回確認し直しましたが、成功しませんでした。", out)
        self.assertEqual(self.installed(), self.pkg("1.26.1"))

    def test_transient_resolution_failure_recovers_after_flushing(self):
        self.seed()
        self.dns.fail_times["example.com"] = 4
        rc, out = self.tool("update")
        self.assertEqual(rc, 0, out)
        self.assertIn("名前解決の確認に一度失敗したため、検証に失敗した応答のキャッシュを消して確認し直したところ、成功しました（確認し直した回数: 1）。", out)
        flushes = [c for c in self.calls("unbound-control") if c["args"] == ["flush_bogus"]]
        self.assertEqual(len(flushes), 1)
        self.assertEqual(self.installed(), self.pkg("1.26.2"))

    def test_rollback_failure_is_reported_loudly(self):
        self.seed()
        self.rules([{"cmd": "systemctl", "contains": "restart", "rc": 1, "stderr": "Job failed"}])
        rc, out = self.tool("update")
        self.assertEqual(rc, 3, out)
        self.assertIn("切り戻しにも失敗", out)

    def test_tampered_package_is_refused_before_any_change(self):
        self.seed()
        target = self.site / "latest" / "download" / "unbound-1.26.2-1.local.el10_2.x86_64.rpm"
        target.write_bytes(b"tampered")
        rc, out = self.tool("update")
        self.assertEqual(rc, 1, out)
        self.assertIn("SHA-256 が一致しません", out)
        self.assertEqual(self.installed(), self.pkg("1.26.1"))
        self.assertEqual(self.calls("dnf"), [])
        self.assertEqual(self.calls("systemctl"), [])

    def test_tampered_manifest_is_refused(self):
        self.seed()
        path = self.site / "latest" / "download" / "manifest.json"
        path.write_text(path.read_text().replace("1.26.2", "1.26.9"))
        rc, out = self.tool("update")
        self.assertEqual(rc, 1, out)
        self.assertIn("manifest.json の SHA-256", out)
        self.assertEqual(self.calls("dnf"), [])

    def test_unreachable_release_changes_nothing(self):
        self.seed()
        shutil.rmtree(self.site / "latest")
        rc, out = self.tool("update")
        self.assertEqual(rc, 1, out)
        self.assertIn("取得できませんでした", out)
        self.assertEqual(self.calls("dnf"), [])
        rc, out = self.tool("update", "--version", "9.9.9")
        self.assertEqual(rc, 1)
        self.assertIn("リリースが見つかりません", out)

    def test_dependency_problem_stops_before_stopping_unbound(self):
        self.seed()
        self.rules([{"cmd": "dnf", "contains": "--downloadonly", "rc": 1, "stderr": "nothing provides libfoo"}])
        rc, out = self.tool("update")
        self.assertEqual(rc, 1, out)
        self.assertIn("依存関係", out)
        self.assertEqual(self.calls("systemctl"), [])
        self.assertEqual(self.installed(), self.pkg("1.26.1"))

    def test_first_update_without_migrate_is_refused(self):
        self.publish("1.26.2")
        rc, out = self.tool("update")
        self.assertEqual(rc, 2)
        self.assertIn("migrate", out)
        self.assertEqual(self.calls(), [])

    def test_second_run_is_refused_while_one_runs(self):
        self.seed()
        import fcntl

        lock = self.root / "var/lib/unbound-packager/lock"
        with open(lock, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            rc, out = self.tool("update")
        self.assertEqual(rc, 75)
        self.assertIn("実行中", out)
        self.assertEqual(self.calls(), [])

    def test_bad_config_stops_everything(self):
        self.seed()
        self.write_config(extra="surprise = 1\n")
        rc, out = self.tool("update")
        self.assertEqual(rc, 2)
        self.assertIn("知らないキー", out)
        self.assertRegex(out, r"unbound-update\.conf:\d+:")
        self.assertEqual(self.calls(), [])

    def test_unsupported_os_is_refused(self):
        self.seed()
        (self.root / "etc/os-release").write_text('ID="rocky"\nVERSION_ID="9.5"\nID_LIKE="rhel"\n')
        rc, out = self.tool("update")
        self.assertEqual(rc, 1)
        self.assertIn("対応していない OS", out)
        self.assertEqual([c for c in self.calls("dnf") if "install" in c["args"]], [])

    def test_lan_address_is_checked_too(self):
        self.seed()
        self.write_config(extra="lan_address = 192.0.2.10\n")
        self.dns.asked.clear()
        self.assertEqual(self.tool("update")[0], 0)
        self.assertEqual(self.dns.asked.count("example.com"), 2)

    def test_check_verify_and_manual_rollback(self):
        self.seed()
        rc, out = self.tool("check")
        self.assertEqual(rc, 0)
        self.assertIn("v1.26.2-1", out)
        self.assertEqual(self.installed(), self.pkg("1.26.1"))
        self.assertEqual(self.tool("update")[0], 0)
        rc, out = self.tool("verify")
        self.assertEqual(rc, 0, out)
        self.assertIn("確認に合格しました", out)
        rc, out = self.tool("rollback")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.installed(), self.pkg("1.26.1"))
        state = self.state()
        self.assertEqual((state["current"], state["previous"]), (self.pkg("1.26.1"), self.pkg("1.26.2")))

    def test_self_update_checks_hash(self):
        self.publish("1.26.2")
        target = self.base / "tool-copy"
        rc, out = self.tool("self-update", "--target", str(target))
        self.assertEqual(rc, 0, out)
        self.assertEqual(target.read_bytes(), helpers.TOOL.read_bytes())
        (self.site / "latest" / "download" / "unbound-update").write_text("#!/usr/bin/python3\nprint('evil')\n")
        other = self.base / "tool-evil"
        rc, out = self.tool("self-update", "--target", str(other))
        self.assertEqual(rc, 1)
        self.assertFalse(other.exists())


class MigrateTest(helpers.Sandbox):
    def test_migrate_restores_config_and_excludes_os_packages(self):
        self.publish("1.26.2")
        old = "/usr/local/etc/unbound"
        tar = self.base / "old.tar"
        tar.write_bytes(helpers.make_tar([
            ("./unbound.conf", f'server:\n    include: "{old}/extra.conf"\n    pidfile: "/usr/local/var/run/unbound.pid"\n'),
            ("./extra.conf", 'local-zone: "home." static\n'),
            ("./sub/keys.key", "KEY"),
            ("./.ubp-meta.json", json.dumps({"config_dir": old})),
        ]))
        rc, out = self.tool("migrate", "--config", str(tar))
        self.assertEqual(rc, 0, out)
        conf = (self.root / "etc/unbound/unbound.conf").read_text()
        self.assertIn('include: "/etc/unbound/extra.conf"', conf)
        self.assertNotIn(old, conf)
        self.assertEqual((self.root / "etc/unbound/sub/keys.key").read_text(), "KEY")
        self.assertFalse((self.root / "etc/unbound/.ubp-meta.json").exists())
        self.assertIn("パスを置き換えました", out)
        self.assertIn("要確認", out)
        self.assertIn("/usr/local/var/run/unbound.pid", out)
        dnf = (self.root / "etc/dnf/dnf.conf").read_text()
        self.assertIn("excludepkgs=kernel*,unbound,unbound-devel,unbound-libs", dnf)
        self.assertIn("[other]\nexclude=foo", dnf)
        self.assertEqual(self.state()["current"], self.pkg("1.26.2"))

    def test_broken_saved_config_keeps_package_default(self):
        self.publish("1.26.2")
        default = (self.root / "etc/unbound/unbound.conf").read_text()
        tar = self.base / "old.tar"
        tar.write_bytes(helpers.make_tar([("./unbound.conf", "broken\n")]))
        self.rules([{"cmd": "unbound-checkconf", "rc": 1, "stderr": "syntax error"}])
        rc, out = self.tool("migrate", "--config", str(tar))
        self.assertEqual(rc, 1, out)
        self.assertIn("切り戻し先がありません", out)
        self.assertEqual((self.root / "etc/unbound/unbound.conf").read_text(), default)
        self.assertNotIn("unbound", (self.root / "etc/dnf/dnf.conf").read_text())

    def test_dangerous_tar_is_refused(self):
        self.publish("1.26.2")
        tar = self.base / "evil.tar"
        tar.write_bytes(helpers.make_tar([("../../evil", "x"), ("./unbound.conf", "")]))
        rc, out = self.tool("migrate", "--config", str(tar))
        self.assertEqual(rc, 1, out)
        self.assertIn("危険な名前", out)
        self.assertFalse((self.base / "evil").exists())

    def test_save_then_migrate_roundtrip(self):
        old = self.root / "usr/local/etc/unbound"
        old.mkdir(parents=True)
        (old / "unbound.conf").write_text('server:\n    include: "/usr/local/etc/unbound/a.conf"\n')
        (old / "a.conf").write_text("# a\n")
        out_file = self.base / "saved.tar"
        rc, out = self.tool("save", "--output", str(out_file))
        self.assertEqual(rc, 0, out)
        self.assertEqual(out_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.tool("save", "--output", str(out_file))[0] != 0, True)
        self.publish("1.26.2")
        rc, out = self.tool("migrate", "--config", str(out_file))
        self.assertEqual(rc, 0, out)
        self.assertIn('include: "/etc/unbound/a.conf"', (self.root / "etc/unbound/unbound.conf").read_text())

    def test_uninstall_removes_only_what_was_added(self):
        self.publish("1.26.2")
        self.assertEqual(self.tool("migrate")[0], 0)
        rc, out = self.tool("uninstall")
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.root / "etc/dnf/dnf.conf").read_text(), "[main]\ngpgcheck=1\nexcludepkgs=kernel*\n\n[other]\nexclude=foo\n")
        self.assertFalse(any("remove-service" in " ".join(c["args"]) for c in self.calls("firewall-cmd")))

    def test_firewall_closed_only_if_opened_here(self):
        self.publish("1.26.2")
        self.rules([
            {"cmd": "systemctl", "contains": "is-active firewalld", "stdout": "active\n"},
            {"cmd": "firewall-cmd", "contains": "--query-service=dns", "rc": 1},
        ])
        self.assertEqual(self.tool("migrate")[0], 0)
        self.assertTrue(any("--add-service=dns" in c["args"] for c in self.calls("firewall-cmd")))
        self.assertEqual(self.tool("uninstall")[0], 0)
        self.assertTrue(any("--remove-service=dns" in c["args"] for c in self.calls("firewall-cmd")))

    def test_init_creates_valid_config_once(self):
        path = self.root / "etc/unbound-packager/unbound-update.conf"
        path.unlink()
        self.assertEqual(self.tool("check")[0], 2)
        rc, out = self.tool("init", "--repository", "Owner-1/unbound-packager")
        self.assertEqual(rc, 0, out)
        self.assertIn("repository = Owner-1/unbound-packager", path.read_text())
        self.assertEqual(self.tool("init", "--repository", "Owner-1/unbound-packager")[0], 2)
        self.assertEqual(self.tool("init", "--repository", "bad repo")[0], 2)


class UbuntuTest(helpers.Sandbox):
    os_name = "ubuntu2604"

    def test_stale_package_lists_are_refreshed_once(self):
        self.publish("1.26.2")
        self.rules([{"cmd": "apt-get", "contains": "-s -y", "rc": 100, "stderr": "not installable", "times": 1}])
        rc, out = self.tool("migrate")
        self.assertEqual(rc, 0, out)
        self.assertIn("apt-get update", out)
        self.assertEqual(len([c for c in self.calls("apt-get") if c["args"] == ["update"]]), 1)

    def test_unresolvable_dependencies_still_stop_before_any_change(self):
        self.publish("1.26.2")
        self.rules([{"cmd": "apt-get", "contains": "-s -y", "rc": 100, "stderr": "not installable"}])
        rc, out = self.tool("migrate")
        self.assertEqual(rc, 1, out)
        self.assertIn("依存関係を解決できません", out)
        self.assertEqual([c for c in self.calls("apt-get") if "install" in c["args"] and "-s" not in c["args"]], [])
        self.assertEqual(self.calls("systemctl"), [])

    def test_migrate_update_and_uninstall(self):
        self.seed()
        pref = (self.root / "etc/apt/preferences.d/unbound-packager").read_text()
        self.assertEqual(pref, "Package: libunbound-dev libunbound8 unbound\nPin: release o=Ubuntu\nPin-Priority: -1\n")
        self.assertIn('"^libunbound8$";', (self.root / "etc/apt/apt.conf.d/52unbound-packager").read_text())
        rc, out = self.tool("update")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.installed(), "1.26.2-0+local1")
        installs = [c["args"] for c in self.calls("apt-get") if "install" in c["args"] and "-s" not in c["args"]]
        self.assertTrue(all("--allow-downgrades" in args for args in installs))
        self.assertFalse(any("libunbound-dev" in " ".join(args) for args in installs))
        self.assertEqual(self.tool("uninstall")[0], 0)
        self.assertFalse((self.root / "etc/apt/preferences.d/unbound-packager").exists())
