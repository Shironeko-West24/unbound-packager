#!/usr/bin/python3 -I
import argparse
import glob
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pkgcommon as c
from ubp import licenses, patches, pkgedit, versions

TOP = "/work/rpmbuild"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--jobs", type=int, default=0)
    args = parser.parse_args()
    opts = c.options()
    versions.parse(args.version)
    srpm_dir = "/work/srpm"
    os.makedirs(srpm_dir, exist_ok=True)
    c.sh(["dnf", "-y", "download", "--source", "unbound"], cwd=srpm_dir)
    srpm = c.one(os.path.join(srpm_dir, "unbound-*.src.rpm"), "ソースパッケージ")
    old_version, old_release = c.out(["rpm", "-qp", "--qf", "%{VERSION} %{RELEASE}", srpm]).split()
    dist = versions.rpm_dist(old_release) or c.out(["rpm", "--eval", "%{?dist}"]).strip()
    defines = [
        "--define", f"_topdir {TOP}",
        "--define", f"dist {dist}",
        "--define", f"vendor {opts['vendor']}",
        "--define", f"packager {opts['maintainer']}",
    ]
    c.say(f"OS のソースパッケージ: {old_version}-{old_release}（dist {dist}）")
    c.sh(["rpm", "-i", "--define", f"_topdir {TOP}", srpm])
    spec = c.one(os.path.join(TOP, "SPECS", "*.spec"), "spec ファイル")
    sources = os.path.join(TOP, "SOURCES")
    text = open(spec, encoding="utf-8").read()
    spec_license = c.out(["rpmspec", "-q", "--srpm", *defines, "--qf", "%{LICENSE}", spec]).strip()
    try:
        licenses.check_spec_license(spec_license, opts["allowed_spec_licenses"])
    except Exception as exc:
        c.fail(str(exc))
    c.say(f"spec の License: {spec_license}")
    expanded = c.out(["rpmspec", "-P", *defines, spec])
    patch_list = pkgedit.spec_patches(expanded)
    try:
        strips = [pkgedit.spec_patch_strip(expanded, text, name) for _, name in patch_list]
    except Exception as exc:
        c.fail(str(exc))
    cands = [p for p in glob.glob(os.path.join(sources, f"unbound-{old_version}.tar.*")) if not p.endswith((".asc", ".sig"))]
    old_tar = cands[0] if len(cands) == 1 else c.fail("旧版のソースが 1 つに決まりません。")
    new_tar = os.path.join(c.IN, f"unbound-{args.version}.tar.gz")
    c.extract_stripped(old_tar, "/work/tree/old")
    c.extract_stripped(new_tar, "/work/tree/new")
    try:
        decisions = patches.decide(
            [(name, os.path.join(sources, name), strip) for (_, name), strip in zip(patch_list, strips)],
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
    drop = []
    for (number, name), decision in zip(patch_list, decisions):
        c.say(patches.describe(name, decision.action, decision.reason))
        if decision.action == "drop":
            drop.append(number)
        elif decision.action == "rebased":
            shutil.copy2(decision.new_path, os.path.join(sources, name))
    text = pkgedit.spec_remove_patches(text, drop)
    text = pkgedit.spec_set_version_release(text, args.version, f"{args.revision}.local%{{?dist}}")
    try:
        text, count = pkgedit.normalize_configure(text, opts["configure"], pkgedit.RPM_CONFIGURE_ANCHOR, opts.get("configure_exceptions", []))
    except Exception as exc:
        c.fail(str(exc))
    c.say(f"configure の行 {count} か所でオプションを統一しました。")
    text = pkgedit.spec_add_changelog(
        text,
        args.version,
        f"{args.revision}.local",
        f"unbound-packager: upstream {args.version}, unified build options",
        time.strftime("%a %b %d %Y"),
        opts["maintainer"],
    )
    open(spec, "w", encoding="utf-8").write(text)
    shutil.copy2(new_tar, sources)
    shutil.copy2(new_tar + ".asc", sources)
    key = os.path.join(c.IN, "signing-key.asc")
    keyrings = pkgedit.spec_gpg_keyrings(expanded)
    if not keyrings and "gpgverify" in text:
        c.fail("spec の %{gpgverify} から、署名鍵のファイルを読み取れませんでした。")
    for name in keyrings:
        shutil.copy2(key, os.path.join(sources, name))
        c.say(f"%{{gpgverify}} が使う署名鍵 {name} を、リリース署名鍵 G2 に置き換えました。")
    for conf in glob.glob(os.path.join(sources, "*.conf")):
        before = open(conf, encoding="utf-8", errors="replace").read()
        after = pkgedit.replace_module_config(before, "ipsecmod")
        if after != before:
            c.say(f"{os.path.basename(conf)} の module-config から ipsecmod を外しました。")
        for name, value in opts.get("server_options", {}).items():
            after, count = pkgedit.set_server_option(after, name, value)
            if count:
                c.say(f"{os.path.basename(conf)} の {name} を {value} にしました。")
        if after != before:
            open(conf, "w", encoding="utf-8").write(after)
    c.sh(["dnf", "-y", "builddep", *defines, spec])
    log = os.path.join(c.OUT, "build.log")
    os.makedirs(c.OUT, exist_ok=True)
    cmd = ["rpmbuild", "-ba", *defines, "--define", "_unpackaged_files_terminate_build 0"]
    if args.jobs:
        cmd += ["--define", f"_smp_build_ncpus {args.jobs}"]
    c.sh(cmd + [spec], log=log)
    expected = set(c.out(["rpmspec", "-q", *defines, "--qf", "%{NAME}\n", spec]).split())
    expected = {n for n in expected if "debuginfo" not in n and "debugsource" not in n}
    built = {}
    for path in glob.glob(os.path.join(TOP, "RPMS", "*", "*.rpm")):
        name = c.out(["rpm", "-qp", "--qf", "%{NAME}", path]).strip()
        if "debuginfo" in name or "debugsource" in name:
            continue
        built[name] = path
    missing = sorted(expected - set(built))
    if missing:
        c.fail("サブパッケージが欠けています: " + "、".join(missing))
    unpackaged = []
    capture = False
    for line in open(log, encoding="utf-8", errors="replace"):
        if "Installed (but unpackaged) file(s) found" in line:
            capture = True
            continue
        if capture:
            if re.match(r"^\s+/", line):
                unpackaged.append(line.strip())
            else:
                capture = False
    pkg_version = c.out(["rpm", "-qp", "--qf", "%{VERSION}-%{RELEASE}", built["unbound"]]).strip()
    sources_out = glob.glob(os.path.join(TOP, "SRPMS", "*.src.rpm"))
    if len(sources_out) != 1:
        c.fail("SRPM が 1 つに決まりません。")
    c.write_manifest("rocky10", args.version, pkg_version, list(built.items()), expected, decisions, unpackaged,
                     {"distro_source": f"{old_version}-{old_release}", "licenses": [spec_license]}, sources=sources_out)


if __name__ == "__main__":
    main()
