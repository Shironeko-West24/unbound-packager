import base64
import hashlib
import json
import os
import shutil

from . import versions
from .containers import OS_NAMES
from .patches import ACTION_LABELS
from .util import UbpError, run, sha256_file

TOOL = "unbound-update"


def encode_args(data):
    return base64.urlsafe_b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii")


def tag_name(version, revision):
    versions.parse(version)
    return f"v{version}-{int(revision)}"


def parse_tag(tag):
    import re

    match = re.match(r"^v(\d+\.\d+\.\d+)-(\d+)$", tag or "")
    return (match.group(1), int(match.group(2))) if match else None


def revisions(tags, version):
    return sorted(parsed[1] for parsed in map(parse_tag, tags) if parsed and parsed[0] == version)


def assemble(code, work, version, revision):
    target = os.path.join(str(work), "release")
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target)
    combined = {"upstream": version, "revision": int(revision), "tag": tag_name(version, revision), "os": {}}
    for os_name in OS_NAMES:
        source = os.path.join(str(work), "out", os_name)
        try:
            manifest = json.load(open(os.path.join(source, "manifest.json"), encoding="utf-8"))
        except (OSError, ValueError):
            raise UbpError(f"{os_name} の成果物がそろっていないため、公開できません。")
        report_path = os.path.join(source, "test-report.json")
        if not os.path.exists(report_path) or not json.load(open(report_path, encoding="utf-8")).get("ok"):
            raise UbpError(f"{os_name} のテストに合格していないため、公開できません。")
        if manifest.get("upstream") != version:
            raise UbpError(f"{os_name} の成果物の版が一致しません。")
        for item in manifest["files"] + manifest.get("sources", []):
            path = os.path.join(source, item["file"])
            if sha256_file(path) != item["sha256"]:
                raise UbpError(f"成果物の SHA-256 が一致しません: {item['file']}")
            if os.path.exists(os.path.join(target, item["file"])):
                raise UbpError(f"ファイル名が重複しています: {item['file']}")
            shutil.copy2(path, os.path.join(target, item["file"]))
        combined["os"][os_name] = {
            key: manifest.get(key, [])
            for key in ("pkg_version", "files", "all_packages", "patches", "unpackaged", "sources", "licenses", "distro_source")
        }
    shutil.copy2(os.path.join(str(code), "server", TOOL), os.path.join(target, TOOL))
    shutil.copy2(os.path.join(str(code), "NOTICE.md"), os.path.join(target, "NOTICE.md"))
    combined["tool"] = {"file": TOOL, "sha256": sha256_file(os.path.join(target, TOOL))}
    with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(combined, handle, ensure_ascii=False, indent=1)
    lines = []
    for name in sorted(os.listdir(target)):
        lines.append(f"{sha256_file(os.path.join(target, name))}  {name}\n")
    with open(os.path.join(target, "SHA256SUMS"), "w", encoding="utf-8") as handle:
        handle.writelines(lines)
    return target, combined


def summary(combined):
    lines = []
    unpackaged = []
    for os_name, entry in combined["os"].items():
        counts = {}
        for patch in entry["patches"]:
            counts[patch["action"]] = counts.get(patch["action"], 0) + 1
        patch_text = f"採用 {counts.get('adopt', 0)}、当て直し {counts.get('rebased', 0)}、除外 {counts.get('drop', 0)}"
        lines.append(f"{os_name}: {entry['pkg_version']}（パッケージ {len(entry['files'])} 個、パッチ {patch_text}）")
        unpackaged += [f"{os_name}: {item}" for item in entry["unpackaged"]]
    return "\n".join(lines), unpackaged


def notes(combined, repository):
    text, unpackaged = summary(combined)
    out = [
        f"Unbound {combined['upstream']} の非公式ビルドです（ビルド {combined['revision']}）。",
        "",
        "NLnet Labs、Rocky Enterprise Software Foundation、Red Hat、Canonical、Debian のいずれとも関係のない、個人によるビルドです。",
        "出どころとライセンスは NOTICE.md にあります。ソースパッケージ（SRPM と deb のソース一式）も、このリリースに含まれています。",
        "",
        "## 内容",
        "",
        "```",
        text,
        "```",
        "",
        "## パッチの判定",
        "",
    ]
    for os_name, entry in combined["os"].items():
        out.append(f"### {os_name}（OS のソースパッケージ: {entry.get('distro_source') or '不明'}）")
        out.append("")
        if not entry["patches"]:
            out.append("パッチはありません。")
        else:
            out += ["| パッチ | 判定 | 理由 |", "| --- | --- | --- |"]
        for patch in entry["patches"]:
            out.append(f"| `{patch['name']}` | {ACTION_LABELS.get(patch['action'], patch['action'])} | {patch['reason']} |")
        out.append("")
    if unpackaged:
        out += ["## パッケージに入っていないファイル", "", "```", *unpackaged, "```", ""]
    out += [
        "## 更新のしかた",
        "",
        "```",
        f"sudo unbound-update update --version {combined['upstream']}",
        "```",
        "",
        f"ファイルの SHA-256 は `SHA256SUMS` にあります。使い方は https://github.com/{repository}/blob/main/docs/usage.md を見てください。",
    ]
    return "\n".join(out) + "\n"


def publish(target, combined, repository, sha, gh=run):
    if not os.path.isfile(os.path.join(target, "SHA256SUMS.sig")):
        raise UbpError("署名（SHA256SUMS.sig）がないため、公開できません。")
    tag = combined["tag"]
    notes_path = os.path.join(os.path.dirname(target), "release-notes.md")
    with open(notes_path, "w", encoding="utf-8") as handle:
        handle.write(notes(combined, repository))
    files = [os.path.join(target, name) for name in sorted(os.listdir(target))]
    gh([
        "gh", "release", "create", tag, "--repo", repository, "--target", sha,
        "--title", f"Unbound {combined['upstream']}（ビルド {combined['revision']}）",
        "--notes-file", notes_path, *files,
    ])
    return tag
