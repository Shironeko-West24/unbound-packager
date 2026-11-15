import json
import unittest

import helpers
from ubp import pkgedit
from ubp.util import UbpError

OPTIONS = json.loads((helpers.ROOT / "packaging" / "options.json").read_text())
SPEC = """Name: unbound
Version: 1.24.2
Release: 7%{?dist}.6
Source0: https://example/unbound-%{version}.tar.gz
Patch1: unbound-fedora-config.patch
Patch2: unbound-system-tls.patch
Patch10: unbound-cve.patch

%prep
%autosetup -p1
%patch -P 2 -p1
%patch10 -p1

%build
%configure --with-libevent --with-pthreads --with-ssl \\
    --disable-static --enable-ipsecmod --enable-cachedb --with-libhiredis \\
    --enable-sha2 --disable-gost --enable-ecdsa \\
    --with-rootkey-file=%{_sharedstatedir}/unbound/root.key

%changelog
* Mon Jan 01 2026 Someone <s@example> - 1.24.2-7
- old
"""
RULES = """#!/usr/bin/make -f
override_dh_auto_configure:
\tdh_auto_configure -- \\
\t\t--disable-rpath \\
\t\t--enable-cachedb \\
\t\t--with-libhiredis \\
\t\t--with-pythonmodule \\
\t\t--enable-subnet
"""
CONTROL = """Source: unbound
Build-Depends:
 debhelper-compat (= 13),
 libevent-dev,
 libhiredis-dev,
 libssl-dev
Standards-Version: 4.6

Package: unbound
Architecture: any

Package: libunbound8
Architecture: any

Package: python3-unbound
Architecture: any
"""


class SpecTest(unittest.TestCase):
    def test_version_and_release(self):
        text = pkgedit.spec_set_version_release(SPEC, "1.26.2", "1.local%{?dist}")
        self.assertIn("Version: 1.26.2", text)
        self.assertIn("Release: 1.local%{?dist}", text)
        with self.assertRaises(UbpError):
            pkgedit.spec_set_version_release("Name: x\n", "1.0.0", "1")

    def test_patch_listing_and_removal(self):
        self.assertEqual(pkgedit.spec_patches(SPEC), [(1, "unbound-fedora-config.patch"), (2, "unbound-system-tls.patch"), (10, "unbound-cve.patch")])
        text = pkgedit.spec_remove_patches(SPEC, [2, 10])
        self.assertIn("Patch1:", text)
        self.assertNotIn("Patch2:", text)
        self.assertNotIn("%patch -P 2", text)
        self.assertNotIn("%patch10", text)
        self.assertIn("%autosetup", text)

    def test_patch_strip_and_keyring_from_expanded_spec(self):
        expanded = (
            "/usr/lib/rpm/redhat/gpgverify --keyring='/w/SOURCES/Yorgos.asc' --signature='/w/SOURCES/u.tar.gz.asc' --data='/w/SOURCES/u.tar.gz'\n"
            "/usr/lib/rpm/rpmuncompress /w/SOURCES/unbound-fedora-config.patch | \n"
            "/usr/bin/patch -p2 -s --fuzz=0 --no-backup-if-mismatch -f\n"
            "/usr/lib/rpm/rpmuncompress /w/SOURCES/unbound-cve.patch | \n"
            "/usr/bin/patch -p1 -s --fuzz=0 --no-backup-if-mismatch -f\n"
        )
        self.assertEqual(pkgedit.spec_patch_strip(expanded, "", "unbound-fedora-config.patch"), 2)
        self.assertEqual(pkgedit.spec_patch_strip(expanded, "", "unbound-cve.patch"), 1)
        self.assertEqual(pkgedit.spec_patch_strip("", "%prep\n%autosetup -c -N\n%autopatch -p2\n", "x.patch"), 2)
        with self.assertRaises(UbpError):
            pkgedit.spec_patch_strip("", "%prep\n%setup -q\n", "x.patch")
        self.assertEqual(pkgedit.spec_gpg_keyrings(expanded), ["Yorgos.asc"])
        self.assertEqual(pkgedit.spec_gpg_keyrings("install -m 0644 /w/SOURCES/root.key /etc\n"), [])

    def test_configure_is_unified(self):
        text, count = pkgedit.normalize_configure(SPEC, OPTIONS["configure"], pkgedit.RPM_CONFIGURE_ANCHOR)
        self.assertEqual(count, 1)
        line = " ".join(l.strip().rstrip("\\") for l in text.split("%build")[1].split("%changelog")[0].splitlines())
        self.assertEqual(pkgedit.check_configure_line(line, OPTIONS["configure"]), [])
        for bad in ("--enable-ipsecmod", "--enable-cachedb", "--with-libhiredis "):
            self.assertNotIn(bad, line)
        self.assertEqual(line.count("--with-libevent"), 1)
        self.assertEqual(line.count("--enable-sha2"), 1)
        self.assertIn("--with-pthreads", line)
        self.assertIn("--with-rootkey-file=", line)
        self.assertIn("--disable-sha1", line)

    def test_changelog_history_is_not_rewritten(self):
        spec = SPEC + "* Mon Jan 01 2024 Someone <s@example> - 1.20.0-1\n- Build with --enable-ecdsa and --with-libhiredis\n"
        text, _ = pkgedit.normalize_configure(spec, OPTIONS["configure"], pkgedit.RPM_CONFIGURE_ANCHOR)
        self.assertIn("- Build with --enable-ecdsa and --with-libhiredis\n", text)
        self.assertEqual(text.split("%changelog")[1], spec.split("%changelog")[1])

    def test_configure_missing_anchor_fails(self):
        with self.assertRaises(UbpError):
            pkgedit.normalize_configure("%build\nmake\n", OPTIONS["configure"], pkgedit.RPM_CONFIGURE_ANCHOR)

    def test_changelog_and_module_config(self):
        text = pkgedit.spec_add_changelog(SPEC, "1.26.2", "1.local", "note", "Sat Nov 14 2026", "packager (unofficial build) <p@example.invalid>")
        self.assertIn("* Sat Nov 14 2026 packager (unofficial build) <p@example.invalid> - 1.26.2-1.local\n- note", text)
        conf = 'server:\n    module-config: "ipsecmod validator iterator"\n'
        self.assertIn('module-config: "validator iterator"', pkgedit.replace_module_config(conf, "ipsecmod"))

    def test_server_options_are_set_only_where_active(self):
        conf = "server:\n\tharden-referral-path: yes\n\t# harden-referral-path: yes\n\tharden-glue: yes\n"
        text, count = pkgedit.set_server_option(conf, "harden-referral-path", "no")
        self.assertEqual(count, 1)
        self.assertIn("\tharden-referral-path: no\n", text)
        self.assertIn("\t# harden-referral-path: yes\n", text)
        self.assertIn("\tharden-glue: yes\n", text)
        self.assertEqual(OPTIONS["server_options"], {"harden-referral-path": "no"})

    def test_unbound_version_output(self):
        out = "Version 1.26.2\n\nConfigure line: --enable-pie --disable-sha1\nLinked libs: libevent\nLinked modules: dns64 python subnetcache respip validator iterator [typecodes: x]\n"
        info = pkgedit.parse_unbound_version_output(out)
        self.assertEqual(info["modules"], OPTIONS["expected_modules"])
        self.assertIn("--enable-tfo-client", pkgedit.check_configure_line(info["configure"], OPTIONS["configure"]))


