#!/usr/bin/python3 -I
import argparse
import glob
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pkgcommon as c
from ubp import licenses, patches, pkgedit, versions

DEB = "/work/deb"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--jobs", type=int, default=0)
    args = parser.parse_args()
    opts = c.options()
    versions.parse(args.version)
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    os.makedirs(DEB, exist_ok=True)
    c.sh(["apt-get", "update"], env=env)
    c.sh(["apt-get", "source", "unbound"], cwd=DEB, env=env)
    old_dir = c.one(os.path.join(DEB, "unbound-*/"), "OS のソースパッケージの展開先").rstrip("/")
    changelog_path = os.path.join(old_dir, "debian", "changelog")
    source, old_full, distribution = pkgedit.changelog_top(open(changelog_path, encoding="utf-8").read())
    c.say(f"OS のソースパッケージ: {old_full}（{distribution}）")
    old_orig = [p for p in glob.glob(os.path.join(DEB, "unbound_*.orig.tar.*")) if not p.endswith((".asc", ".sig"))]
    if len(old_orig) != 1:
        c.fail("旧版の orig.tar が 1 つに決まりません。")
    new_tar = os.path.join(c.IN, f"unbound-{args.version}.tar.gz")
    c.extract_stripped(old_orig[0], "/work/tree/old")
    c.extract_stripped(new_tar, "/work/tree/new")
    new_dir = os.path.join(DEB, f"build/unbound-{args.version}")
    shutil.rmtree(os.path.dirname(new_dir), ignore_errors=True)
    os.makedirs(os.path.dirname(new_dir))
    shutil.copytree("/work/tree/new", new_dir, symlinks=True)
    shutil.copytree(os.path.join(old_dir, "debian"), os.path.join(new_dir, "debian"), symlinks=True)
    shutil.copy2(new_tar, os.path.join(os.path.dirname(new_dir), f"unbound_{args.version}.orig.tar.gz"))
    shutil.copy2(new_tar + ".asc", os.path.join(os.path.dirname(new_dir), f"unbound_{args.version}.orig.tar.gz.asc"))
    debian = os.path.join(new_dir, "debian")
    series_path = os.path.join(debian, "patches", "series")
    decisions = []
    if os.path.exists(series_path):
        entries = pkgedit.series_entries(open(series_path, encoding="utf-8").read())
        try:
            decisions = patches.decide(
                [(name, os.path.join(debian, "patches", name), strip) for name, strip, _ in entries],
                "/work/tree/old",
                "/work/tree/new",
                args.version,
                opts["drop_patches"],
                "/work/patchwork",
                commit_checker=patches.github_commit_checker(c.fetch),
                advisory=patches.advisory_checker(c.fetch),
            )
        except patches.PatchError as exc:
            c.fail(str(exc))
        kept = []
        for (name, _, line), decision in zip(entries, decisions):
            c.say(patches.describe(name, decision.action, decision.reason))
            target = os.path.join(debian, "patches", name)
            if decision.action == "drop":
                os.unlink(target)
                continue
            if decision.action == "rebased":
                shutil.copy2(decision.new_path, target)
            kept.append(line)
        open(series_path, "w", encoding="utf-8").write("".join(line + "\n" for line in kept))
    copyright_text = open(os.path.join(debian, "copyright"), encoding="utf-8", errors="replace").read()
    try:
        packaging = licenses.check_packaging(copyright_text, opts["allowed_packaging_licenses"])
    except Exception as exc:
        c.fail(str(exc))
    c.say("パッケージングのライセンス: " + "、".join(packaging))
    control_path = os.path.join(debian, "control")
    control = open(control_path, encoding="utf-8").read()
    try:
        control = licenses.replace_maintainer(control, opts["maintainer"])
    except Exception as exc:
        c.fail(str(exc))
    for package in opts["drop_build_deps"].get("ubuntu2604", []):
        control, count = pkgedit.control_remove_build_dep(control, package)
        c.say(f"ビルド依存から {package} を外しました（{count} か所）。")
    open(control_path, "w", encoding="utf-8").write(control)
    rules_path = os.path.join(debian, "rules")
    rules = open(rules_path, encoding="utf-8").read()
    try:
        rules, count = pkgedit.normalize_configure(rules, opts["configure"], pkgedit.DEB_CONFIGURE_ANCHOR, opts.get("configure_exceptions", []))
    except Exception as exc:
        c.fail(str(exc))
    c.say(f"configure の行 {count} か所でオプションを統一しました。")
    rules = pkgedit.rules_list_missing(rules)
    open(rules_path, "w", encoding="utf-8").write(rules)
    for test in opts["disabled_tests"]:
        for path in glob.glob(os.path.join(new_dir, "testdata", test + ".*")):
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)
            c.say(f"上流のテスト {os.path.basename(path)} を無効にしました。")
    for conf in sorted(glob.glob(os.path.join(debian, "**", "*.conf"), recursive=True)):
        before = open(conf, encoding="utf-8", errors="replace").read()
        after = before
        for name, value in opts.get("server_options", {}).items():
            after, count = pkgedit.set_server_option(after, name, value)
            if count:
                c.say(f"{os.path.relpath(conf, debian)} の {name} を {value} にしました。")
        if after != before:
            open(conf, "w", encoding="utf-8").write(after)
    key = os.path.join(c.IN, "signing-key.asc")
    upstream_key = os.path.join(debian, "upstream", "signing-key.asc")
    if os.path.exists(upstream_key):
        shutil.copy2(key, upstream_key)
    new_full = versions.deb_version(args.version, args.revision, versions.deb_epoch(old_full))
    entry = pkgedit.changelog_entry(source, new_full, distribution, f"unbound-packager: upstream {args.version}, unified build options", opts["maintainer"])
    new_changelog = os.path.join(debian, "changelog")
    open(new_changelog, "w", encoding="utf-8").write(entry + open(changelog_path, encoding="utf-8").read())
    c.sh(["apt-get", "-y", "build-dep", "./"], cwd=new_dir, env=env)
    log = os.path.join(c.OUT, "build.log")
    os.makedirs(c.OUT, exist_ok=True)
    cmd = ["dpkg-buildpackage", "-uc", "-us"]
    if args.jobs:
        cmd.append(f"-j{args.jobs}")
    c.sh(cmd, cwd=new_dir, env=env, log=log)
    expected = set(pkgedit.control_binary_packages(control))
    built = {}
    for path in glob.glob(os.path.join(os.path.dirname(new_dir), "*.deb")):
        name = c.out(["dpkg-deb", "-f", path, "Package"]).strip()
        built[name] = path
    missing = sorted(expected - set(built))
    if missing:
        c.fail("サブパッケージが欠けています: " + "、".join(missing))
    unpackaged = []
    for line in open(log, encoding="utf-8", errors="replace"):
        match = re.search(r"dh_missing: warning: (\S+) exists in debian/tmp but is not installed", line)
        if match:
            unpackaged.append(match.group(1))
    pkg_version = c.out(["dpkg-deb", "-f", built["unbound"], "Version"]).strip()
    parent = os.path.dirname(new_dir)
    sources_out = []
    for pattern in ("*.dsc", "*.debian.tar.*", "*.orig.tar.*"):
        sources_out += glob.glob(os.path.join(parent, pattern))
    if not any(path.endswith(".dsc") for path in sources_out):
        c.fail("deb のソースパッケージ（.dsc）が作られていません。")
    c.write_manifest("ubuntu2604", args.version, pkg_version, [(n, p) for n, p in built.items() if n in expected],
                     expected, decisions, unpackaged,
                     {"distro_source": old_full, "licenses": licenses.all_licenses(copyright_text)}, sources=sources_out)


if __name__ == "__main__":
    main()