class InTestSelectionTest(unittest.TestCase):
    def test_source_packages_are_not_installed(self):
        import importlib.util
        import os
        import tempfile

        spec = importlib.util.spec_from_file_location("ubp_intest", helpers.ROOT / "packaging" / "intest.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            for name in ("unbound-1.26.2-1.local.el10_2.x86_64.rpm", "unbound-libs-1.26.2-1.local.el10_2.x86_64.rpm",
                         "unbound-1.26.2-1.local.el10_2.src.rpm", "unbound_1.26.2-0+local1_amd64.deb",
                         "unbound_1.26.2-0+local1.dsc", "unbound_1.26.2.orig.tar.gz"):
                open(os.path.join(directory, name), "w").close()
            rpms, debs = module.package_files(directory)
        self.assertEqual([os.path.basename(p) for p in rpms], ["unbound-1.26.2-1.local.el10_2.x86_64.rpm", "unbound-libs-1.26.2-1.local.el10_2.x86_64.rpm"])
        self.assertEqual([os.path.basename(p) for p in debs], ["unbound_1.26.2-0+local1_amd64.deb"])


class InTestOutputTest(unittest.TestCase):
    def test_results_are_shown_as_ok_and_ng(self):
        import contextlib
        import importlib.util
        import io

        spec = importlib.util.spec_from_file_location("ubp_intest_output", helpers.ROOT / "packaging" / "intest.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            module.record("全サブパッケージの導入", True)
            module.record("名前解決と DNSSEC の確認", False, "example.com（127.0.0.1）: 名前解決できません（rcode 2）\n次の行")
        self.assertEqual(
            buffer.getvalue(),
            "OK 全サブパッケージの導入\nNG 名前解決と DNSSEC の確認\n   example.com（127.0.0.1）: 名前解決できません（rcode 2）\n   次の行\n",
        )
        self.assertNotIn("合格", buffer.getvalue())


class DebTest(unittest.TestCase):
    def test_rules(self):
        text, count = pkgedit.normalize_configure(RULES, OPTIONS["configure"], pkgedit.DEB_CONFIGURE_ANCHOR)
        self.assertEqual(count, 1)
        self.assertNotIn("--enable-cachedb", text)
        self.assertIn("--disable-cachedb", text)
        self.assertIn("--disable-rpath", text)
        self.assertEqual(text.count("--with-pythonmodule"), 1)
        listed = pkgedit.rules_list_missing(text)
        self.assertIn("override_dh_missing:\n\tdh_missing --list-missing", listed)
        self.assertIn("--list-missing", pkgedit.rules_list_missing("override_dh_missing:\n\tdh_missing --fail-missing\n"))

    def test_libunbound_only_build_keeps_its_own_python_settings(self):
        rules = (
            "CONFIGURE_ARGS = \\\n\t--disable-rpath \\\n\t--with-libevent \\\n\t--enable-tfo-client \\\n\n"
            "ifneq ($(DEB_HOST_ARCH), i386)\n\tredis=--with-libhiredis\nendif\n\n"
            "b/unbound/config.h:\n\tPYTHON_VERSION=\"3\" \\\n\t    dh_auto_configure -Bb/unbound -- \\\n"
            "\t\t--with-pythonmodule \\\n\t\t--enable-cachedb \\\n\t\t$(redis) \\\n\t\t${CONFIGURE_ARGS}\n"
            "b/unbound/build-stamp: b/unbound/config.h\n\t${MAKE} -Cb/unbound\n\n"
            "b/libunbound/config.h:\n\t    dh_auto_configure -Bb/libunbound -- \\\n"
            "\t\t--with-libunbound-only \\\n\t\t--without-pythonmodule \\\n\t\t--without-pyunbound \\\n"
            "\t\t--enable-event-api \\\n\t\t${CONFIGURE_ARGS}\n"
        )
        text, count = pkgedit.normalize_configure(rules, OPTIONS["configure"], pkgedit.DEB_CONFIGURE_ANCHOR, OPTIONS["configure_exceptions"])
        self.assertEqual(count, 2)
        main, lib = text.split("b/libunbound/config.h:")
        self.assertEqual(pkgedit.check_configure_line(main, OPTIONS["configure"]), [])
        self.assertNotIn("--enable-cachedb", main)
        self.assertIn("\tredis=\n", main)
        self.assertNotIn("--with-pythonmodule", lib)
        self.assertNotIn("--with-pyunbound", lib)
        self.assertIn("--without-pythonmodule", lib)
        self.assertIn("--without-pyunbound", lib)
        self.assertIn("--enable-event-api", lib)
        self.assertIn("--disable-sha1", lib)
        self.assertIn("--with-libevent", lib)
        plain, _ = pkgedit.normalize_configure(rules, OPTIONS["configure"], pkgedit.DEB_CONFIGURE_ANCHOR)
        self.assertIn("--with-pythonmodule", plain.split("b/libunbound/config.h:")[1])

    def test_control(self):
        text, count = pkgedit.control_remove_build_dep(CONTROL, "libhiredis-dev")
        self.assertEqual(count, 1)
        self.assertNotIn("libhiredis", text)
        self.assertIn(" libevent-dev,\n libssl-dev", text)
        inline, count = pkgedit.control_remove_build_dep("Build-Depends: a, libhiredis-dev (>= 1), b\n", "libhiredis-dev")
        self.assertEqual((inline, count), ("Build-Depends: a, b\n", 1))
        self.assertEqual(pkgedit.control_binary_packages(CONTROL), ["unbound", "libunbound8", "python3-unbound"])

    def test_changelog(self):
        top = "unbound (1.24.2-1ubuntu2) resolute; urgency=medium\n\n  * x\n"
        self.assertEqual(pkgedit.changelog_top(top), ("unbound", "1.24.2-1ubuntu2", "resolute"))
        entry = pkgedit.changelog_entry("unbound", "1.26.2-0+local1", "resolute", "note", "packager (unofficial build) <p@example.invalid>")
        self.assertTrue(entry.startswith("unbound (1.26.2-0+local1) resolute; urgency=medium\n\n  * note\n\n -- packager (unofficial build) <p@example.invalid>  "))
        self.assertEqual(pkgedit.series_names("a.patch\n# c\n\nb.patch -p1\n"), ["a.patch", "b.patch"])
        self.assertEqual(
            pkgedit.series_entries("a.patch\n# c\n\nb.patch -p0\nc.patch -p 2 # note\n"),
            [("a.patch", 1, "a.patch"), ("b.patch", 0, "b.patch -p0"), ("c.patch", 2, "c.patch -p 2")],
        )
